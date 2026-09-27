from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from Legacy.cnn_articles_api.backend.database import get_db
from Legacy.cnn_articles_api.backend.models import DimPerson
from Legacy.cnn_articles_api.backend.schemas import PersonSchema
from typing import List

router = APIRouter()


@router.get("/", response_model=List[PersonSchema])
def get_opinions(db: Session = Depends(get_db)):
    return db.query(DimPerson).all()





