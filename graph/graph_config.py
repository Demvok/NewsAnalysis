from langgraph.graph import StateGraph
from nodes import  write_event, event_classifier, sentiment_analysis

from typing import Optional, List, TypedDict

class ChunkState(TypedDict, total=False):
    chunk_id: int
    topic: str
    content: str
    general_event: Optional[List[dict]] = None
    person_event: Optional[List[dict]] = None
    is_processed: int = 0


def create_app():
    graph = StateGraph(state_schema=ChunkState)

    graph.add_node("extract_events", event_classifier.main)
    graph.add_node("sentiment_analysis", sentiment_analysis.main)
    graph.add_node("write_events_to_db", write_event.main)    
    

    def condition_event_or_opinion(state):
        if bool(state.get("person_event")):
            return 'sentiment_analysis'
        else:
            return 'write_events_to_db'

    graph.add_conditional_edges('extract_events', condition_event_or_opinion)
    graph.add_edge("sentiment_analysis", "write_events_to_db")
    
    graph.set_entry_point("extract_events")
    graph.set_finish_point("write_events_to_db")

    return graph.compile()

