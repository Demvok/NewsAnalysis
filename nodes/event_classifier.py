import os
import json
from database.DBConnector import *

from pydantic import BaseModel, Field
from typing import Optional, List, TypedDict
from langchain.output_parsers import PydanticOutputParser
from langchain.prompts import PromptTemplate

from graph.model import llm_invoke

import warnings
warnings.filterwarnings("ignore")


# 
# EQUALS TO STAGE 0
# 

class EventExtractionState(TypedDict):
    topic: str
    chunk: str
    events: Optional[dict]


class GeneralEvent(BaseModel):
    title: str = Field(description="Title of the general event.")
    description: str = Field(description="Brief summary of the event, limited to 200 characters.")

class PersonEvent(BaseModel):
    person_name: str = Field(description="Name of the person involved in the event.")
    citation: str = Field(description="Key statement or citation related to the person and the topic.")

class EventClassification(BaseModel):
    general_event: Optional[GeneralEvent]
    person_event: Optional[PersonEvent]

parser = PydanticOutputParser(pydantic_object=EventClassification)

prompt = PromptTemplate.from_template(
    """You are an expert journalist assistant. Your task is to extract:

    1. General events related to the topic "{topic}" from the article.
    2. Person events related to someone’s statement or action regarding "{topic}".

    Whitout superfluous information, just the most important details.

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



def extract_events(topic, content):

    formatted_prompt = prompt.format(topic=topic, chunk=content)
    response = llm_invoke(formatted_prompt)

    parsed = _parse_event_output(response.content)

    return parsed

def _parse_event_output(response: str):
    events = {"general_event": None, "person_event": None}

    try:
        # Parse the JSON response
        jsoned = json.loads(response.strip('```json').strip())

        # Handle general_event only if it exists and is not None
        if jsoned.get('general_event') is not None:
            events['general_event'] = GeneralEvent(**jsoned['general_event'])

        # Handle person_event only if it exists and is not None
        if jsoned.get('person_event') is not None:
            events['person_event'] = PersonEvent(**jsoned['person_event'])

    except Exception as e:
        print(f"Error while parsing response: {e}")
    
    return events


def main(state):
    chunk_id = state['chunk_id']
    topic = state['topic']
    content = state['content']

    return extract_events(topic, content)