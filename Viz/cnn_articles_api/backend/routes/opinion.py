from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from backend.database import get_db
from backend.models import DimOpinion
from backend.schemas import OpinionSchema

router = APIRouter()

@router.get("/", response_model=List[OpinionSchema])
def get_opinions(
    db: Session = Depends(get_db),
    topic_id: Optional[int] = Query(None, description="Filter by topic id"),
    person_id: Optional[int] = Query(None, description="Filter by person id"),
    relevancy_score: Optional[int] = Query(None, description="Filter by relevancy score"),
    is_selected: Optional[int] = Query(None, description="Filter by is_selected"),
):
    query = db.query(DimOpinion)
    
    if topic_id is not None:
        # Припускаємо, що в таблиці DimOpinion є поле, яке містить topic_id
        query = query.filter(DimOpinion.fk_topic_id == topic_id)
    
    if person_id is not None:
        # Припускаємо, що в таблиці DimOpinion є поле, яке містить person_id
        query = query.filter(DimOpinion.fk_person_id == person_id)

    if relevancy_score is not None:
        # Припускаємо, що в таблиці DimOpinion є поле, яке містить person_id
        query = query.filter(DimOpinion.relevancy_score >= relevancy_score)

    if is_selected is not None:
        # Припускаємо, що в таблиці DimOpinion є поле, яке містить person_id
        query = query.filter(DimOpinion.is_selected == is_selected)

    return query.all()