from config import FIELD_LENGTH_POLICY
import json
import time
import utils.logger as log

from pydantic import ValidationError
from graph.states_setup import GeneralEvent, PersonEvent, EventClassification, ChunkState

from langchain.output_parsers import PydanticOutputParser
from langchain.prompts import PromptTemplate

from graph.model import llm_invoke
from database.DBConnector import get_article_chunk

logger = log.setup_logger(name="event_classifier", log_file="graph.log")

import warnings
warnings.filterwarnings("ignore")


# 
# EQUALS TO STAGE 0
# 


parser = PydanticOutputParser(pydantic_object=EventClassification)

prompt = PromptTemplate.from_template(
    """You are an expert journalist assistant. Your task is to extract:

    1. General events related to the topic "{topic}" from the article.
    2. Person events related to someone’s statement or action regarding "{topic}".

    Ensure the following constraints:
    - The "title" of the general event must not exceed 50 characters.
    - The "description" of the general event must not exceed 200 characters.
    - The "person_name" must not exceed 150 characters.
    - The "citation" must not exceed 300 characters.

    Without superfluous information, just the most important details.

    Return a JSON object in the following format:

    {{
    "general_event": {{
        "title": "...",
        "description": "..."
    }},
    "person_event": {{
        "person_name": "...",
        "citation": "..."
    }}
    }}

    If no event is found, use `null`.

    Article:
    {chunk}
    """
).partial(format_instructions=parser.get_format_instructions())

#
#   Different policies for field length handling
#

def _validate_event_lengths(event):
    """Validate the lengths of fields in the event."""
    if event.get("general_event"):
        if len(event["general_event"].get("title", "")) > 50 or len(event["general_event"].get("description", "")) > 200:
            return False
    if event.get("person_event"):
        if len(event["person_event"].get("person_name", "")) > 150 or len(event["person_event"].get("citation", "")) > 300:
            return False
    return True

def _truncate_event_fields(event):
    """Truncate fields to their maximum allowed lengths."""
    if event.get("general_event"):
        event["general_event"]["title"] = event["general_event"].get("title", "")[:50]
        event["general_event"]["description"] = event["general_event"].get("description", "")[:200]
    if event.get("person_event"):
        event["person_event"]["person_name"] = event["person_event"].get("person_name", "")[:150]
        event["person_event"]["citation"] = event["person_event"].get("citation", "")[:300]
    return event

def _refine_event_field(field_name, field_value, max_length):
    """Refine a single field using the LLM to fit within the maximum length."""
    refinement_prompt = f"Refine the following text to fit within {max_length} characters:\n\n{field_value}"
    response = llm_invoke(refinement_prompt)
    return response.content[:max_length]

def _refine_event_fields(event):
    """Refine fields that exceed their maximum allowed lengths."""
    if event.get("general_event"):
        if len(event["general_event"].get("title", "")) > 50:
            event["general_event"]["title"] = _refine_event_field("title", event["general_event"]["title"], 50)
        if len(event["general_event"].get("description", "")) > 200:
            event["general_event"]["description"] = _refine_event_field("description", event["general_event"]["description"], 200)
    if event.get("person_event"):
        if len(event["person_event"].get("person_name", "")) > 150:
            event["person_event"]["person_name"] = _refine_event_field("person_name", event["person_event"]["person_name"], 150)
        if len(event["person_event"].get("citation", "")) > 300:
            event["person_event"]["citation"] = _refine_event_field("citation", event["person_event"]["citation"], 300)
    return event


#
#   
#


def _parse_event_output(response: str):
    """Parse the LLM response and return events."""
    start_time = time.time()  # Start timing the parsing process
    events = {"general_event": None, "person_event": None}

    try:
        # Parse the JSON response
        jsoned = json.loads(response.strip('```json').strip())

        if FIELD_LENGTH_POLICY == "IGNORE":
            # Skip validation and directly parse
            events['general_event'] = (
                GeneralEvent(**jsoned['general_event']) if jsoned.get('general_event') else None
            )
            events['person_event'] = (
                PersonEvent(**jsoned['person_event']) if jsoned.get('person_event') else None
            )

        elif FIELD_LENGTH_POLICY == "RETRY":            
            # Validate lengths and raise errors if invalid
            if not _validate_event_lengths(jsoned):
                raise ValueError("LLM output does not meet length constraints.")
            events['general_event'] = (
                GeneralEvent(**jsoned['general_event']) if jsoned.get('general_event') else None
            )
            events['person_event'] = (
                PersonEvent(**jsoned['person_event']) if jsoned.get('person_event') else None
            )

        elif FIELD_LENGTH_POLICY == "REFINE":
            # Attempt to refine fields if validation fails
            try:
                events['general_event'] = (
                    GeneralEvent(**jsoned['general_event']) if jsoned.get('general_event') else None
                )
                events['person_event'] = (
                    PersonEvent(**jsoned['person_event']) if jsoned.get('person_event') else None
                )
            except ValidationError:
                jsoned = _refine_event_fields(jsoned)
                events['general_event'] = (
                    GeneralEvent(**jsoned['general_event']) if jsoned.get('general_event') else None
                )
                events['person_event'] = (
                    PersonEvent(**jsoned['person_event']) if jsoned.get('person_event') else None
                )

        elif FIELD_LENGTH_POLICY == "TRUNCATE":
            # Truncate fields if validation fails
            try:
                events['general_event'] = (
                    GeneralEvent(**jsoned['general_event']) if jsoned.get('general_event') else None
                )
                events['person_event'] = (
                    PersonEvent(**jsoned['person_event']) if jsoned.get('person_event') else None
                )
            except ValidationError:
                jsoned = _truncate_event_fields(jsoned)
                events['general_event'] = (
                    GeneralEvent(**jsoned['general_event']) if jsoned.get('general_event') else None
                )
                events['person_event'] = (
                    PersonEvent(**jsoned['person_event']) if jsoned.get('person_event') else None
                )

    except json.JSONDecodeError:
        logger.warning("Received invalid JSON response. Returning empty events.")
    except Exception as e:
        logger.warning(f"Error while parsing response: {e}")
        raise e  # Re-raise the exception to trigger re-invocation if needed

    end_time = time.time()  # End timing for parsing
    logger.debug(
        "Finished parsing event output.",
        extra={"execution_time": log.timeUsed(start_time, end_time)}
    )
    return events

def extract_events(topic, content, max_retries=3):
    """Extract events with retry logic for invalid outputs."""
    retries = 0
    start_time = time.time()  # Start timing the extraction process

    # First attempt
    formatted_prompt = prompt.format(topic=topic, chunk=content)
    response = llm_invoke(formatted_prompt)

    try:
        parsed = _parse_event_output(response.content)
        if parsed['general_event'] is None and parsed['person_event'] is None:
            # Skip chunk if both events are None
            logger.warning(
                f"Skipping chunk due to None output on the first attempt.",
                extra={"execution_time": log.timeUsed(start_time, time.time())}
            )
            return None
        end_time = time.time()  # End timing
        logger.debug(
            f"Successfully extracted events from chunk",
            extra={"execution_time": log.timeUsed(start_time, end_time)}
        )
        return parsed  # Return if parsing and validation succeed
    except Exception as e:
        logger.warning(
            f"Error during first attempt: {e}",
            extra={"execution_time": log.timeUsed(start_time, time.time())}
        )

    # Retry logic for other policies (if applicable)
    while retries < max_retries - 1:  # Subtract 1 because the first attempt is already done
        if FIELD_LENGTH_POLICY == "IGNORE":
            break  # Skip retries for IGNORE mode
                
        retries += 1
        response = llm_invoke(formatted_prompt)

        try:
            parsed = _parse_event_output(response.content)
            end_time = time.time()  # End timing
            logger.info(
                f"Successfully extracted events after {retries} retries.",
                extra={"execution_time": log.timeUsed(start_time, end_time)}
            )
            return parsed  # Return if parsing and validation succeed
        except Exception as e:
            logger.warning(
                f"Retry {retries}/{max_retries} due to invalid output: {e}",
                extra={"execution_time": log.timeUsed(start_time, time.time())}
            )

    end_time = time.time()  # End timing after retries
    logger.error(
        f"Skipping extraction after {max_retries} retries.",
        extra={"execution_time": log.timeUsed(start_time, end_time)}
    )
    return None  # Skip the current chunk if retries fail

def main(state: ChunkState):    
    chunk_id = state.chunk_id
    topic = state.topic
    content = state.content
    state.origin_article_id = get_article_chunk(chunk_id)['fk_article_id']
    logger.info(f"Processing chunk {chunk_id} started")

    start_time = time.time()  # Start timing the main process
    # Extract events
    extracted = extract_events(topic, content)

    if extracted is None:
        end_time = time.time()  # End timing for skipped chunk
        logger.warning(
            f"Skipping chunk {state.chunk_id} due to None output or repeated failures.",
            extra={"execution_time": log.timeUsed(start_time, end_time)}
        )
        return state  # Skip updating state if extraction fails

    # Ensure general_event and person_event are initialized as lists
    if state.general_event is None:
        state.general_event = []  # Initialize as an empty list if None
    if state.person_event is None:
        state.person_event = []  # Initialize as an empty list if None

    # Update the state with extracted events
    if extracted['general_event'] is not None:
        # Convert the Pydantic model to a dictionary before appending
        state.general_event.append(extracted['general_event'].dict())

    if extracted['person_event'] is not None:
        # Convert the Pydantic model to a dictionary before appending
        state.person_event.append(extracted['person_event'].dict())

    end_time = time.time()  # End timing for successful processing
    logger.info(
        f"Chunk {state.chunk_id} processed, ({len(state.general_event)}) general events, ({len(state.person_event)}) opinions.",
        extra={"execution_time": log.timeUsed(start_time, end_time)}
    )
    return state