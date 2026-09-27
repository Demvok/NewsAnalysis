from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.models import DimTopic
from backend.schemas import TopicSchema
from typing import List

router = APIRouter()


@router.get("/", response_model=List[TopicSchema])
def get_opinions(db: Session = Depends(get_db)):
    return db.query(DimTopic).all()