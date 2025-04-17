from langgraph.graph import StateGraph, START, END
import nodes 

from typing import Optional, List, TypedDict




def create_app():
    graph = StateGraph(state_schema='EventExtractionState')

    # Stage 0: Extracting events from chunks
    graph.add_node("extract_events", nodes.event_classifier.extract_events_node)
       
    
    # graph.add_edge("split_articles_to_chunks", "extract_events")  
    
    graph.set_entry_point("extract_events")
    graph.set_finish_point("extract_events")

    return graph.compile()

