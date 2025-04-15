import os
import json
from database.DBConnector import *

from pydantic import BaseModel, Field
from typing import Optional, List, TypedDict
from langchain.output_parsers import PydanticOutputParser
from langchain.prompts import PromptTemplate

from model import llm_invoke

import warnings
warnings.filterwarnings("ignore")


# 
# EQUALS TO STAGE 0
# 


class GeneralEvent(BaseModel):
    fk_origin_article_id: str = Field(description="Unique identifier for the origin article.")
    description: str = Field(description="Brief summary of the event, limited to 200 characters.", max_length=200)

class PersonEvent(BaseModel):
    fk_origin_article_id: str = Field(description="Unique identifier for the origin article.")
    citation: str = Field(description="Key statement or citation related to the person and the topic.", max_length=300)

class Person(BaseModel):
    name: str = Field(description="Name of the person involved in the event.")

class EventClassification(BaseModel):
    general_event: Optional[GeneralEvent]
    person_event: Optional[PersonEvent]

class EventExtractionState(TypedDict):
    topic: str
    chunk: str
    events: Optional[dict]


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
        "summary": "..."
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



def extract_events_node(state):
    topic = state["topic"]
    chunk = state["chunk"]

    formatted_prompt = prompt.format(topic=topic, chunk=chunk)
    response = llm_invoke(formatted_prompt)

    print("----- PROMPT -----")
    print(formatted_prompt)

    parsed = parse_event_output(response.content)
    print("----- RESPONSE -----")
    print(response)

    return {"events": parsed}

# def parse_event_output(response: str):
#     events = {"general_event": None, "person_event": None}

#     # Виділення інформації з тексту (відокремлені блоки General Event і Person Event)
#     try:

#         general_event_part = response.split("Person Event:")[0].strip()
#         person_event_part = response.split("Person Event:")[1].strip()

#         # Пошук в JSON-форматі для General Event
#         general_event_json = json.loads(general_event_part.replace("General Event:", "").strip())
#         events["general_event"] = GeneralEvent(**general_event_json)

#         # Пошук в JSON-форматі для Person Event
#         person_event_json = json.loads(person_event_part.replace("Person Event:", "").strip())
#         events["person_event"] = PersonEvent(**person_event_json)

#     except Exception as e:
#         print(f"Error while parsing response: {e}")
    
#     return events


def parse_event_output(response: str):
    events = {"general_event": None, "person_event": None}

    # Виділення інформації з тексту (відокремлені блоки General Event і Person Event)
    try:
        jsoned = json.loads(response.strip('```json').strip())
        events['general_event'] = jsoned['general_event']
        events['person_event'] = jsoned['person_event']

    except Exception as e:
        print(f"Error while parsing response: {e}")
    
    return events


def main():
   
    article_id = get_article_chunk(78)["fk_article_id"]
    topic_id = get_article(article_id)["fk_topic_id"]
    topic = get_topic(topic_id)["topic_name"]

    return extract_events_node({"topic": topic, "chunk": t_get_article_chunk_content(78)})