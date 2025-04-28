from pydantic import BaseModel, Field
from typing import Optional, List, TypedDict


# Full chunk state with all fields, non-null are present at the start
class ChunkState(TypedDict, total=False):
    chunk_id: int
    topic: str
    origin_article_id:int = None
    content: str
    general_event: Optional[List[dict]] = None
    person_event: Optional[List[dict]] = None
    is_processed: int = 0

class GeneralEvent(BaseModel):
    title: Optional[str] = Field(description="Title of the general event.", max_length=50)
    description: Optional[str] = Field(description="Brief summary of the event, limited to 200 characters.", max_length=200)

class PersonEvent(BaseModel):
    person_name: Optional[str] = Field(description="Name of the person involved in the event.", max_length=150)
    citation: Optional[str] = Field(description="Key statement or citation related to the person and the topic.", max_length=300)




# Parser setup
class EventClassification(BaseModel):
    general_event: Optional[GeneralEvent]
    person_event: Optional[PersonEvent]

class SentimentScore(BaseModel):
    sentiment: int = Field(description="Sentiment score of the opinion.", ge=-1, le=1)