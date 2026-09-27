from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from Legacy.cnn_articles_api.backend.database import get_db
from Legacy.cnn_articles_api.backend.models import FctAttitude
from Legacy.cnn_articles_api.backend.schemas import FctAttitudeSchema
from typing import List

router = APIRouter()


@router.get("/", response_model=List[FctAttitudeSchema])
def get_attitude(db: Session = Depends(get_db)):
    return db.query(FctAttitude).all()