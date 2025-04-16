from langgraph.graph import StateGraph, START, END
from nodes import data_splitter, event_classifier

from typing import Optional, List, TypedDict

class EventExtractionState(TypedDict):
    topic: str
    chunk: str
    events: Optional[dict]


def create_app():
    graph = StateGraph(state_schema='EventExtractionState')

    # tmp
    graph.set_entry_point("extract_events")


    # # Stage -1: Splitting articles into chunks
    # graph.add_node("split_articles_to_chunks", data_splitter.main)
    # graph.set_entry_point("split_articles_to_chunks")

    # Stage 0: Extracting events from chunks
    graph.add_node("extract_events", event_classifier.extract_events_node)
    # graph.add_edge("split_articles_to_chunks", "extract_events")  
    
    graph.set_finish_point("extract_events")

    return graph.compile()

