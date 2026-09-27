from langgraph.graph import StateGraph, START, END
from nodes import  write_event, event_classifier, event_scoring, sentiment_analysis, inconsistency_detection, person_summary, opinion_scoring
from .states_setup import ChunkState
from typing import List


def create_app():
    graph = StateGraph(state_schema=ChunkState)

    graph.add_node("extract_events", event_classifier.main)

    # Opinion processing
    graph.add_node("sentiment_analysis", sentiment_analysis.main)
    graph.add_node("inconsistency_detection", inconsistency_detection.main)
    graph.add_node("person_summary", person_summary.main)
    graph.add_node("scoring_system_person", opinion_scoring.main)

    # General event processing
    graph.add_node("scoring_system_general", event_scoring.main)

    graph.add_node("write_events_to_db", write_event.main)


    graph.add_edge(START, "extract_events")

    def router_extract_events(state: ChunkState) -> List[str]:
        routes = []
        if state.general_event:
            routes.append("scoring_system_general")
        if state.person_event:
            routes.append("sentiment_analysis")
        if not routes:
            routes.append(END)
        return routes

    graph.add_conditional_edges(
        "extract_events",
        router_extract_events,
        {
            "scoring_system_general": "scoring_system_general",
            "sentiment_analysis": "sentiment_analysis",
            END: END
        }
    )

    # Opinion processing
    graph.add_edge('sentiment_analysis', 'inconsistency_detection')
    graph.add_edge('inconsistency_detection', 'person_summary')
    graph.add_edge('person_summary', 'scoring_system_person')

    graph.add_edge(['scoring_system_person', 'scoring_system_general'], 'write_events_to_db')

    graph.add_edge("write_events_to_db", END)

    return graph.compile()

