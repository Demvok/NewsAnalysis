from pydantic import BaseModel, Field
from typing import Optional, List, Annotated
from operator import add
import json
from ast import literal_eval
from pandas import Timestamp

# Define custom reducers for different merge strategies
def keep_latest(old_val, new_val):
    """Replace old value with new value"""
    return new_val

def merge_unique_events(old_events, new_events):
    """Merge events lists, keeping the most complete version of each event"""
    if not old_events:
        return new_events
    if not new_events:
        return old_events
    
    # Create a dictionary to store the most complete version of each event
    # Use a tuple of key identifying fields as the dictionary key
    event_dict = {}
    
    # Helper function to create a key for each event
    def get_event_key(event):
        if 'person_name' in event and 'citation' in event:
            # For person events
            return ('person', event['person_name'], event['citation'])
        elif 'title' in event and 'description' in event:
            # For general events
            return ('general', event['title'], event['description'])
        else:
            # Fallback for other event types
            return ('other', str(event))
    
    # Process old events first
    for event in old_events:
        key = get_event_key(event)
        event_dict[key] = event
    
    # Process new events, keeping the version with more fields when there's a conflict
    for event in new_events:
        key = get_event_key(event)
        if key in event_dict:
            # If this event already exists, keep the one with more fields
            old_event = event_dict[key]
            if len(event) > len(old_event):
                event_dict[key] = event
        else:
            # New unique event
            event_dict[key] = event
    
    # Return the list of most complete events
    return list(event_dict.values())

def save_state_as_json(state, filename):
    """
    Save the given state as a JSON file.

    Args:
        state (dict): The state to save.
        filename (str): The name of the file to save the state to.
    """
    with open(filename, 'w') as f:
        # Option 1: Use model_dump() to get a Python dict directly
        json.dump(state.model_dump(), f, indent=4, default=str)

def save_output_as_json(state, filename):
    """
    Save the given state as a JSON file.

    Args:
        state (dict): The state to save.
        filename (str): The name of the file to save the state to.
    """
    with open(filename, 'w') as f:
        json.dump(dict(state.items()), f, indent=4, default=str)

def load_state_from_json(filename):
    """
    Load a state from a JSON file and convert it to a ChunkState object.
    
    Args:
        filename (str): The path to the JSON file to load.
        
    Returns:
        ChunkState: The loaded state as a ChunkState object.
    """
    with open(filename, 'r') as f:
        data = json.load(f)
    
    # Handle timestamp conversion if needed
    if 'article_date' in data and data['article_date'] is not None:
        data['article_date'] = Timestamp(data['article_date'])
    
    # Create a ChunkState object from the loaded data
    return ChunkState(**data)


# Full chunk state with all fields
class ChunkState(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    
    chunk_id: Annotated[int, keep_latest] = Field(description="Unique identifier for the chunk")
    topic: Annotated[str, keep_latest]
    topic_id: Annotated[Optional[int], keep_latest]
    origin_article_id: Annotated[Optional[int], keep_latest] = None
    article_date: Annotated[Optional[Timestamp], keep_latest] = None
    content: Annotated[str, keep_latest]
    general_event: Annotated[Optional[List[dict]], merge_unique_events] = Field(default_factory=list)
    person_event: Annotated[Optional[List[dict]], merge_unique_events] = Field(default_factory=list)
    is_processed: Annotated[int, add] = 0


# Parser setup
class GeneralEvent(BaseModel):
    title: Optional[str] = Field(description="Title of the general event.", max_length=50)
    description: Optional[str] = Field(description="Brief summary of the event, limited to 200 characters.", max_length=200)

class PersonEvent(BaseModel):
    person_name: Optional[str] = Field(description="Name of the person involved in the event.", max_length=150)
    citation: Optional[str] = Field(description="Key statement or citation related to the person and the topic.", max_length=300)

class EventClassification(BaseModel):
    general_event: Optional[GeneralEvent]
    person_event: Optional[PersonEvent]

class SentimentScore(BaseModel):
    sentiment: float = Field(description="Sentiment score of the opinion.", ge=-1, le=1)

class InconsistencyComment(BaseModel):
    inconsistency_comment: str = Field(description="Comment explaining the inconsistency.", max_length=200)

class PersonSummary(BaseModel):
    person_summary: str = Field(description="Summary of the person's stance on topic.", max_length=300)

class OpinionScores(BaseModel):
    relevancy: float = Field(description="Relevancy score from 0 to 1", ge=0, le=1)
    contribution: float = Field(description="Contribution score from 0 to 1", ge=0, le=1)
    controversy: float = Field(description="Controversy score from 0 to 1", ge=0, le=1)