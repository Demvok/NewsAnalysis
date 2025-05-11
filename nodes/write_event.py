from database.DBConnector import add_event_full, add_opinion_full, add_person, add_attitude_full, update_article_chunk
from utils.logger import setup_logger
from config import EVENTS_HOTNESS_THRESHOLD, OPINION_HOTNESS_THRESHOLD

# Initialize logger
logger = setup_logger(name="write_node", log_file="graph.log")

def main(state):
    logger.debug('Write events node reached')
    if state.origin_article_id is not None:
        origin_article_id = state.origin_article_id
    else:
        raise ValueError("origin_article_id is missing in the state.")

    # Process general events
    if state.general_event is not None:
        for event in state.general_event:
            title = event.get('title')  # Actually, this is not used in the DB, so it can be deleted from prompt
            description = event.get('description')

            # Skip if required fields are missing
            if description is None:
                logger.debug(f"(Stage -1) Skipping general event due to missing description: {event}")
                continue

            if event.get('event_hotness') is None:  # Unscored event case
                logger.debug('(Stage 0) Unscored event case')
                add_event_full(
                    fk_origin_article_id=origin_article_id,
                    description=description,
                    event_title=title
                )
            else:
                logger.debug('(Stage 1) Scored event case')
                relevance_score = event.get('relevance_score', None)  # Default to None if not provided
                influence_score = event.get('influence_score', None)
                novelty_score = event.get('novelty_score', None)
                event_hotness = event.get('event_hotness', None)

                is_selected = 0
                if event_hotness >= EVENTS_HOTNESS_THRESHOLD:
                    is_selected = 1

                add_event_full(
                    fk_origin_article_id=origin_article_id,
                    description=description,
                    event_title=title,
                    relevance_score=relevance_score,
                    influence_score=influence_score,
                    novelty_score=novelty_score,
                    event_hotness=event_hotness,
                    is_selected=is_selected
                )

    # Process person events
    if state.person_event is not None:
        for event in state.person_event:
            person_name = event.get('person_name')  # Actually, this is not used in the DB, so it can be deleted from prompt
            citation = event.get('citation')
            fk_person_id = add_person(person_name=person_name) if person_name is not None else None

            # Skip if required fields are missing
            if person_name is None or citation is None:
                logger.warning(f"(Stage -1) Skipping person event due to missing fields: {event}")
                continue
            if event.get('sentiment') is None:    # Unanalysed event case
                logger.debug('(Stage 0) No sentiment case')
                add_opinion_full(
                    fk_origin_article_id=origin_article_id,
                    fk_person_id=fk_person_id,
                    citation=citation
                )
            elif event.get('inconsistency_flag') is None:  # No inconsistency flag, only sentiment present
                logger.debug('(Stage 1) No inconsistency case')

                sentiment = event.get('sentiment')
                
                add_opinion_full(
                    fk_origin_article_id=origin_article_id,
                    fk_person_id=fk_person_id,
                    citation=citation,
                    sentiment_score=sentiment
                )
            elif event.get('opinion_hotness') is None:  # No opinion hotness, only inconsistency flag present
                logger.debug('(Stage 2) No score case')

                sentiment = event.get('sentiment')

                inconsistency_flag = event.get('inconsistency_flag')
                inconsistency_with_id = event.get('inconsistency_with_id')
                inconsistency_comment = event.get('inconsistency_comment')

                add_opinion_full(
                    fk_origin_article_id=origin_article_id,
                    fk_person_id=fk_person_id,
                    citation=citation,
                    sentiment_score=sentiment,
                    inconsistency_flag=inconsistency_flag,
                    inconsistency_with_id=inconsistency_with_id,
                    inconsistency_comment=inconsistency_comment
                )
            elif event.get('opinion_hotness') is not None: # Scored case
                logger.debug('(Stage 3) Scored case')

                sentiment = event.get('sentiment')

                inconsistency_flag = event.get('inconsistency_flag')
                inconsistency_with_id = event.get('inconsistency_with_id')
                inconsistency_comment = event.get('inconsistency_comment')

                controversy_score = event.get('controversy_score')
                relevance_score = event.get('relevance_score')
                contribution_score = event.get('contribution_score')
                opinion_hotness = event.get('opinion_hotness')

                is_selected = 0
                if opinion_hotness >= OPINION_HOTNESS_THRESHOLD:
                    is_selected = 1

                add_opinion_full(
                    fk_origin_article_id=origin_article_id,
                    fk_person_id=fk_person_id,
                    citation=citation,
                    sentiment_score=sentiment,
                    inconsistency_flag=inconsistency_flag,
                    inconsistency_with_id=inconsistency_with_id,
                    inconsistency_comment=inconsistency_comment,
                    controversy_score=controversy_score,
                    relevance_score=relevance_score,
                    contribution_score=contribution_score,
                    opinion_hotness=opinion_hotness,
                    is_selected=is_selected
                )
            else:
                continue

            if event.get('sentiment_deviation') or event.get('stance') or event.get('person_summary'):
                logger.debug('(Stage 2.1) Summary full case')
                topic_id = state.topic_id
                if topic_id is None:
                    raise ValueError("topic_id is missing in the state.")
                add_attitude_full(
                    fk_person_id=fk_person_id,
                    fk_topic_id=topic_id,
                    sentiment_deviation=event.get('sentiment_deviation', None),
                    stance=event.get('stance', None),
                    person_summary=event.get('person_summary', None),
                    is_expert_flag=event.get('is_expert_flag', None)
                )

    # state.is_processed = 1
    # update_article_chunk(
    #     chunk_id=state.chunk_id,
    #     is_processed=True
    # )

    return state