from langgraph.graph import StateGraph, START, END
from nodes import  write_event, event_classifier, sentiment_analysis, merge_events, blank

from typing import Optional, List, TypedDict

class ChunkState(TypedDict, total=False):
    chunk_id: int
    topic: str
    origin_article_id:int = None
    content: str
    general_event: Optional[List[dict]] = None
    person_event: Optional[List[dict]] = None
    is_processed: int = 0


def create_app():
    graph = StateGraph(state_schema=ChunkState)

    graph.add_node("extract_events", event_classifier.main)

    # Opinion processing
    graph.add_node("sentiment_analysis", blank.main)
    graph.add_node("inconsistency_detection", blank.main)
    graph.add_node("person_summary", blank.main)
    graph.add_node("scoring_system_person", blank.main)

    # General event processing
    graph.add_node("scoring_system_general", blank.main)

    graph.add_node("merge_events", merge_events.main)
    graph.add_node("write_events_to_db", write_event.main)    
    


   
    graph.add_edge(START, "extract_events")

    def router_events_opinions(state: ChunkState) -> str:
        # Explicitly check for valid states and route accordingly
        if state.get("general_event") is not None:
            return "scoring_system_general"
        elif state.get("person_event") is not None:
            return "sentiment_analysis"
        else:
            return END  # Explicitly route to END if no valid state is found

    # Add conditional edges based on the router function
    graph.add_conditional_edges("extract_events", router_events_opinions)

    # Opinion processing
    graph.add_edge('sentiment_analysis', 'inconsistency_detection')
    graph.add_edge('inconsistency_detection', 'person_summary')
    graph.add_edge('inconsistency_detection', 'scoring_system_person')
    graph.add_edge('person_summary', 'scoring_system_person')
    graph.add_edge('scoring_system_person', 'merge_events')

    # General event processing
    graph.add_edge('scoring_system_general', 'merge_events')

    graph.add_edge('merge_events', 'write_events_to_db')
    graph.add_edge('write_events_to_db', END)

    return graph.compile()

