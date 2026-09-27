from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.models import DimArticle
from backend.schemas import ArticleSchema, ArticleCreateSchema
from typing import List

router = APIRouter()


@router.get("/", response_model=List[ArticleSchema])
def get_opinions(db: Session = Depends(get_db)):
    return db.query(DimArticle).all()



@router.post("/", response_model=ArticleSchema, status_code=status.HTTP_201_CREATED)
def create_article(article: ArticleCreateSchema, db: Session = Depends(get_db)):
    # Перевірка на унікальність URL (опційно)
    existing = db.query(DimArticle).filter(DimArticle.url == article.url).first()
    if existing:
        raise HTTPException(status_code=400, detail="Article with this URL already exists")

    new_article = DimArticle(**article.dict())
    db.add(new_article)
    db.commit()
    db.refresh(new_article)
    return new_article