from config import FIELD_LENGTH_POLICY
import json
import time
import re
import utils.logger as log

from pydantic import ValidationError
from graph.states_setup import GeneralEvent, PersonEvent, EventClassification, ChunkState

from langchain.output_parsers import PydanticOutputParser
from langchain.prompts import PromptTemplate

from graph.model import llm_invoke
from graph.prompts import EVENT_CLASSIFICATION_PROMPT, REFINING_PROMPT

logger = log.setup_logger(name="event_classifier", log_file="graph.log")

import warnings
warnings.filterwarnings("ignore")


# 
# EQUALS TO STAGE 0
# 


parser = PydanticOutputParser(pydantic_object=EventClassification)

prompt = PromptTemplate.from_template(EVENT_CLASSIFICATION_PROMPT).partial(format_instructions=parser.get_format_instructions())

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
    response = llm_invoke(REFINING_PROMPT.format(max_length=max_length, field_value=field_value), usage_type='refine')
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
        # First, attempt to extract from code blocks
        content = response
        if "```" in response:
            # Find content between code block markers using regex
            pattern = r'```(?:json)?\s*([\s\S]*?)```'
            matches = re.search(pattern, response)
            if matches:
                content = matches.group(1).strip()
            
        # Clean up any remaining whitespace or special characters
        content = content.strip()
        
        # Parse the JSON response
        jsoned = json.loads(content)
        # Apply field length policy to the JSON
        return _apply_field_length_policy(jsoned)

    except json.JSONDecodeError as e:
        logger.warning(f"Received invalid JSON response: {str(e)}. Attempting to fix...")
        
        # Special case for code blocks with backticks - try another extraction method
        if "```json" in response:
            try:
                # Extract just the JSON part from the code block
                start_idx = response.find("```json") + 7
                end_idx = response.find("```", start_idx)
                if end_idx > start_idx:
                    clean_json = response[start_idx:end_idx].strip()
                    try:
                        jsoned = json.loads(clean_json)
                        logger.info("Successfully extracted JSON from code block using substring method")
                        return _apply_field_length_policy(jsoned)
                    except json.JSONDecodeError:
                        logger.warning("Failed parsing code block content with substring method")
                        # Continue with other methods
            except Exception as code_block_error:
                logger.warning(f"Error processing code block: {code_block_error}")
        
        # Direct pattern extraction for field-by-field approach
        try:
            # Extract each field directly using regex
            general_title = re.search(r'"title":\s*"([^"]*)"', content)
            general_desc = re.search(r'"description":\s*"([^"]*)"', content)
            person_name = re.search(r'"person_name":\s*"([^"]*)"', content)
            person_citation = re.search(r'"citation":\s*"([^"]*)"', content)
            
            # Build a new clean JSON string
            json_parts = []
            
            if general_title or general_desc:
                general_parts = []
                if general_title:
                    general_parts.append(f'"title": "{general_title.group(1)}"')
                if general_desc:
                    general_parts.append(f'"description": "{general_desc.group(1)}"')
                json_parts.append(f'"general_event": {{{", ".join(general_parts)}}}')
            
            if person_name or person_citation:
                person_parts = []
                if person_name:
                    person_parts.append(f'"person_name": "{person_name.group(1)}"')
                if person_citation:
                    person_parts.append(f'"citation": "{person_citation.group(1)}"')
                json_parts.append(f'"person_event": {{{", ".join(person_parts)}}}')
            
            clean_json = f'{{{", ".join(json_parts)}}}'
            
            # Attempt to parse the cleaned JSON
            jsoned = json.loads(clean_json)
            logger.info("Successfully reconstructed JSON with regex field extraction")
            return _apply_field_length_policy(jsoned)
        except Exception as regex_error:
            logger.warning(f"Failed to extract with regex: {str(regex_error)}")
        
        # If we still can't parse it, try a manual approach with the specific structure we've seen in logs
        try:
            # Try to manually extract the structure from the logs
            lines = content.strip().split('\n')
            
            # Initialize our extracted data
            extracted = {"general_event": {}, "person_event": {}}
            current_section = None
            
            for line in lines:
                line = line.strip()
                
                if '"general_event"' in line:
                    current_section = "general_event"
                elif '"person_event"' in line:
                    current_section = "person_event"
                elif '"title"' in line and current_section == "general_event":
                    title = line.split(':', 1)[1].strip().strip('",')
                    extracted["general_event"]["title"] = title.strip('"')
                elif '"description"' in line and current_section == "general_event":
                    desc = line.split(':', 1)[1].strip().strip('",')
                    extracted["general_event"]["description"] = desc.strip('"')
                elif '"person_name"' in line and current_section == "person_event":
                    name = line.split(':', 1)[1].strip().strip('",')
                    extracted["person_event"]["person_name"] = name.strip('"')
                elif '"citation"' in line and current_section == "person_event":
                    cite = line.split(':', 1)[1].strip().strip('",')
                    extracted["person_event"]["citation"] = cite.strip('"')
            
            # Clean up the extracted data
            if not extracted["general_event"]:
                extracted["general_event"] = None
            if not extracted["person_event"]:
                extracted["person_event"] = None
                
            # Apply field length policies
            return _apply_field_length_policy(extracted)
        except Exception as manual_error:
            logger.warning(f"Manual extraction failed: {str(manual_error)}")

    except Exception as e:
        logger.warning(f"Error while parsing response: {str(e)}")

    end_time = time.time()  # End timing for parsing
    logger.debug(
        "Finished parsing event output.",
        extra={"execution_time": log.timeUsed(start_time, end_time)}
    )
    return events

def _apply_field_length_policy(jsoned):
    """Apply the configured field length policy to parsed JSON data."""
    events = {"general_event": None, "person_event": None}
    
    try:
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
    except Exception as e:
        logger.warning(f"Error applying field length policy: {str(e)}")
    
    return events

def extract_events(topic, content, max_retries=3):
    """Extract events with retry logic for invalid outputs."""
    retries = 0
    start_time = time.time()  # Start timing the extraction process

    # First attempt
    formatted_prompt = prompt.format(topic=topic, chunk=content)
    response = llm_invoke(formatted_prompt, usage_type='event_extraction')

    parsed = _parse_event_output(response.content)
    # It's okay if both events are None - this is valid and shouldn't be treated as an error
    if parsed['general_event'] is None and parsed['person_event'] is None:
        logger.info(
            f"No events found in chunk on first attempt, which is okay.",
            extra={"execution_time": log.timeUsed(start_time, time.time())}
        )
        return parsed  # Return empty events structure, not None
    
    end_time = time.time()
    logger.debug(
        f"Successfully extracted events from chunk",
        extra={"execution_time": log.timeUsed(start_time, end_time)}
    )
    return parsed

def main(state: ChunkState):
    logger.info(f"(Stage 0) Processing chunk {state.chunk_id} started")

    chunk_id = state.chunk_id
    topic = state.topic
    content = state.content

    origin_article_id = state.origin_article_id

    if origin_article_id is None:
        from database.DBConnector import get_article_chunk
        state.origin_article_id = get_article_chunk(chunk_id)['fk_article_id']
        origin_article_id = state.origin_article_id

    article_date = state.article_date
    if article_date is None:
        from database.DBConnector import get_article
        state.article_date = get_article(origin_article_id).loc['article_date']
        article_date = state.article_date
    
    logger.debug('ChunkState context checked valid')
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