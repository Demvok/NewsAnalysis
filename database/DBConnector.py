import os, time
from logging import DEBUG, INFO, WARNING, ERROR, CRITICAL
from tqdm import tqdm
import utils.logger as log
from datetime import datetime
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.sql.expression import tuple_, or_, and_
from sqlalchemy import Column, Integer, String, ForeignKey, Float, TIMESTAMP, Boolean, func
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, scoped_session
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm.exc import DetachedInstanceError
from contextlib import contextmanager


############################################################################################################

DATABASE_URL = os.getenv('DATABASE_URL')
logger = log.setup_logger(name='DBConnector', log_file='dbloader.log')

############################################################################################################

# Create engine and session factory
engine = create_engine(DATABASE_URL)
SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
Session = scoped_session(SessionFactory)

def refresh_connection():
    """DEPRECATED"""
    global engine, SessionFactory, Session
    engine = create_engine(DATABASE_URL)
    SessionFactory = sessionmaker(bind=engine)
    Session = scoped_session(SessionFactory)

@contextmanager
def _get_session():
    session = Session()
    try:
        yield session
        session.commit()
    except OperationalError as e:
        session.rollback()
        refresh_connection()
        logger.error(f'Connection lost, please retry: {e}')
        raise
    except DetachedInstanceError as e:
        session.rollback()
        logger.warning(f'DetachedInstanceError occurred: {e}')
        raise
    except Exception as e:
        session.rollback()
        logger.critical(e)
        raise
    finally:
        session.close()

#
# Defining ORMs
#

Base = declarative_base()

class DimArticle(Base):
    __tablename__ = 'dimArticle'

    article_id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(150), nullable=True)
    fk_topic_id = Column(Integer, ForeignKey('dimTopic.topic_id', ondelete="CASCADE"), nullable=False, default=1)
    url = Column(String(150), nullable=False)
    article_date = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), nullable=True)
    modified_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp(), nullable=True)
    content = Column(String, nullable=True)

    # One-to-many relationship with DimEvent
    events = relationship("DimEvent", back_populates="article", cascade="all, delete-orphan")
    # One-to-many relationship with DimOpinion
    opinions = relationship("DimOpinion", back_populates="article", cascade="all, delete-orphan")
    # One-to-many relationship with DimArticleChunks
    chunks = relationship("DimArticleChunks", back_populates="article", cascade="all, delete-orphan")
    # Many-to-one relationship with DimTopic
    topic = relationship("DimTopic", back_populates="articles")

class DimEvent(Base):
    __tablename__ = 'dimEvents'

    event_id = Column(Integer, primary_key=True, autoincrement=True)
    event_title = Column(String(50), nullable=True)
    fk_origin_article_id = Column(Integer, ForeignKey('dimArticle.article_id', ondelete="CASCADE"), nullable=False)
    description = Column(String(200), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), nullable=True)
    modified_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp(), nullable=True)
    relevance_score = Column(Float, nullable=True)
    influence_score = Column(Float, nullable=True)
    novelty_score = Column(Float, nullable=True)
    event_hotness = Column(Float, nullable=True)
    is_selected = Column(Boolean, nullable=True)

    # Many-to-one relationship with DimArticle
    article = relationship("DimArticle", back_populates="events")

class DimOpinion(Base):
    __tablename__ = 'dimOpinion'

    opinion_id = Column(Integer, primary_key=True, autoincrement=True)
    fk_origin_article_id = Column(Integer, ForeignKey('dimArticle.article_id', ondelete="CASCADE"), nullable=False)
    fk_person_id = Column(Integer, ForeignKey('dimPerson.person_id', ondelete="CASCADE"), nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), nullable=True)
    modified_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp(), nullable=True)
    citation = Column(String(300), nullable=True)
    sentiment_score = Column(Float, nullable=True)
    inconsistency_flag = Column(Boolean, nullable=True)
    inconsistency_with_id = Column(Integer, ForeignKey('dimOpinion.opinion_id', ondelete="SET NULL"), nullable=True)
    inconsistency_comment = Column(String(200), nullable=True)
    controversy_score = Column(Float, nullable=True)
    relevancy_score = Column(Float, nullable=True)
    contribution_score = Column(Float, nullable=True)
    opinion_hotness = Column(Float, nullable=True)
    is_selected = Column(Boolean, nullable=True)

    # Many-to-one relationship with DimArticle
    article = relationship("DimArticle", back_populates="opinions")
    # Many-to-one relationship with DimPerson
    person = relationship("DimPerson", back_populates="opinions")
    # Self-referential relationship for inconsistent opinions
    inconsistent_opinion = relationship(
        "DimOpinion", remote_side=[opinion_id], backref="inconsistent_with"
    )

class DimPerson(Base):
    __tablename__ = 'dimPerson'

    person_id = Column(Integer, primary_key=True, autoincrement=True)
    person_name = Column(String(100), nullable=False)
    image_url = Column(String(100), nullable=True)
    political_party = Column(String(50), nullable=True)
    attribute_1 = Column(String(100), nullable=True)
    attribute_2 = Column(String(100), nullable=True)
    attribute_3 = Column(String(100), nullable=True)
    attribute_4 = Column(String(100), nullable=True)

    # One-to-many relationship with DimOpinion
    opinions = relationship("DimOpinion", back_populates="person")
    # One-to-many relationship with FctAttitude
    attitudes = relationship("FctAttitude", back_populates="person")

class DimArticleChunks(Base):
    __tablename__ = 'dimArticleChunks'

    chunk_id = Column(Integer, primary_key=True, autoincrement=True)
    fk_article_id = Column(Integer, ForeignKey('dimArticle.article_id', ondelete="CASCADE"), nullable=False)
    start_index = Column(Integer, nullable=True)
    end_index = Column(Integer, nullable=True)
    is_processed = Column(Boolean, nullable=True)

    # Many-to-one relationship with DimArticle
    article = relationship("DimArticle", back_populates="chunks")

class DimTopic(Base):
    __tablename__ = 'dimTopic'

    topic_id = Column(Integer, primary_key=True, autoincrement=True)
    topic_name = Column(String(50), nullable=False)
    query = Column(String(100), nullable=False)
    source = Column(String(100), nullable=True)

    # One-to-many relationship with FctAttitude
    attitudes = relationship("FctAttitude", back_populates="topic")
    # One-to-many relationship with DimArticle
    articles = relationship("DimArticle", back_populates="topic")

class FctAttitude(Base):
    __tablename__ = 'fctAttitude'

    fk_topic_id = Column(Integer, ForeignKey('dimTopic.topic_id'), primary_key=True, nullable=False)
    fk_person_id = Column(Integer, ForeignKey('dimPerson.person_id'), primary_key=True, nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), nullable=True)
    modified_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp(), nullable=True)
    sentiment_deviation = Column(Float, nullable=True)
    stance = Column(String(30), nullable=True)
    person_summary = Column(String(300), nullable=True)
    is_expert_flag = Column(Boolean, nullable=True)

    # Many-to-one relationship with DimTopic
    topic = relationship("DimTopic", back_populates="attitudes")
    # Many-to-one relationship with DimPerson
    person = relationship("DimPerson", back_populates="attitudes")

# 
# Top-level functions
# 

def t_execute_query(query: str):
    """
    Виконує SQL запит та повертає результат.

    :param query: SQL запит у вигляді рядка.
    :return: Якщо запит повертає рядки, повертає список результатів. Інакше повертає None.
    """
    with engine.connect() as conn:
        result = conn.execute(text(query))
        if result.returns_rows:
            data = result.fetchall()

def t_get_events_input(processing_stage=-1, **conditions):
    """Gets a list of event_ids that match the given conditions (e.g., is_selected=1).
    Has processing stage settings:\n
    processing_stage = -1 - does not perform additional checks\n
    processing_stage = 0 - corresponds to complete raw data without scores\n
    processing_stage = 1 - corresponds to data that has successfully passed evaluation\n
    """
    matching_ids = find_events_by_conditions(processing_stage, **conditions)
    return get_event_df(matching_ids)

def t_get_opinions_input(processing_stage=-1, **conditions):
    """Gets a list of opinion_ids that match the given conditions.\n
    processing_stage=-1 does not perform additional filtering\n
    processing_stage=0 corresponds to complete raw data, without sentiment_score\n
    processing_stage=1 corresponds to complete data after sentiment analysis, without inconsistency\n
    processing_stage=2 corresponds to complete data after inconsistency detection, without score\n
    processing_stage=3 corresponds to fully evaluated data\n
    """
    matching_ids = find_opinion_by_conditions(processing_stage, **conditions)
    return get_opinion_df(matching_ids)

def t_get_article_chunk_content(chunk_id: int):
    chunk = get_article_chunk(chunk_id)
    article  = get_article(int(chunk.fk_article_id)).content
    if chunk.start_index >= chunk.end_index: raise ValueError('Broken chunk indexes')
    return article[int(chunk.start_index) : int(chunk.end_index)]

def t_find_articles_without_chunks():
    res = None
    with _get_session() as session:
        query = session.query(DimArticle.article_id).outerjoin(DimArticleChunks, DimArticle.article_id == DimArticleChunks.fk_article_id).filter(DimArticleChunks.chunk_id == None).order_by(DimArticle.article_id.asc())
        res = query.all()
    return [elem.article_id for elem in res] if res else None

def t_get_articles_without_chunks_input():
    article_ids = t_find_articles_without_chunks()
    if not article_ids:
        logger.warning("No articles found without chunks")
        return pd.DataFrame()  # Return an empty DataFrame as a precaution
    return get_article_df(article_ids)

def t_get_unprocessed_chunks_input(*args, **kwargs):
    """
    Fetches unprocessed chunks along with their topics and content in a single query.
    Call with is_processed=False to get unprocessed chunks.
    """
    with _get_session() as session:
        # Query to fetch unprocessed chunks
        query = (
            session.query(
                DimArticleChunks.chunk_id,
                DimArticleChunks.fk_article_id,
                DimArticleChunks.start_index,
                DimArticleChunks.end_index,
                DimTopic.topic_name,
                DimArticle.content
            )
            .join(DimArticle, DimArticleChunks.fk_article_id == DimArticle.article_id)
            .join(DimTopic, DimArticle.fk_topic_id == DimTopic.topic_id)
            .filter(DimArticleChunks.is_processed == False)  # Only unprocessed chunks
        )

        # Apply additional conditions if provided
        for attr, value in kwargs.items():
            query = query.filter(getattr(DimArticleChunks, attr) == value)

        # Fetch results
        results = query.all()

        if not results:
            logger.warning("No unprocessed chunks found")
            return pd.DataFrame()  # Return an empty DataFrame as a precaution

        # Convert results to a DataFrame
        data = [
            {
                "chunk_id": row.chunk_id,
                "content": row.content[row.start_index:row.end_index],
                "topic": row.topic_name,
            }
            for row in results
        ]
        return pd.DataFrame(data)

def t_upload_person_event(
        article_id: str,
        person: str,
        citation: str,
        sentiment: float = None,
        inconsistency_flag: bool = None,
        inconsistency_id: int = None,
        inconsistency_comment: str = None,
        controversy: float = None,
        relevance: float = None,
        contribution: float = None,
        overall: float = None
    ):
    if sentiment is None and inconsistency_flag is None and controversy is None:
        # basic upload
        logger.info('Updating base data')
        person_id = add_person(person)
        add_opinion(article_id, person_id, citation)
    elif sentiment is not None: 
        # sentiment upload
        logger.info('Updating sentiment score')
        person_id = find_person(person)
        opinion_id = find_opinion(article_id, person_id, citation)
        if opinion_id is not None:
            update_opinion(opinion_id=opinion_id, sentiment_score=sentiment)
        else:
            logger.warning(f"Opinion not found for article_id={article_id}, person_id={person_id}, citation={citation}")
    elif inconsistency_flag is not None or inconsistency_comment is not None or inconsistency_id is not None:
        # inconsistency upload
        logger.info('Updating inconsistency data')
        person_id = find_person(person)
        opinion_id = find_opinion(article_id, person_id, citation)
        if opinion_id is not None:
            if inconsistency_flag is not None:
                inconsistency_flag = 1 if inconsistency_flag else 0
            else:
                inconsistency_flag = None
            inconsistency_id = None if inconsistency_id is None else inconsistency_id
            inconsistency_comment = None if inconsistency_comment is None else inconsistency_comment
            update_opinion(opinion_id=opinion_id, inconsistency_flag=inconsistency_flag, inconsistency_with_id=inconsistency_id, inconsistency_comment=inconsistency_comment)
        else:
            logger.warning(f"Opinion not found for article_id={article_id}, person_id={person_id}, citation={citation}")
    else:
        # scores upload
        logger.info('Updating scores')
        person_id = find_person(person)
        opinion_id = find_opinion(article_id, person_id, citation)
        if opinion_id is not None:
            controversy = None if controversy is None else float(controversy)
            relevance = None if relevance is None else float(relevance)
            contribution = None if contribution is None else float(contribution)
            overall = None if overall is None else float(overall)
            update_opinion(opinion_id=opinion_id, controversy_score=controversy, relevancy_score=relevance, contribution_score=contribution, opinion_hotness=overall)
        else:
            logger.warning(f"Opinion not found for article_id={article_id}, person_id={person_id}, citation={citation}")

# 
# DimEvent
# 

def find_event(fk_origin_article_id: int, description: str, get_id=True):
    """Шукає event_id в базі за fk_origin_article_id та description."""
    with _get_session() as session:
        start_time = time.time()
        # Виконуємо запит для пошуку події за двома полями
        event = session.query(DimEvent).filter(
            DimEvent.fk_origin_article_id == fk_origin_article_id,
            DimEvent.description == description
        ).first()
        end_time = time.time()

        if event:
            logger.debug(f"Event already exists with event_id: {event.event_id}", extra={"execution_time": log.timeUsed(start_time, end_time)})
            return event if not get_id else event.event_id # Повертаємо event_id, якщо подія знайдена
        else:
            logger.debug(f"Event not found", extra={"execution_time": log.timeUsed(start_time, end_time)})
            return None  # Повертаємо None, якщо подія не знайдена

def find_events_by_conditions(processing_stage=-1, **conditions):
    """Отримує список event_id, що відповідають заданим умовам (наприклад is_selected=1).
    Має налаштування стану обробки:\n
    processing_stage = -1 - не здійснює додаткової перевірки\n
    processing_stage = 0 - відповідає повним сирим даним без оцінок\n
    processing_stage = 1 - відповідає даним, що успішно пройшли оцінку\n
    """
    with _get_session() as session:
        start_time = time.time()
        query = session.query(DimEvent.event_id)
        
        for attr, value in conditions.items():
            query = query.filter(getattr(DimEvent, attr) == value)
        
        if processing_stage == 0:
            query = query.filter(
                and_(
                    DimEvent.fk_origin_article_id.isnot(None),
                    DimEvent.description.isnot(None)),
                    or_(
                        DimEvent.relevance_score.is_(None),
                        DimEvent.influence_score.is_(None),
                        DimEvent.novelty_score.is_(None),
                        DimEvent.event_hotness.is_(None)
                    )
                )
        elif processing_stage == 1:
            query = query.filter(
                DimEvent.relevance_score.isnot(None),
                DimEvent.influence_score.isnot(None),
                DimEvent.novelty_score.isnot(None),
                DimEvent.event_hotness.isnot(None)
            )
        
        event_ids = [event_id for event_id, in query.all()]
        end_time = time.time()

        if event_ids:
            logger.info(f"Found {len(event_ids)} events matching conditions", extra={'execution_time': log.timeUsed(start_time, end_time)})
        else:
            logger.info(f"No events found matching conditions", extra={'execution_time': log.timeUsed(start_time, end_time)})
        
        return event_ids

def add_event(fk_origin_article_id: int, description: str) -> int:
    """Додає новий запис у dimEvents, якщо його ще немає, і повертає його event_id."""
    with _get_session() as session:
        start_time = time.time()
        # Перевіряємо, чи існує вже подія з таким fk_origin_article_id та description
        existing_event = find_event(fk_origin_article_id, description, get_id=False)

        if existing_event: return existing_event.event_id

        # Якщо подія не існує, додаємо новий запис
        new_event = DimEvent(
            fk_origin_article_id=fk_origin_article_id,
            description=description
        )
        session.add(new_event)
        session.commit()
        session.refresh(new_event)  # Отримуємо згенерований event_id
        end_time = time.time()
        logger.info(f"Event added with event_id: {new_event.event_id}, description: {description}", extra={'execution_time': log.timeUsed(start_time, end_time)})
        return new_event.event_id

def add_event_full(
    fk_origin_article_id: int,
    description: str,
    event_title: str = None,
    relevance_score: float = None,
    influence_score: float = None,
    novelty_score: float = None,
    event_hotness: float = None,
    is_selected: bool = None
) -> int:
    """Додає новий запис у dimEvents з можливістю встановлення всіх полів, якщо його ще немає, і повертає його event_id."""
    with _get_session() as session:
        start_time = time.time()
        existing_event = find_event(fk_origin_article_id, description, get_id=False)

        if existing_event:
            return existing_event.event_id

        new_event = DimEvent(
            fk_origin_article_id=fk_origin_article_id,
            description=description,
            event_title=event_title,
            relevance_score=relevance_score,
            influence_score=influence_score,
            novelty_score=novelty_score,
            event_hotness=event_hotness,
            is_selected=bool(is_selected)
        )
        session.add(new_event)
        session.commit()
        session.refresh(new_event)
        end_time = time.time()
        logger.info(f"Event added with event_id: {new_event.event_id}, description: {description}", extra={'execution_time': log.timeUsed(start_time, end_time)})
        return new_event.event_id

def add_event_df(df: pd.DataFrame):
    """Завантажує DataFrame у таблицю dimEvents, перевіряючи наявність записів перед додаванням."""
    with _get_session() as session:
        start_time = time.time()
        # Конвертуємо DataFrame у список словників для кожного рядка
        records = df.to_dict(orient='records')
        added_count = 0

        for record in records:
            # Перевіряємо, чи існує вже подія з таким fk_origin_article_id та description
            existing_event = find_event(record['fk_origin_article_id'], record['description'], get_id=False)

            if not existing_event:
                # Якщо подія не існує, додаємо новий запис
                new_event = DimEvent(**record)
                session.add(new_event)
                added_count += 1

        # Зберігаємо зміни в БД
        session.commit()
        end_time = time.time()
        logger.info(f"Added {added_count} events", extra={'execution_time': log.timeUsed(start_time, end_time)})

def get_event(event_id: int) -> pd.Series:
    """Отримує подію за ID та повертає її як Series."""
    
    def get_event_element(event_id: int):
        with _get_session() as session:
            return session.query(DimEvent).filter(DimEvent.event_id == event_id).first()

    start_time = time.time()
    event = get_event_element(event_id)
    if event:
        # Перетворюємо модель на словник і створюємо Series
        event_dict = {
            'event_id': event.event_id,
            'event_title': event.event_title,
            'fk_origin_article_id': event.fk_origin_article_id,
            'description': event.description,
            'created_at': event.created_at,
            'modified_at': event.modified_at,
            'relevance_score': event.relevance_score,
            'influence_score': event.influence_score,
            'novelty_score': event.novelty_score,
            'event_hotness': event.event_hotness,
            'is_selected': event.is_selected
        }
        end_time = time.time()
        logger.debug(f"Event found with event_id: {event_id}", extra={"execution_time": log.timeUsed(start_time, end_time)})
        return pd.Series(event_dict)  # Створюємо Series з одного запису
    else:
        logger.info(f"Event with event_id {event_id} not found", extra={"execution_time": log.timeUsed(start_time, end_time)})
        return pd.Series()  # Повертаємо порожній Series, якщо запис не знайдений

def get_event_df(event_ids: list) -> pd.DataFrame:
    """Отримує події за списком ID та повертає їх як DataFrame."""
    with _get_session() as session:
        start_time = time.time()
        events = session.query(DimEvent).filter(DimEvent.event_id.in_(event_ids)).all()
        end_time = time.time()

        if events:
            event_dicts = [{
                'event_id': event.event_id,
                'event_title': event.event_title,
                'fk_origin_article_id': event.fk_origin_article_id,
                'description': event.description,
                'created_at': event.created_at,
                'modified_at': event.modified_at,
                'relevance_score': event.relevance_score,
                'influence_score': event.influence_score,
                'novelty_score': event.novelty_score,
                'event_hotness': event.event_hotness,
                'is_selected': event.is_selected
            } for event in events]
            logger.info(f"Found {len(events)} events", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.DataFrame(event_dicts)
        else:
            logger.info(f"No events found for the provided event_ids", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.DataFrame()

def update_event(
    event_id: int,
    event_title: str = None,
    relevance_score: float = None,
    influence_score: float = None,
    novelty_score: float = None,
    event_hotness: float = None,
    is_selected: bool = None
):
    """Оновлює існуючий запис у dimEvents, доповнюючи його новими даними."""
    with _get_session() as session:
        start_time = time.time()
        event = session.query(DimEvent).filter(DimEvent.event_id == int(event_id)).first()
        if not event:
            logger.info(f"Event with event_id={int(event_id)} not found")
            return

        # Оновлення лише переданих параметрів
        if event_title is not None:
            event.event_title = str(event_title)
        if relevance_score is not None:
            event.relevance_score = float(relevance_score)
        if influence_score is not None:
            event.influence_score = float(influence_score)
        if novelty_score is not None:
            event.novelty_score = float(novelty_score)
        if event_hotness is not None:
            event.event_hotness = float(event_hotness)
        if is_selected is not None:
            event.is_selected = bool(is_selected)
        end_time = time.time()
        logger.debug(f"{int(event_id)} updated successfully", extra={'execution_time': log.timeUsed(start_time, end_time)})

def update_event_df(df: pd.DataFrame):
    """Оновлює існуючі записи у dimEvents на основі DataFrame з новими даними."""
    with _get_session() as session:
        start_time = time.time()
        updated_count = 0

        for _, row in df.iterrows():
            event = session.query(DimEvent).filter(DimEvent.event_id == int(row['event_id'])).first()
            if event:
                if 'event_title' in row and pd.notna(row['event_title']):
                    event.event_title = str(row['event_title'])
                if 'relevance_score' in row and pd.notna(row['relevance_score']):
                    event.relevance_score = float(row['relevance_score'])
                if 'influence_score' in row and pd.notna(row['influence_score']):
                    event.influence_score = float(row['influence_score'])
                if 'novelty_score' in row and pd.notna(row['novelty_score']):
                    event.novelty_score = float(row['novelty_score'])
                if 'event_hotness' in row and pd.notna(row['event_hotness']):
                    event.event_hotness = float(row['event_hotness'])
                if 'is_selected' in row and pd.notna(row['is_selected']):
                    event.is_selected = bool(row['is_selected'])
                updated_count += 1

        session.commit()
        end_time = time.time()
        logger.debug(f"Updated {updated_count} events", extra={'execution_time': log.timeUsed(start_time, end_time)})

def delete_event(event_id: int):
    """Видаляє запис з dimEvents за event_id."""
    with _get_session() as session:
        start_time = time.time()
        event = session.query(DimEvent).filter(DimEvent.event_id == event_id).first()
        if not event:
            logger.info(f"Event with event_id={event_id} not found")
            return
        
        session.delete(event)
        end_time = time.time()
        logger.debug(f"Event with event_id={event_id} deleted successfully", extra={'execution_time': log.timeUsed(start_time, end_time)})

def delete_events(event_ids: list):
    """Видаляє записи з dimEvents за списком event_id."""
    with _get_session() as session:
        start_time = time.time()
        events = session.query(DimEvent).filter(DimEvent.event_id.in_(event_ids)).all()
        if not events:
            logger.info(f"No events found for the provided event_ids")

        for event in events:
            session.delete(event)
        
        session.commit()
        end_time = time.time()
        logger.debug(f"Events with event_ids={event_ids} deleted successfully", extra={'execution_time': log.timeUsed(start_time, end_time)})

# 
# DimPerson
# 

def find_person(person_name: str, get_id=True):
    """Searches for person_id in the database by person_name."""
    with _get_session() as session:
        start_time = time.time()
        person = session.query(DimPerson).filter(
            DimPerson.person_name == person_name
        ).first()
        end_time = time.time()

        if person:
            logger.debug(f'Person already exists with person_id: {person.person_id}', extra={"execution_time": log.timeUsed(start_time, end_time)})
            return person if not get_id else person.person_id # Повертаємо person, якщо знайдено
        else:
            logger.warning(f'Person not found', extra={"execution_time": log.timeUsed(start_time, end_time)})
            return None  # Повертаємо None, якщо не знайдено

def find_person_by_conditions(**conditions):
    """Отримує список person_id, що відповідають заданим умовам."""
    with _get_session() as session:
        start_time = time.time()
        query = session.query(DimPerson.person_id)
        
        for attr, value in conditions.items():
            if isinstance(value, tuple) and len(value) == 2:
                # Handle range conditions (e.g., date range)
                query = query.filter(getattr(DimPerson, attr).between(value[0], value[1]))
            else:
                # Handle equality conditions
                query = query.filter(getattr(DimPerson, attr) == value)
        
        person_ids = [person_id for person_id, in query.all()]
        end_time = time.time()
        
        if person_ids:
            logger.info(f"Found {len(person_ids)} persons matching conditions", extra={'execution_time': log.timeUsed(start_time, end_time)})
        else:
            logger.warning(f"No persons found matching conditions", extra={'execution_time': log.timeUsed(start_time, end_time)})
        return person_ids

def add_person(
        person_name: str,
        image_url: str = None,
        political_party: str = None,
        attribute_1: str = None,
        attribute_2: str = None,
        attribute_3: str = None,
        attribute_4: str = None) -> int:
    """Додає новий запис у dimPerson, якщо його ще немає, і повертає його person_id."""
    with _get_session() as session:
        start_time = time.time()
        existing_person = find_person(person_name, get_id=False)

        if existing_person: return existing_person.person_id

        new_person = DimPerson(
            person_name=person_name,
            image_url=image_url,
            political_party=political_party,
            attribute_1=attribute_1,
            attribute_2=attribute_2,
            attribute_3=attribute_3,
            attribute_4=attribute_4
        )
        session.add(new_person)
        session.commit()
        session.refresh(new_person)
        end_time = time.time()
        logger.info(f"Person added with person_id: {new_person.person_id}, person_name: {person_name}", extra={'execution_time': log.timeUsed(start_time, end_time)})
        return new_person.person_id

def add_person_full(
    person_name: str,
    image_url: str = None,
    political_party: str = None,
    attribute_1: str = None,
    attribute_2: str = None,
    attribute_3: str = None,
    attribute_4: str = None
) -> int:
    """Додає новий запис у dimPerson з можливістю встановлення всіх полів, якщо його ще немає, і повертає його person_id."""
    with _get_session() as session:
        start_time = time.time()
        existing_person = find_person(person_name, get_id=False)

        if existing_person:
            return existing_person.person_id

        new_person = DimPerson(
            person_name=person_name,
            image_url=image_url,
            political_party=political_party,
            attribute_1=attribute_1,
            attribute_2=attribute_2,
            attribute_3=attribute_3,
            attribute_4=attribute_4
        )
        session.add(new_person)
        session.commit()
        session.refresh(new_person)
        end_time = time.time()
        logger.info(f"Person added with person_id: {new_person.person_id}, person_name: {person_name}", extra={'execution_time': log.timeUsed(start_time, end_time)})
        return new_person.person_id

def add_person_df(df: pd.DataFrame):
    """Завантажує DataFrame у таблицю dimPerson, перевіряючи наявність записів перед додаванням."""
    with _get_session() as session:
        start_time = time.time()
        records = df.to_dict(orient='records')
        added_count = 0

        for record in records:
            existing_person = find_person(record['person_name'], get_id=False)

            if not existing_person:
                new_person = DimPerson(**record)
                session.add(new_person)
                added_count += 1

        session.commit()
        end_time = time.time()
        logger.info(f"Added {added_count} persons", extra={'execution_time': log.timeUsed(start_time, end_time)})

def get_person(person_id: int) -> pd.Series:
    """Отримує особу за ID та повертає її як Series."""
    with _get_session() as session:
        start_time = time.time()
        person = session.query(DimPerson).filter(DimPerson.person_id == person_id).first()
        end_time = time.time()

        if person:
            person_dict = {
                'person_id': person.person_id,
                'person_name': person.person_name,
                'image_url': person.image_url,
                'political_party': person.political_party,
                'attribute_1': person.attribute_1,
                'attribute_2': person.attribute_2,
                'attribute_3': person.attribute_3,
                'attribute_4': person.attribute_4
            }
            logger.info(f"Person found with person_id: {person_id}", extra={"execution_time": log.timeUsed(start_time, end_time)})
            return pd.Series(person_dict)
        else:
            logger.warning(f"Person with person_id {person_id} not found", extra={"execution_time": log.timeUsed(start_time, end_time)})
            return pd.Series()

def get_person_df(person_ids: list) -> pd.DataFrame:
    """Отримує особи за списком ID та повертає їх як DataFrame."""
    with _get_session() as session:
        start_time = time.time()
        persons = session.query(DimPerson).filter(DimPerson.person_id.in_(person_ids)).all()
        end_time = time.time()

        if persons:
            person_dicts = [{
                'person_id': person.person_id,
                'person_name': person.person_name,
                'image_url': person.image_url,
                'political_party': person.political_party,
                'attribute_1': person.attribute_1,
                'attribute_2': person.attribute_2,
                'attribute_3': person.attribute_3,
                'attribute_4': person.attribute_4
            } for person in persons]
            logger.info(f"Found {len(persons)} persons", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.DataFrame(person_dicts)
        else:
            logger.warning(f"No persons found for the provided person_ids", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.DataFrame()

def update_person(
    person_id: int,
    person_name: str = None,
    image_url: str = None,
    political_party: str = None,
    attribute_1: str = None,
    attribute_2: str = None,
    attribute_3: str = None,
    attribute_4: str = None
):
    """Оновлює існуючий запис у dimPerson, доповнюючи його новими даними."""
    with _get_session() as session:
        start_time = time.time()
        person = session.query(DimPerson).filter(DimPerson.person_id == int(person_id)).first()
        if not person:
            logger.warning(f"Person with person_id={int(person_id)} not found")
            return

        if person_name is not None:
            person.person_name = str(person_name)
        if image_url is not None:
            person.image_url = str(image_url)
        if political_party is not None:
            person.political_party = str(political_party)
        if attribute_1 is not None:
            person.attribute_1 = str(attribute_1)
        if attribute_2 is not None:
            person.attribute_2 = str(attribute_2)
        if attribute_3 is not None:
            person.attribute_3 = str(attribute_3)
        if attribute_4 is not None:
            person.attribute_4 = str(attribute_4)
        session.commit()
        end_time = time.time()
        logger.info(f"{int(person_id)} updated successfully", extra={'execution_time': log.timeUsed(start_time, end_time)})

def delete_person(person_id: int):
    """Видаляє запис з dimPerson за person_id."""
    with _get_session() as session:
        start_time = time.time()
        person = session.query(DimPerson).filter(DimPerson.person_id == person_id).first()
        if not person:
            logger.warning(f"Person with person_id={person_id} not found")
            return
        
        session.delete(person)
        session.commit()
        end_time = time.time()
        logger.info(f"Person with person_id={person_id} deleted successfully", extra={'execution_time': log.timeUsed(start_time, end_time)})

def delete_people(person_ids: list):
    """Видаляє записи з dimPerson за списком person_id."""
    with _get_session() as session:
        start_time = time.time()
        persons = session.query(DimPerson).filter(DimPerson.person_id.in_(person_ids)).all()
        if not persons:
            logger.warning(f"No persons found for the provided person_ids")

        for person in persons:
            session.delete(person)
        
        session.commit()
        end_time = time.time()
        logger.info(f"Persons with person_ids={person_ids} deleted successfully", extra={'execution_time': log.timeUsed(start_time, end_time)})

#
# DimOpinion
#

def find_opinion(fk_origin_article_id: int, fk_person_id: int, citation: str, get_id=True):
    """Searches for opinion_id in the database by fk_origin_article_id and fk_person_id."""
    with _get_session() as session:
        start_time = time.time()
        opinion = session.query(DimOpinion).filter(
            DimOpinion.fk_origin_article_id == fk_origin_article_id,
            DimOpinion.fk_person_id == fk_person_id,
            DimOpinion.citation == citation
        ).first()
        end_time = time.time()

        if opinion:
            logger.debug(f"Opinion already exists with opinion_id: {opinion.opinion_id}", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return opinion if not get_id else opinion.opinion_id # Повертаємо opinion, якщо знайдено
        else:
            logger.warning(f"Opinion not found", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return None  # Повертаємо None, якщо не знайдено

def find_opinion_by_conditions(processing_stage=-1, **conditions):
    """Отримує список opinion_id, що відповідають заданим умовам.\n
    processing_stage=-1 не виконує додаткового фільтрування\n
    processing_stage=0 відповідає повним сирим даним, без sentiment_score\n
    processing_stage=1 відповідає повним даним після sentiment analysis, без inconsistency\n
    processing_stage=2 відповідає повним даним після inconsistency detection, без score\n
    processing_stage=3 відповідає повністю оціненим даним\n
    """
    with _get_session() as session:
        start_time = time.time()
        query = session.query(DimOpinion.opinion_id)
        
        for attr, value in conditions.items():
            if isinstance(value, tuple) and len(value) == 2:
                # Handle range conditions (e.g., date range)
                query = query.filter(getattr(DimOpinion, attr).between(value[0], value[1]))
            else:
                # Handle equality conditions
                query = query.filter(getattr(DimOpinion, attr) == value)
        
        if processing_stage == 0:  # raw but complete data
            query = query.filter(
                DimOpinion.opinion_id.isnot(None),
                DimOpinion.fk_origin_article_id.isnot(None),
                DimOpinion.fk_person_id.isnot(None),
                DimOpinion.sentiment_score.is_(None)
            )
        elif processing_stage == 1:  # has sentiment, but not yet tested for inconsistencies
            query = query.filter(
                DimOpinion.sentiment_score.is_not(None),
                DimOpinion.inconsistency_flag.is_(None)
            )
        elif processing_stage == 2:  # has incosistencies, but not yet scored
            query = query.filter(
                DimOpinion.inconsistency_flag.isnot(None),
                and_(
                    DimOpinion.contribution_score.is_(None),
                    DimOpinion.controversy_score.is_(None),
                    DimOpinion.relevancy_score.is_(None),
                    DimOpinion.opinion_hotness.is_(None)
                )
            )
        elif processing_stage == 3:  # scored
            query = query.filter(
                DimOpinion.contribution_score.isnot(None),
                DimOpinion.controversy_score.isnot(None),
                DimOpinion.relevancy_score.isnot(None),
                DimOpinion.opinion_hotness.isnot(None)
            )
        
        opinion_ids = [opinion_id for opinion_id, in query.all()]
        end_time = time.time()
        
        if opinion_ids:
            logger.info(f"Found {len(opinion_ids)} opinions matching conditions", extra={'execution_time': log.timeUsed(start_time, end_time)})
        else:
            logger.warning(f"No opinions found matching conditions", extra={'execution_time': log.timeUsed(start_time, end_time)})
        
        return opinion_ids

def add_opinion(
        fk_origin_article_id: int,
        fk_person_id: int,
        citation: str,
        sentiment_score: float = None,
        inconsistency_flag: bool = None,
        inconsistency_with_id: int = None,
        inconsistency_comment: str = None,
        controversy_score: float = None,
        relevancy_score: float = None,
        contribution_score: float = None,
        opinion_hotness: float = None,
        is_selected: bool = None) -> int:
    """Додає новий запис у dimOpinion, якщо його ще немає, і повертає його opinion_id."""
    with _get_session() as session:
        start_time = time.time()
        existing_opinion = find_opinion(fk_origin_article_id, fk_person_id, citation, get_id=False)

        if existing_opinion: return existing_opinion.opinion_id

        new_opinion = DimOpinion(
            fk_origin_article_id=fk_origin_article_id,
            fk_person_id=fk_person_id,
            citation=citation,
            sentiment_score=sentiment_score,
            inconsistency_flag=bool(inconsistency_flag),
            inconsistency_with_id=inconsistency_with_id,
            inconsistency_comment=inconsistency_comment,
            controversy_score=controversy_score,
            relevancy_score=relevancy_score,
            contribution_score=contribution_score,
            opinion_hotness=opinion_hotness,
            is_selected=bool(is_selected)
        )
        session.add(new_opinion)
        session.commit()
        session.refresh(new_opinion)
        end_time = time.time()
        logger.info(f"Opinion added with opinion_id: {new_opinion.opinion_id}, citation: {citation}", extra={'execution_time': log.timeUsed(start_time, end_time)})
        return new_opinion.opinion_id

def add_opinion_full(
    fk_origin_article_id: int,
    fk_person_id: int,
    citation: str = None,
    sentiment_score: float = None,
    inconsistency_flag: bool = None,
    inconsistency_with_id: int = None,
    inconsistency_comment: str = None,
    controversy_score: float = None,
    relevancy_score: float = None,
    contribution_score: float = None,
    opinion_hotness: float = None,
    is_selected: bool = None
) -> int:
    """Додає новий запис у dimOpinion з можливістю встановлення всіх полів, якщо його ще немає, і повертає його opinion_id."""
    with _get_session() as session:
        start_time = time.time()
        new_opinion = DimOpinion(
            fk_origin_article_id=fk_origin_article_id,
            fk_person_id=fk_person_id,
            citation=citation,
            sentiment_score=sentiment_score,
            inconsistency_flag=bool(inconsistency_flag),
            inconsistency_with_id=inconsistency_with_id,
            inconsistency_comment=inconsistency_comment,
            controversy_score=controversy_score,
            relevancy_score=relevancy_score,
            contribution_score=contribution_score,
            opinion_hotness=opinion_hotness,
            is_selected=bool(is_selected)
        )
        session.add(new_opinion)
        session.commit()
        session.refresh(new_opinion)
        end_time = time.time()
        logger.info(f"Opinion added with opinion_id: {new_opinion.opinion_id}", extra={'execution_time': log.timeUsed(start_time, end_time)})
        return new_opinion.opinion_id

def add_opinion_df(df: pd.DataFrame):
    """Завантажує DataFrame у таблицю dimOpinion, перевіряючи наявність записів перед додаванням."""
    with _get_session() as session:
        start_time = time.time()
        records = df.to_dict(orient='records')
        added_count = 0

        for record in records:
            existing_opinion = find_opinion(record['fk_origin_article_id'], record['fk_person_id'], record['citation'], get_id=False)

            if not existing_opinion:
                new_opinion = DimOpinion(**record)
                session.add(new_opinion)
                added_count += 1

        session.commit()
        end_time = time.time()
        logger.info(f"Added {added_count} opinions", extra={'execution_time': log.timeUsed(start_time, end_time)})

def get_opinion(opinion_id: int) -> pd.Series:
    """Отримує думку за ID та повертає її як Series."""
    with _get_session() as session:
        start_time = time.time()
        opinion = session.query(DimOpinion).filter(DimOpinion.opinion_id == opinion_id).first()
        end_time = time.time()

        if opinion:
            opinion_dict = {
                'opinion_id': opinion.opinion_id,
                'fk_origin_article_id': opinion.fk_origin_article_id,
                'fk_person_id': opinion.fk_person_id,
                'citation': opinion.citation,
                'sentiment_score': opinion.sentiment_score,
                'inconsistency_flag': opinion.inconsistency_flag,
                'inconsistency_with_id': opinion.inconsistency_with_id,
                'inconsistency_comment': opinion.inconsistency_comment,
                'controversy_score': opinion.controversy_score,
                'relevancy_score': opinion.relevancy_score,
                'contribution_score': opinion.contribution_score,
                'opinion_hotness': opinion.opinion_hotness,
                'is_selected': opinion.is_selected,
                'created_at': opinion.created_at,
                'modified_at': opinion.modified_at
            }
            logger.info(f"Opinion found with opinion_id: {opinion_id}", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.Series(opinion_dict)
        else:
            logger.warning(f"Opinion with opinion_id {opinion_id} not found", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.Series()

def get_opinion_df(opinion_ids: list) -> pd.DataFrame:
    """Отримує думки за списком ID та повертає їх як DataFrame."""
    with _get_session() as session:
        start_time = time.time()
        opinions = session.query(DimOpinion).filter(DimOpinion.opinion_id.in_(opinion_ids)).all()
        end_time = time.time()

        if opinions:
            opinion_dicts = [{
                'opinion_id': opinion.opinion_id,
                'fk_origin_article_id': opinion.fk_origin_article_id,
                'fk_person_id': opinion.fk_person_id,
                'citation': opinion.citation,
                'sentiment_score': opinion.sentiment_score,
                'inconsistency_flag': opinion.inconsistency_flag,
                'inconsistency_with_id': opinion.inconsistency_with_id,
                'inconsistency_comment': opinion.inconsistency_comment,
                'controversy_score': opinion.controversy_score,
                'relevancy_score': opinion.relevancy_score,
                'contribution_score': opinion.contribution_score,
                'opinion_hotness': opinion.opinion_hotness,
                'is_selected': opinion.is_selected,
                'created_at': opinion.created_at,
                'modified_at': opinion.modified_at
            } for opinion in opinions]
            logger.info(f"Found {len(opinions)} opinions", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.DataFrame(opinion_dicts)
        else:
            logger.warning(f"No opinions found for the provided opinion_ids", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.DataFrame()

def update_opinion(
    opinion_id: int,
    fk_origin_article_id: int = None,
    fk_person_id: int = None,
    citation: str = None,
    sentiment_score: float = None,
    inconsistency_flag: bool = None,
    inconsistency_with_id: int = None,
    inconsistency_comment: str = None,
    controversy_score: float = None,
    relevancy_score: float = None,
    contribution_score: float = None,
    opinion_hotness: float = None,
    is_selected: bool = None
):
    """Оновлює існуючий запис у dimOpinion, доповнюючи його новими даними."""
    with _get_session() as session:
        start_time = time.time()
        opinion = session.query(DimOpinion).filter(DimOpinion.opinion_id == int(opinion_id)).first()
        if not opinion:
            logger.warning(f"Opinion with opinion_id={int(opinion_id)} not found")
            return

        if fk_origin_article_id is not None:
            opinion.fk_origin_article_id = int(fk_origin_article_id)
        if fk_person_id is not None:
            opinion.fk_person_id = int(fk_person_id)
        if citation is not None:
            opinion.citation = str(citation)
        if sentiment_score is not None:
            opinion.sentiment_score = float(sentiment_score)
        if inconsistency_flag is not None:
            opinion.inconsistency_flag = bool(inconsistency_flag)
        if inconsistency_with_id is not None:
            opinion.inconsistency_with_id = int(inconsistency_with_id)
        if inconsistency_comment is not None:
            opinion.inconsistency_comment = str(inconsistency_comment)
        if controversy_score is not None:
            opinion.controversy_score = float(controversy_score)
        if relevancy_score is not None:
            opinion.relevancy_score = float(relevancy_score)
        if contribution_score is not None:
            opinion.contribution_score = float(contribution_score)
        if opinion_hotness is not None:
            opinion.opinion_hotness = float(opinion_hotness)
        if is_selected is not None:
            opinion.is_selected = bool(is_selected)
        session.commit()
        end_time = time.time()
        logger.info(f"{int(opinion_id)} updated successfully", extra={'execution_time': log.timeUsed(start_time, end_time)})

def delete_opinion(opinion_id: int):
    """Видаляє запис з dimOpinion за opinion_id."""
    with _get_session() as session:
        start_time = time.time()
        opinion = session.query(DimOpinion).filter(DimOpinion.opinion_id == opinion_id).first()
        if not opinion:
            logger.warning(f"Opinion with opinion_id={opinion_id} not found")
            return
        
        session.delete(opinion)
        session.commit()
        end_time = time.time()
        logger.info(f"Opinion with opinion_id={opinion_id} deleted successfully", extra={'execution_time': log.timeUsed(start_time, end_time)})

def delete_opinions(opinion_ids: list):
    """Видаляє записи з dimOpinion за списком opinion_id."""
    with _get_session() as session:
        start_time = time.time()
        opinions = session.query(DimOpinion).filter(DimOpinion.opinion_id.in_(opinion_ids)).all()
        if not opinions:
            logger.warning(f"No opinions found for the provided opinion_ids")

        for opinion in opinions:
            session.delete(opinion)
        
        session.commit()
        end_time = time.time()
        logger.info(f"Opinions with opinion_ids={opinion_ids} deleted successfully", extra={'execution_time': log.timeUsed(start_time, end_time)})

# 
# dimArticle
# 

def find_article(title: str, article_date: datetime, get_id=True):
    """Searches for article_id in the database by title and article_date."""
    with _get_session() as session:
        start_time = time.time()
        article = session.query(DimArticle).filter(
            DimArticle.title == title,
            DimArticle.article_date == article_date
        ).first()
        end_time = time.time()

        if article:
            logger.debug(f"Article already exists with article_id: {article.article_id}", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return article if not get_id else article.article_id # Повертаємо article, якщо знайдено
        else:
            logger.warning(f"Article not found", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return None  # Повертаємо None, якщо не знайдено

def find_article_by_conditions(**conditions):
    """Отримує список article_id, що відповідають заданим умовам."""
    with _get_session() as session:
        start_time = time.time()
        query = session.query(DimArticle.article_id)
        
        for attr, value in conditions.items():
            if isinstance(value, tuple) and len(value) == 2:
                # Handle range conditions (e.g., date range)
                query = query.filter(getattr(DimArticle, attr).between(value[0], value[1]))
            else:
                # Handle equality conditions
                query = query.filter(getattr(DimArticle, attr) == value)
        
        article_ids = [article_id for article_id, in query.all()]
        end_time = time.time()
        
        if article_ids:
            logger.info(f"Found {len(article_ids)} articles matching conditions", extra={'execution_time': log.timeUsed(start_time, end_time)})
        else:
            logger.warning(f"No articles found matching conditions", extra={'execution_time': log.timeUsed(start_time, end_time)})
        
        return article_ids

def add_article(
    title: str,
    fk_topic_id: int,
    url: str,
    article_date: str = None,
    content: str = None
) -> int:
    """Додає новий запис у dimArticle, якщо його ще немає, і повертає його article_id."""
    with _get_session() as session:
        start_time = time.time()
        new_article = DimArticle(
            title=title,
            fk_topic_id=fk_topic_id,
            url=url,
            article_date=article_date,
            content=content
        )
        session.add(new_article)
        session.commit()
        session.refresh(new_article)
        end_time = time.time()
        logger.debug(f"Article added with article_id: {new_article.article_id}", extra={'execution_time': log.timeUsed(start_time, end_time)})
        return new_article.article_id

def add_article_full(
    title: str,
    fk_topic_id: int,
    url: str,
    article_date: str = None,
    content: str = None
) -> int:
    """Додає новий запис у dimArticle з можливістю встановлення всіх полів, якщо його ще немає, і повертає його article_id."""
    with _get_session() as session:
        start_time = time.time()
        new_article = DimArticle(
            title=title,
            fk_topic_id=fk_topic_id,
            url=url,
            article_date=article_date,
            content=content
        )
        session.add(new_article)
        session.commit()
        session.refresh(new_article)
        end_time = time.time()
        logger.debug(f"Article added with article_id: {new_article.article_id}", extra={'execution_time': log.timeUsed(start_time, end_time)})
        return new_article.article_id

def add_article_df(df: pd.DataFrame):
    """Завантажує DataFrame у таблицю dimArticle, перевіряючи наявність записів перед додаванням."""
    with _get_session() as session:
        start_time = time.time()
        records = df.to_dict(orient='records')
        added_count = 0

        for record in records:
            # Перевіряємо, чи існує вже стаття з таким title та url
            existing_article = session.query(DimArticle).filter(
                DimArticle.title == record['title'],
                DimArticle.url == record['url']
            ).first()

            if not existing_article:
                new_article = DimArticle(**record)
                session.add(new_article)
                added_count += 1

        session.commit()
        end_time = time.time()
        logger.info(f"Added {added_count} articles", extra={'execution_time': log.timeUsed(start_time, end_time)})

def get_article(article_id: int) -> pd.Series:
    """Отримує статтю за ID та повертає її як Series."""
    with _get_session() as session:
        start_time = time.time()
        article = session.query(DimArticle).filter(DimArticle.article_id == article_id).first()
        end_time = time.time()

        if article:
            article_dict = {
                'article_id': article.article_id,
                'title': article.title,
                'fk_topic_id': article.fk_topic_id,
                'url': article.url,
                'article_date': article.article_date,
                'created_at': article.created_at,
                'modified_at': article.modified_at,
                'content': article.content
            }
            logger.debug(f"Article found with article_id: {article_id}", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.Series(article_dict)
        else:
            logger.warning(f"Article with article_id {article_id} not found", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.Series()

def get_article_df(article_ids: list) -> pd.DataFrame:
    """Отримує статті за списком ID та повертає їх як DataFrame."""
    with _get_session() as session:
        start_time = time.time()
        articles = session.query(DimArticle).filter(DimArticle.article_id.in_(article_ids)).all()
        end_time = time.time()

        if articles:
            article_dicts = [{
                'article_id': article.article_id,
                'title': article.title,
                'fk_topic_id': article.fk_topic_id,
                'url': article.url,
                'article_date': article.article_date,
                'created_at': article.created_at,
                'modified_at': article.modified_at,
                'content': article.content
            } for article in articles]
            logger.info(f"Found {len(articles)} articles", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.DataFrame(article_dicts)
        else:
            logger.warning(f"No articles found for the provided article_ids", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.DataFrame()

def update_article(
    article_id: int,
    title: str = None,
    fk_topic_id: int = None,
    url: str = None,
    article_date: str = None,
    content: str = None
):
    """Оновлює існуючий запис у dimArticle, доповнюючи його новими даними."""
    with _get_session() as session:
        start_time = time.time()
        article = session.query(DimArticle).filter(DimArticle.article_id == int(article_id)).first()
        if not article:
            logger.warning(f"Article with article_id={int(article_id)} not found")
            return

        # Оновлення лише переданих параметрів
        if title is not None:
            article.title = str(title)
        if fk_topic_id is not None:
            article.fk_topic_id = int(fk_topic_id)
        if url is not None:
            article.url = str(url)
        if article_date is not None:
            article.article_date = str(article_date)
        if content is not None:
            article.content = str(content)
        end_time = time.time()
        logger.debug(f"{int(article_id)} updated successfully", extra={'execution_time': log.timeUsed(start_time, end_time)})

def delete_article(article_id: int):
    """Видаляє запис з dimArticle за article_id."""
    with _get_session() as session:
        start_time = time.time()
        article = session.query(DimArticle).filter(DimArticle.article_id == article_id).first()
        if not article:
            logger.warning(f"Article with article_id={article_id} not found")
            return
        
        session.delete(article)
        session.commit()
        end_time = time.time()
        logger.debug(f"Article with article_id={article_id} deleted successfully", extra={'execution_time': log.timeUsed(start_time, end_time)})

def delete_articles(article_ids: list):
    """Видаляє записи з dimArticle за списком article_id."""
    with _get_session() as session:
        start_time = time.time()
        articles = session.query(DimArticle).filter(DimArticle.article_id.in_(article_ids)).all()
        if not articles:
            logger.warning(f"No articles found for the provided article_ids")

        for article in articles:
            session.delete(article)
        
        session.commit()
        end_time = time.time()
        logger.info(f"Articles with article_ids={article_ids} deleted successfully", extra={'execution_time': log.timeUsed(start_time, end_time)})

#
# dimTopic
#

def find_topic(topic_name: str, get_id=True):
    """Searches for topic_id in the database by topic_name."""
    with _get_session() as session:
        start_time = time.time()
        topic = session.query(DimTopic).filter(DimTopic.topic_name == topic_name).first()
        end_time = time.time()

        if topic:
            logger.debug(f"Topic already exists with topic_id: {topic.topic_id}", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return topic if not get_id else topic.topic_id # Повертаємо topic, якщо знайдено
        else:
            logger.warning(f"Topic not found", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return None  # Повертаємо None, якщо не знайдено

def find_topic_by_conditions(**conditions):
    """Отримує список topic_id, що відповідають заданим умовам."""
    with _get_session() as session:
        start_time = time.time()
        query = session.query(DimTopic.topic_id)
        
        for attr, value in conditions.items():
            if isinstance(value, tuple) and len(value) == 2:
                # Handle range conditions (e.g., date range)
                query = query.filter(getattr(DimTopic, attr).between(value[0], value[1]))
            else:
                # Handle equality conditions
                query = query.filter(getattr(DimTopic, attr) == value)
        
        topic_ids = [topic_id for topic_id, in query.all()]
        end_time = time.time()
        
        if topic_ids:
            logger.info(f"Found {len(topic_ids)} topics matching conditions", extra={'execution_time': log.timeUsed(start_time, end_time)})
        else:
            logger.warning(f"No topics found matching conditions", extra={'execution_time': log.timeUsed(start_time, end_time)})
        
        return topic_ids

def add_topic(topic_name: str, query: str, source: str = None) -> int:
    """Додає новий запис у dimTopic, якщо його ще немає, і повертає його topic_id."""
    with _get_session() as session:
        start_time = time.time()
        new_topic = DimTopic(
            topic_name=topic_name,
            query=query,
            source=source
        )
        session.add(new_topic)
        session.commit()
        session.refresh(new_topic)
        end_time = time.time()
        logger.info(f"Topic added with topic_id: {new_topic.topic_id}", extra={'execution_time': log.timeUsed(start_time, end_time)})
        return new_topic.topic_id

def add_topic_full(topic_name: str, query: str, source: str = None) -> int:
    """Додає новий запис у dimTopic з можливістю встановлення всіх полів, якщо його ще немає, і повертає його topic_id."""
    with _get_session() as session:
        start_time = time.time()
        new_topic = DimTopic(
            topic_name=topic_name,
            query=query,
            source=source
        )
        session.add(new_topic)
        session.commit()
        session.refresh(new_topic)
        end_time = time.time()
        logger.info(f"Topic added with topic_id: {new_topic.topic_id}", extra={'execution_time': log.timeUsed(start_time, end_time)})
        return new_topic.topic_id

def add_topic_df(df: pd.DataFrame):
    """Завантажує DataFrame у таблицю dimTopic, перевіряючи наявність записів перед додаванням."""
    with _get_session() as session:
        start_time = time.time()
        records = df.to_dict(orient='records')
        added_count = 0

        for record in records:
            existing_topic = session.query(DimTopic).filter(
                DimTopic.topic_name == record['topic_name']
            ).first()

            if not existing_topic:
                new_topic = DimTopic(**record)
                session.add(new_topic)
                added_count += 1

        session.commit()
        end_time = time.time()
        logger.info(f"Added {added_count} topics", extra={'execution_time': log.timeUsed(start_time, end_time)})

def get_topic(topic_id: int) -> pd.Series:
    """Отримує тему за ID та повертає її як Series."""
    with _get_session() as session:
        start_time = time.time()
        topic = session.query(DimTopic).filter(DimTopic.topic_id == topic_id).first()
        end_time = time.time()

        if topic:
            topic_dict = {
                'topic_id': topic.topic_id,
                'topic_name': topic.topic_name,
                'query': topic.query,
                'source': topic.source
            }
            logger.debug(f"Topic found with topic_id: {topic_id}", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.Series(topic_dict)
        else:
            logger.warning(f"Topic with topic_id {topic_id} not found", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.Series()

def get_topic_df(topic_ids: list) -> pd.DataFrame:
    """Отримує теми за списком ID та повертає їх як DataFrame."""
    with _get_session() as session:
        start_time = time.time()
        topics = session.query(DimTopic).filter(DimTopic.topic_id.in_(topic_ids)).all()
        end_time = time.time()

        if topics:
            topic_dicts = [{
                'topic_id': topic.topic_id,
                'topic_name': topic.topic_name,
                'query': topic.query,
                'source': topic.source
            } for topic in topics]
            logger.info(f"Found {len(topics)} topics", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.DataFrame(topic_dicts)
        else:
            logger.warning(f"No topics found for the provided topic_ids", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.DataFrame()

def update_topic(
    topic_id: int,
    topic_name: str = None,
    query: str = None,
    source: str = None
):
    """Оновлює існуючий запис у dimTopic, доповнюючи його новими даними."""
    with _get_session() as session:
        start_time = time.time()
        topic = session.query(DimTopic).filter(DimTopic.topic_id == int(topic_id)).first()
        if not topic:
            logger.warning(f"Topic with topic_id={int(topic_id)} not found")
            return

        # Оновлення лише переданих параметрів
        if topic_name is not None:
            topic.topic_name = str(topic_name)
        if query is not None:
            topic.query = str(query)
        if source is not None:
            topic.source = str(source)
        end_time = time.time()
        logger.info(f"{int(topic_id)} updated successfully", extra={'execution_time': log.timeUsed(start_time, end_time)})

def delete_topic(topic_id: int):
    """Видаляє запис з dimTopic за topic_id."""
    with _get_session() as session:
        start_time = time.time()
        topic = session.query(DimTopic).filter(DimTopic.topic_id == topic_id).first()
        if not topic:
            logger.warning(f"Topic with topic_id={topic_id} not found")
        
        session.delete(topic)
        session.commit()
        end_time = time.time()
        logger.info(f"Topic with topic_id={topic_id} deleted successfully", extra={'execution_time': log.timeUsed(start_time, end_time)})

def delete_topics(topic_ids: list):
    """Видаляє записи з dimTopic за списком topic_id."""
    with _get_session() as session:
        start_time = time.time()
        topics = session.query(DimTopic).filter(DimTopic.topic_id.in_(topic_ids)).all()
        if not topics:
            logger.warning(f"No topics found for the provided topic_ids")

        for topic in topics:
            session.delete(topic)
        
        session.commit()
        end_time = time.time()
        logger.info(f"Topics with topic_ids={topic_ids} deleted successfully", extra={'execution_time': log.timeUsed(start_time, end_time)})

# 
# fctAttitude
# 

def find_attitude(fk_topic_id: int, fk_person_id: int):
    """Searches for a record in the database by fk_topic_id and fk_person_id."""
    with _get_session() as session:
        start_time = time.time()
        attitude = session.query(FctAttitude).filter(
            FctAttitude.fk_topic_id == fk_topic_id,
            FctAttitude.fk_person_id == fk_person_id
        ).first()
        end_time = time.time()

        if attitude:
            logger.debug(f"Attitude already exists with fk_topic_id: {fk_topic_id} and fk_person_id: {fk_person_id}", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return {'fk_topic_id': attitude.fk_topic_id, 'fk_person_id': attitude.fk_person_id}  # Повертаємо attitude, якщо знайдено
        else:
            logger.warning(f"Attitude not found", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return None  # Повертаємо None, якщо не знайдено

def find_attitude_by_conditions(**conditions):
    """Отримує список записів, що відповідають заданим умовам."""
    with _get_session() as session:
        start_time = time.time()
        query = session.query(FctAttitude.fk_topic_id, FctAttitude.fk_person_id)
        
        for attr, value in conditions.items():
            if isinstance(value, tuple) and len(value) == 2:
                # Handle range conditions (e.g., date range)
                query = query.filter(getattr(FctAttitude, attr).between(value[0], value[1]))
            else:
                # Handle equality conditions
                query = query.filter(getattr(FctAttitude, attr) == value)
        
        attitudes = query.all()
        end_time = time.time()
        
        if attitudes:
            logger.info(f"Found {len(attitudes)} attitudes matching conditions", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return [{'fk_topic_id': att[0], 'fk_person_id': att[1]} for att in attitudes]
        else:
            logger.warning(f"No attitudes found matching conditions", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return []

def add_attitude(fk_topic_id: int, fk_person_id: int, sentiment_deviation: float = None, stance: str = None, person_summary: str = None, is_expert_flag: bool = None) -> None:
    """Додає новий запис у fctAttitude, якщо його ще немає."""
    with _get_session() as session:
        start_time = time.time()
        existing_attitude = find_attitude(fk_topic_id, fk_person_id)
        
        if existing_attitude:
            logger.warning(f"Attitude already exists with fk_topic_id: {fk_topic_id} and fk_person_id: {fk_person_id}", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return

        new_attitude = FctAttitude(
            fk_topic_id=fk_topic_id,
            fk_person_id=fk_person_id,
            sentiment_deviation=sentiment_deviation,
            stance=stance,
            person_summary=person_summary,
            is_expert_flag=bool(is_expert_flag)
        )
        session.add(new_attitude)
        session.commit()
        end_time = time.time()
        logger.info(f"Attitude added with fk_topic_id: {fk_topic_id} and fk_person_id: {fk_person_id}", extra={'execution_time': log.timeUsed(start_time, end_time)})

def add_attitude_full(fk_topic_id: int, fk_person_id: int, sentiment_deviation: float = None, stance: str = None, person_summary: str = None, is_expert_flag: bool = None) -> None:
    """Додає новий запис у fctAttitude з можливістю встановлення всіх полів, якщо його ще немає."""
    with _get_session() as session:
        start_time = time.time()
        existing_attitude = find_attitude(fk_topic_id, fk_person_id)
        
        if existing_attitude:
            logger.warning(f"Attitude already exists with fk_topic_id: {fk_topic_id} and fk_person_id: {fk_person_id}", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return

        new_attitude = FctAttitude(
            fk_topic_id=fk_topic_id,
            fk_person_id=fk_person_id,
            sentiment_deviation=sentiment_deviation,
            stance=stance,
            person_summary=person_summary,
            is_expert_flag=bool(is_expert_flag)
        )
        session.add(new_attitude)
        session.commit()
        end_time = time.time()
        logger.info(f"Attitude added with fk_topic_id: {fk_topic_id} and fk_person_id: {fk_person_id}", extra={'execution_time': log.timeUsed(start_time, end_time)})

def add_attitude_df(df: pd.DataFrame):
    """Завантажує DataFrame у таблицю fctAttitude, перевіряючи наявність записів перед додаванням."""
    with _get_session() as session:
        start_time = time.time()
        records = df.to_dict(orient='records')
        added_count = 0

        for record in records:
            existing_attitude = session.query(FctAttitude).filter(
                FctAttitude.fk_topic_id == record['fk_topic_id'],
                FctAttitude.fk_person_id == record['fk_person_id']
            ).first()

            if not existing_attitude:
                new_attitude = FctAttitude(**record)
                session.add(new_attitude)
                added_count += 1
                print(record)

        session.commit()
        end_time = time.time()
        logger.info(f"Added {added_count} attitudes", extra={'execution_time': log.timeUsed(start_time, end_time)})

def get_attitude(fk_topic_id: int, fk_person_id: int) -> pd.Series:
    """Отримує запис за fk_topic_id та fk_person_id та повертає його як Series."""
    with _get_session() as session:
        start_time = time.time()
        attitude = session.query(FctAttitude).filter(
            FctAttitude.fk_topic_id == fk_topic_id,
            FctAttitude.fk_person_id == fk_person_id
        ).first()
        end_time = time.time()

        if attitude:
            attitude_dict = {
                'fk_topic_id': attitude.fk_topic_id,
                'fk_person_id': attitude.fk_person_id,
                'sentiment_deviation': attitude.sentiment_deviation,
                'stance': attitude.stance,
                'person_summary': attitude.person_summary,
                'is_expert_flag': attitude.is_expert_flag,
                'created_at': attitude.created_at,
                'modified_at': attitude.modified_at
            }
            logger.info(f"Attitude found with fk_topic_id: {fk_topic_id} and fk_person_id: {fk_person_id}", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.Series(attitude_dict)
        else:
            logger.warning(f"Attitude with fk_topic_id {fk_topic_id} and fk_person_id {fk_person_id} not found", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.Series()

def get_attitude_df(attitude_ids: list) -> pd.DataFrame:
    """Отримує записи за списком пар (fk_topic_id, fk_person_id) та повертає їх як DataFrame."""
    with _get_session() as session:
        start_time = time.time()
        attitudes = session.query(FctAttitude).filter(
            tuple_(FctAttitude.fk_topic_id, FctAttitude.fk_person_id).in_(attitude_ids)
        ).all()
        end_time = time.time()

        if attitudes:
            attitude_dicts = [{
                'fk_topic_id': attitude.fk_topic_id,
                'fk_person_id': attitude.fk_person_id,
                'sentiment_deviation': attitude.sentiment_deviation,
                'stance': attitude.stance,
                'person_summary': attitude.person_summary,
                'is_expert_flag': attitude.is_expert_flag,
                'created_at': attitude.created_at,
                'modified_at': attitude.modified_at
            } for attitude in attitudes]
            logger.info(f"Found {len(attitudes)} attitudes", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.DataFrame(attitude_dicts)
        else:
            logger.warning(f"No attitudes found for the provided attitude_ids", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.DataFrame()

def update_attitude(
    fk_topic_id: int,
    fk_person_id: int,
    sentiment_deviation: float = None,
    stance: str = None,
    person_summary: str = None,
    is_expert_flag: bool = None
):
    """Оновлює існуючий запис у fctAttitude, доповнюючи його новими даними."""
    with _get_session() as session:
        start_time = time.time()
        attitude = session.query(FctAttitude).filter(
            FctAttitude.fk_topic_id == int(fk_topic_id),
            FctAttitude.fk_person_id == int(fk_person_id)
        ).first()
        if not attitude:
            logger.warning(f"Attitude with fk_topic_id={int(fk_topic_id)} and fk_person_id={int(fk_person_id)} not found")
            return

        # Оновлення лише переданих параметрів
        if sentiment_deviation is not None:
            attitude.sentiment_deviation = float(sentiment_deviation)
        if stance is not None:
            attitude.stance = str(stance)
        if person_summary is not None:
            attitude.person_summary = str(person_summary)
        if is_expert_flag is not None:
            attitude.is_expert_flag = bool(is_expert_flag)
        end_time = time.time()
        logger.info(f"Attitude with fk_topic_id={int(fk_topic_id)} and fk_person_id={int(fk_person_id)} updated successfully", extra={'execution_time': log.timeUsed(start_time, end_time)})

def delete_attitude(fk_topic_id: int, fk_person_id: int):
    """Видаляє запис з fctAttitude за fk_topic_id та fk_person_id."""
    with _get_session() as session:
        start_time = time.time()
        attitude = session.query(FctAttitude).filter(
            FctAttitude.fk_topic_id == fk_topic_id,
            FctAttitude.fk_person_id == fk_person_id
        ).first()
        if not attitude:
            logger.warning(f"Attitude with fk_topic_id={fk_topic_id} and fk_person_id={fk_person_id} not found")
            return
        
        session.delete(attitude)
        session.commit()
        end_time = time.time()
        logger.info(f"Attitude with fk_topic_id={fk_topic_id} and fk_person_id={fk_person_id} deleted successfully", extra={'execution_time': log.timeUsed(start_time, end_time)})

def delete_attitudes(attitude_ids: list):
    """Видаляє записи з fctAttitude за списком пар (fk_topic_id, fk_person_id)."""
    with _get_session() as session:
        start_time = time.time()
        attitudes = session.query(FctAttitude).filter(
            tuple_(FctAttitude.fk_topic_id, FctAttitude.fk_person_id).in_(attitude_ids)
        ).all()
        if not attitudes:
            logger.warning(f"No attitudes found for the provided attitude_ids")

        for attitude in attitudes:
            session.delete(attitude)
        
        session.commit()
        end_time = time.time()
        logger.info(f"Attitudes with attitude_ids={attitude_ids} deleted successfully", extra={'execution_time': log.timeUsed(start_time, end_time)})

# 
# dimArticleChunks
#

def find_article_chunk(fk_article_id: int):
    """Searches for a record in the database by fk_article_id."""
    with _get_session() as session:
        start_time = time.time()
        article_chunk = session.query(DimArticleChunks).filter(
            DimArticleChunks.fk_article_id == fk_article_id
        ).first()
        end_time = time.time()

        if article_chunk:
            logger.debug(f"Article chunk already exists with fk_article_id: {fk_article_id}.", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return article_chunk  # Повертаємо article_chunk, якщо знайдено
        else:
            logger.warning(f"Article chunk not found", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return None  # Повертаємо None, якщо не знайдено

def find_article_chunk_by_conditions(**conditions):
    """Отримує список chunk_id, що відповідають заданим умовам."""
    with _get_session() as session:
        start_time = time.time()
        query = session.query(DimArticleChunks.chunk_id)
        
        for attr, value in conditions.items():
            if isinstance(value, tuple) and len(value) == 2:
                # Handle range conditions (e.g., date range)
                query = query.filter(getattr(DimArticleChunks, attr).between(value[0], value[1]))
            else:
                # Handle equality conditions
                query = query.filter(getattr(DimArticleChunks, attr) == value)
        
        chunk_ids = [chunk_id for chunk_id, in query.all()]
        end_time = time.time()
        
        if chunk_ids:
            logger.info(f"Found {len(chunk_ids)} article chunks matching conditions", extra={'execution_time': log.timeUsed(start_time, end_time)})
        else:
            logger.warning(f"No article chunks found matching conditions", extra={'execution_time': log.timeUsed(start_time, end_time)})
        
        return chunk_ids

def add_article_chunk(fk_article_id: int, start_index: int, end_index: int, is_processed: bool = None) -> None:
    """Додає новий запис у dimArticleChunks, якщо його ще немає."""
    with _get_session() as session:
        start_time = time.time()
        new_article_chunk = DimArticleChunks(
            fk_article_id=fk_article_id,
            start_index=start_index,
            end_index=end_index,
            is_processed=bool(is_processed)
        )
        session.add(new_article_chunk)
        session.commit()
        end_time = time.time()
        logger.debug(f"Article chunk added with fk_article_id: {fk_article_id}, start_index: {start_index}, and end_index: {end_index}", extra={'execution_time': log.timeUsed(start_time, end_time)})

def add_article_chunk_df(df: pd.DataFrame):
    """Завантажує DataFrame у таблицю dimArticleChunks, перевіряючи наявність записів перед додаванням."""
    with _get_session() as session:
        start_time = time.time()
        records = df.to_dict(orient='records')
        added_count = 0

        for record in tqdm(records, desc='Uploading: ', unit='chunk', disable=log.LOGGING_LEVEL != DEBUG):
            existing_article_chunk = session.query(DimArticleChunks).filter(
                DimArticleChunks.fk_article_id == record['fk_article_id'],
                DimArticleChunks.start_index == record['start_index'],
                DimArticleChunks.end_index == record['end_index']
            ).first()

            if not existing_article_chunk:
                new_article_chunk = DimArticleChunks(**record)
                session.add(new_article_chunk)
                added_count += 1

        session.commit()
        end_time = time.time()
        logger.info(f"Added {added_count} article chunks", extra={'execution_time': log.timeUsed(start_time, end_time)})

def get_article_chunk(chunk_id: int) -> pd.Series:
    """Отримує запис за fk_article_id та повертає його як Series."""
    with _get_session() as session:
        start_time = time.time()
        article_chunk = session.query(DimArticleChunks).filter(
            DimArticleChunks.chunk_id == chunk_id
        ).first()
        end_time = time.time()

        if article_chunk:
            article_chunk_dict = {
                'chunk_id': article_chunk.chunk_id,
                'fk_article_id': article_chunk.fk_article_id,
                'start_index': article_chunk.start_index,
                'end_index': article_chunk.end_index,
                'is_processed': article_chunk.is_processed
            }
            logger.debug(f"Article chunk found with chunk_id: {chunk_id}", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.Series(article_chunk_dict)
        else:
            logger.warning(f"Article chunk with chunk_id {chunk_id} not found", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.Series()

def get_article_chunk_df(article_chunk_ids: list) -> pd.DataFrame:
    """Отримує записи за списком chunk_id та повертає їх як DataFrame."""
    with _get_session() as session:
        start_time = time.time()
        article_chunks = session.query(DimArticleChunks).filter(
            DimArticleChunks.chunk_id.in_(article_chunk_ids)
        ).all()
        end_time = time.time()

        if article_chunks:
            article_chunk_dicts = [{
                'chunk_id': article_chunk.chunk_id,
                'fk_article_id': article_chunk.fk_article_id,
                'start_index': article_chunk.start_index,
                'end_index': article_chunk.end_index,
                'is_processed': article_chunk.is_processed
            } for article_chunk in article_chunks]
            logger.info(f"Found {len(article_chunks)} article chunks", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.DataFrame(article_chunk_dicts)
        else:
            logger.warning(f"No article chunks found for the provided article_chunk_ids", extra={'execution_time': log.timeUsed(start_time, end_time)})
            return pd.DataFrame()

def update_article_chunk(
    chunk_id: int,
    new_start_index: int = None,
    new_end_index: int = None,
    is_processed: bool = None
):
    """Оновлює існуючий запис у dimArticleChunks, доповнюючи його новими даними."""
    with _get_session() as session:
        start_time = time.time()
        article_chunk = session.query(DimArticleChunks).filter(
            DimArticleChunks.chunk_id == int(chunk_id)
        ).first()
        if not article_chunk:
            logger.warning(f"Article chunk with chunk_id={int(chunk_id)} not found")
            return

        # Оновлення лише переданих параметрів
        if new_start_index is not None:
            article_chunk.start_index = int(new_start_index)
        if new_end_index is not None:
            article_chunk.end_index = int(new_end_index)
        if is_processed is not None:
            article_chunk.is_processed = bool(is_processed)
        end_time = time.time()
        logger.info(f"Article chunk with chunk_id={int(chunk_id)} updated successfully", extra={'execution_time': log.timeUsed(start_time, end_time)})

def delete_article_chunk(chunk_id: int):
    """Видаляє запис з dimArticleChunks за chunk_id."""
    with _get_session() as session:
        start_time = time.time()
        article_chunk = session.query(DimArticleChunks).filter(
            DimArticleChunks.chunk_id == chunk_id
        ).first()
        if not article_chunk:
            logger.warning(f"Article chunk with chunk_id={chunk_id} not found")
            return
        
        session.delete(article_chunk)
        session.commit()
        end_time = time.time()
        logger.debug(f"Article chunk with chunk_id={chunk_id} deleted successfully", extra={'execution_time': log.timeUsed(start_time, end_time)})

def delete_article_chunks(article_chunk_ids: list):
    """Видаляє записи з dimArticleChunks за списком chunk_id."""
    with _get_session() as session:
        start_time = time.time()
        article_chunks = session.query(DimArticleChunks).filter(
            DimArticleChunks.chunk_id.in_(article_chunk_ids)
        ).all()
        if not article_chunks:
            logger.warning(f"No article chunks found for the provided article_chunk_ids")

        for article_chunk in article_chunks:
            session.delete(article_chunk)
        
        session.commit()
        end_time = time.time()
        logger.info(f"Article chunks with article_chunk_ids={article_chunk_ids} deleted successfully", extra={'execution_time': log.timeUsed(start_time, end_time)})

