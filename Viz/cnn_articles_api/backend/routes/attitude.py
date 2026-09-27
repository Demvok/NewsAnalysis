from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.models import FctAttitude
from backend.schemas import FctAttitudeSchema
from typing import List

router = APIRouter()


@router.get("/", response_model=List[FctAttitudeSchema])
def get_attitude(db: Session = Depends(get_db)):
    return db.query(FctAttitude).all()