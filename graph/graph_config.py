from langgraph.graph import StateGraph
from nodes import  write_event, event_classifier

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

    # Stage 0: Extracting events from chunks
    graph.add_node("extract_events", event_classifier.main)
    graph.add_node("write_events_to_db", write_event.main)
       
    
    graph.add_edge("extract_events", "write_events_to_db")  
    
    graph.set_entry_point("extract_events")
    graph.set_finish_point("write_events_to_db")

    return graph.compile()

