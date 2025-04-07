from langchain_huggingface import HuggingFaceEndpoint
import warnings
import os
from database.DBConnector import get_article_chunk, t_get_article_chunk_content, get_article, get_topic


from pydantic import BaseModel, Field
from typing import Optional, List, TypedDict
from langchain.output_parsers import PydanticOutputParser
from langchain.prompts import PromptTemplate
from langgraph.graph import StateGraph, END
import json
warnings.filterwarnings("ignore")


HF_API_TOKEN = os.getenv('HUGGINGFACE_API_KEY')


class GeneralEvent(BaseModel):
    title: str = Field(description="Title of the article.")
    date: str = Field(description="Date of the event.")
    article_id: str = Field(description="Unique identifier for the article.")
    summary: str = Field(description="Brief summary of the event, limited to 140 characters.")


class PersonEvent(BaseModel):
    article_id: str = Field(description="Unique identifier for the article.")
    name: str = Field(description="Name of the person involved.")
    date: str = Field(description="Date of the event.")
    citation: str = Field(description="Key statement or citation related to the person and the topic.")


class EventClassification(BaseModel):
    general_event: Optional[GeneralEvent]
    person_event: Optional[PersonEvent]


class EventExtractionState(TypedDict):
    topic: str
    chunk: str
    events: Optional[dict]


def parse_event_output(response: str):
    events = {"general_event": None, "person_event": None}

    # Виділення інформації з тексту (відокремлені блоки General Event і Person Event)
    try:

        general_event_part = response.split("Person Event:")[0].strip()
        person_event_part = response.split("Person Event:")[1].strip()

        # Пошук в JSON-форматі для General Event
        general_event_json = json.loads(general_event_part.replace("General Event:", "").strip())
        events["general_event"] = GeneralEvent(**general_event_json)

        # Пошук в JSON-форматі для Person Event
        person_event_json = json.loads(person_event_part.replace("Person Event:", "").strip())
        events["person_event"] = PersonEvent(**person_event_json)

    except Exception as e:
        print(f"Error while parsing response: {e}")
    
    return events



# Setup Hugging Face model
llm = HuggingFaceEndpoint(
    repo_id='tiiuae/falcon-7b-instruct',
    huggingfacehub_api_token=HF_API_TOKEN,
    task='text-generation'
)


parser = PydanticOutputParser(pydantic_object=EventClassification)



prompt = PromptTemplate.from_template(
    """You are an expert journalist assistant. Your task is to extract:

    1. A general event related to the topic "{topic}" from the article.
    2. A person event related to someone’s statement or action regarding "{topic}".

    Whitout superfluous information, just the most important details.

    Return a JSON object in the following format:

    {{
    "general_event": {{
        "title": "...",
        "date": "...",
        "article_id": "...",
        "summary": "..."
    }},
    "person_event": {{
        "article_id": "...",
        "name": "...",
        "date": "...",
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
    response = llm.invoke(formatted_prompt)

    print("----- PROMPT -----")
    print(formatted_prompt)
    print("----- RESPONSE -----")
    print(response)

    parsed = parse_event_output(response)
    return {"events": parsed}



def main():

    graph = StateGraph(state_schema=EventExtractionState)
    graph.add_node("extract_events", extract_events_node)
    graph.set_entry_point("extract_events")
    graph.set_finish_point("extract_events")
    app = graph.compile()



    article_id = get_article_chunk(358)["fk_article_id"]
    topic_id = get_article(article_id)["fk_topic_id"]
    topic = get_topic(topic_id)["topic_name"]


    result = app.invoke({"topic": topic, "chunk": t_get_article_chunk_content(358)})
    print(result["events"])


