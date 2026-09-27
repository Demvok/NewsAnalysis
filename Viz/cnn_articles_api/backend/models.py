from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, Float
from sqlalchemy.orm import relationship
from backend.database import Base
import datetime

class DimPerson(Base):
    __tablename__ = "dimPerson"
    person_id = Column(Integer, primary_key=True)
    person_name = Column(String(255))
    image_url = Column(String(255))


class DimTopic(Base):
    __tablename__ = "dimTopic"
    topic_id = Column(Integer, primary_key=True)
    topic_name = Column(String(255))
    query = Column(String(255))
    source = Column(String(255))

class DimArticle(Base):
    __tablename__ = "dimArticle"
    article_id = Column(Integer, primary_key=True)
    title = Column(String(255))
    fk_topic_id = Column(Integer, ForeignKey("dimTopic.topic_id"))
    url = Column(String(255))
    article_date = Column(DateTime)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    modified_at = Column(DateTime, default=datetime.datetime.utcnow)
    content = Column(Text)

class DimOpinion(Base):
    __tablename__ = "dimOpinion"
    opinion_id = Column(Integer, primary_key=True)
    fk_origin_article_id = Column(Integer, ForeignKey("dimArticle.article_id"))
    fk_person_id = Column(Integer, ForeignKey("dimPerson.person_id"))
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    modified_at = Column(DateTime, default=datetime.datetime.utcnow)
    citation = Column(Text)
    sentiment_score = Column(Float)
    inconsistency_flag = Column(Boolean)
    inconsistency_with_id = Column(String)
    inconsistency_comment = Column(Text)
    relevancy_score = Column(Float)
    controversy_score = Column(Float)
    contribution_score = Column(Float)
    opinion_hotness = Column(Float)
    is_selected = Column(Boolean)



class dimEvents(Base):
    __tablename__ = "dimEvents"
    event_id = Column(Integer, primary_key=True, nullable=False)
    event_title = Column(String(50))
    fk_origin_article_id = Column(Integer, ForeignKey("dimArticle.article_id"))
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    modified_at = Column(DateTime, default=datetime.datetime.utcnow)
    relevance_score = Column(Float)
    influence_score = Column(Float)
    novelty_score = Column(Float)
    event_hotness = Column(Float)
    is_selected = Column(Boolean)


class FctAttitude(Base):
    __tablename__ = "fctAttitude"

    fk_topic_id = Column(Integer, primary_key=True, nullable=False)
    fk_person_id = Column(Integer, primary_key=True, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    modified_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    sentiment_deviation = Column(Float)
    stance = Column(String(30))
    person_summary = Column(String(300))