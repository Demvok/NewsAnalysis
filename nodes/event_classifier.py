import json

from pydantic import BaseModel, Field
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

def _validate_event_lengths(event):
    """Validate the lengths of fields in the event."""
    if event.get("general_event"):
        if len(event["general_event"].get("title", "")) > 50 or len(event["general_event"].get("description", "")) > 200:
            return False
    if event.get("person_event"):
        if len(event["person_event"].get("person_name", "")) > 150 or len(event["person_event"].get("citation", "")) > 300:
            return False
    return True

def _parse_event_output(response: str):
    events = {"general_event": None, "person_event": None}

    try:
        # Parse the JSON response
        jsoned = json.loads(response.strip('```json').strip())

        # Validate lengths
        if not _validate_event_lengths(jsoned):
            raise ValueError("LLM output does not meet length constraints.")

        # Handle general_event only if it exists and is not None
        if jsoned.get('general_event') is not None:
            events['general_event'] = GeneralEvent(**jsoned['general_event'])

        # Handle person_event only if it exists and is not None
        if jsoned.get('person_event') is not None:
            events['person_event'] = PersonEvent(**jsoned['person_event'])

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

    logger.debug(f"Chunk {state['chunk_id']} processed, ({len(state['general_event'])}) general events, ({len(state['person_event'])}) opinions.")
    return state