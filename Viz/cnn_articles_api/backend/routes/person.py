from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.models import DimPerson
from backend.schemas import PersonSchema
from typing import List

router = APIRouter()


@router.get("/", response_model=List[PersonSchema])
def get_opinions(db: Session = Depends(get_db)):
    return db.query(DimPerson).all()





