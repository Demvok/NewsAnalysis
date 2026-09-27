from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.models import dimEvents
from backend.schemas import EventSchema
from typing import List, Optional

router = APIRouter()


@router.get("/", response_model=List[EventSchema])
def get_opinions(db: Session = Depends(get_db),
    relevance_score: Optional[float] = Query(None, description="Filter by relevancy score"),
    event_hotness: Optional[float] = Query(None, description="Filter by event hotness"),
    is_selected: Optional[float] = Query(None, description="Filter by is_selected"),
):
    query = db.query(dimEvents)

    
    if event_hotness is not None:
        # Припускаємо, що в таблиці DimOpinion є поле, яке містить person_id
        query = query.filter(dimEvents.event_hotness >= event_hotness)

    if relevance_score is not None:
        # Припускаємо, що в таблиці DimOpinion є поле, яке містить person_id
        query = query.filter(dimEvents.relevance_score >= relevance_score)

    if is_selected is not None:
        # Припускаємо, що в таблиці DimOpinion є поле, яке містить person_id
        query = query.filter(dimEvents.is_selected == is_selected)


    return query.all()
