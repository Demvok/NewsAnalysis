from langgraph.graph import StateGraph, START, END
import nodes 

from typing import Optional, List, TypedDict
from pydantic import Field
import nodes.write_event

class ChunkState(TypedDict):
    chunk_id: int = Field(description="ID of the chunk.")
    topic: str = Field(description="Topic of the chunk.")
    content: str = Field(description="Content of the chunk.")


def create_app():
    graph = StateGraph(state_schema=ChunkState)

    # Stage 0: Extracting events from chunks
    graph.add_node("extract_events", nodes.event_classifier.main)
    graph.add_node("write_events_to_db", nodes.write_event.main)
       
    
    graph.add_edge("extract_events", "write_events_to_db")  
    
    graph.set_entry_point("extract_events")
    graph.set_finish_point("write_events_to_db")

    return graph.compile()

