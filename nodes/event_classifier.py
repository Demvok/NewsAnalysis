import os
import json

from pydantic import BaseModel, Field, ValidationError
from typing import Optional
from langchain.output_parsers import PydanticOutputParser
from langchain.prompts import PromptTemplate

from graph.model import llm_invoke

from utils.logger import setup_logger
logger = setup_logger(name="event_classifier", log_file="graph.log")

import warnings
warnings.filterwarnings("ignore")


# 
# EQUALS TO STAGE 0
# 


class GeneralEvent(BaseModel):
    title: str = Field(description="Title of the general event.", max_length=50)
    description: str = Field(description="Brief summary of the event, limited to 200 characters.", max_length=200)

class PersonEvent(BaseModel):
    person_name: str = Field(description="Name of the person involved in the event.", max_length=150)
    citation: str = Field(description="Key statement or citation related to the person and the topic.", max_length=300)

class EventClassification(BaseModel):
    general_event: Optional[GeneralEvent]
    person_event: Optional[PersonEvent]

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

FIELD_LENGTH_POLICY = os.getenv("FIELD_LENGTH_POLICY").upper()

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
        event["general_event"]["title"] = event["general_event"]["title"][:50]
        event["general_event"]["description"] = event["general_event"]["description"][:200]
    if event.get("person_event"):
        event["person_event"]["person_name"] = event["person_event"]["person_name"][:150]
        event["person_event"]["citation"] = event["person_event"]["citation"][:300]
    return event

def _refine_event_field(field_name, field_value, max_length):
    """Refine a single field using the LLM to fit within the maximum length."""
    refinement_prompt = f"Refine the following text to fit within {max_length} characters:\n\n{field_value}"
    response = llm_invoke(refinement_prompt)
    return response.content[:max_length]

def _refine_event_fields(event):
    """Refine fields that exceed their maximum allowed lengths."""
    if event.get("general_event"):
        if len(event["general_event"]["title"]) > 50:
            event["general_event"]["title"] = _refine_event_field("title", event["general_event"]["title"], 50)
        if len(event["general_event"]["description"]) > 200:
            event["general_event"]["description"] = _refine_event_field("description", event["general_event"]["description"], 200)
    if event.get("person_event"):
        if len(event["person_event"]["person_name"]) > 150:
            event["person_event"]["person_name"] = _refine_event_field("person_name", event["person_event"]["person_name"], 150)
        if len(event["person_event"]["citation"]) > 300:
            event["person_event"]["citation"] = _refine_event_field("citation", event["person_event"]["citation"], 300)
    return event

def _parse_event_output(response: str):
    events = {"general_event": None, "person_event": None}

    try:
        # Parse the JSON response
        jsoned = json.loads(response.strip('```json').strip())

        if FIELD_LENGTH_POLICY == "IGNORE":
            logger.debug("Ignoring field length policy")
            # Skip validation and directly parse
            events['general_event'] = GeneralEvent(**jsoned['general_event']) if jsoned.get('general_event') else None
            events['person_event'] = PersonEvent(**jsoned['person_event']) if jsoned.get('person_event') else None

        elif FIELD_LENGTH_POLICY == "RETRY":
            logger.debug("Retrying field length policy")
            # Validate lengths and raise errors if invalid
            if not _validate_event_lengths(jsoned):
                raise ValueError("LLM output does not meet length constraints.")
            events['general_event'] = GeneralEvent(**jsoned['general_event']) if jsoned.get('general_event') else None
            events['person_event'] = PersonEvent(**jsoned['person_event']) if jsoned.get('person_event') else None

        elif FIELD_LENGTH_POLICY == "REFINE":
            logger.debug("Refining field length policy")
            # Attempt to refine fields if validation fails
            try:
                events['general_event'] = GeneralEvent(**jsoned['general_event']) if jsoned.get('general_event') else None
                events['person_event'] = PersonEvent(**jsoned['person_event']) if jsoned.get('person_event') else None
            except ValidationError:
                jsoned = _refine_event_fields(jsoned)
                events['general_event'] = GeneralEvent(**jsoned['general_event']) if jsoned.get('general_event') else None
                events['person_event'] = PersonEvent(**jsoned['person_event']) if jsoned.get('person_event') else None

        elif FIELD_LENGTH_POLICY == "TRUNCATE":
            logger.debug("Truncating field length policy")
            # Truncate fields if validation fails
            try:
                events['general_event'] = GeneralEvent(**jsoned['general_event']) if jsoned.get('general_event') else None
                events['person_event'] = PersonEvent(**jsoned['person_event']) if jsoned.get('person_event') else None
            except ValidationError:
                jsoned = _truncate_event_fields(jsoned)
                events['general_event'] = GeneralEvent(**jsoned['general_event']) if jsoned.get('general_event') else None
                events['person_event'] = PersonEvent(**jsoned['person_event']) if jsoned.get('person_event') else None

    except Exception as e:
        print(f"Error while parsing response: {e}")
        raise e  # Re-raise the exception to trigger re-invocation
    
    return events

def extract_events(topic, content, max_retries=3):
    """Extract events with retry logic for invalid outputs."""
    retries = 0
    while retries < max_retries:
        formatted_prompt = prompt.format(topic=topic, chunk=content)
        response = llm_invoke(formatted_prompt)

        try:
            parsed = _parse_event_output(response.content)
            return parsed  # Return if parsing and validation succeed
        except Exception as e:
            logger.warning(f"Retry {retries + 1}/{max_retries} due to invalid output: {e}")
            retries += 1

        if FIELD_LENGTH_POLICY == "IGNORE":
            break  # Skip retries for IGNORE mode

    raise ValueError("Failed to extract valid events after multiple retries.")

def main(state):
    logger.info(f"Processing chunk {state['chunk_id']} started")
    topic = state['topic']
    content = state['content']

    # Extract events
    extracted = extract_events(topic, content)

    # Update the state with extracted events
    state['general_event'] = state.get('general_event', [])
    state['person_event'] = state.get('person_event', [])

    if extracted['general_event'] is not None:
        state['general_event'].append(extracted['general_event'].dict())  # Convert Pydantic model to dict

    if extracted['person_event'] is not None:
        state['person_event'].append(extracted['person_event'].dict())  # Convert Pydantic model to dict

    logger.info(f"Chunk {state['chunk_id']} processed, ({len(state['general_event'])}) general events, ({len(state['person_event'])}) opinions.")
    return state