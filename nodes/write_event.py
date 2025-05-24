from database.DBConnector import (
    add_event_full, add_opinion_full, add_person, add_attitude_full, 
    update_article_chunk, get_opinion, get_event, get_attitude
)
from utils.logger import setup_logger
from config import EVENTS_HOTNESS_THRESHOLD, OPINION_HOTNESS_THRESHOLD, MARK_PROCESSED, WRITE_TO_DB
import time
import re

# Initialize logger
logger = setup_logger(name="write_node", log_file="graph.log")

def smart_strip(text):
    """
    Removes unnecessary quotes, slashes, and whitespace from text fields.
    
    Args:
        text: The text to clean
        
    Returns:
        Cleaned text string or None if input was None
    """
    if text is None:
        return None
    
    # Convert to string if not already
    text = str(text)
    
    # Remove leading/trailing quotes, slashes and whitespace
    text = text.strip()
    text = re.sub(r'^[\'"\\|/\s]+|[\'"\\|/\s]+$', '', text)
    
    # Normalize multiple spaces
    text = re.sub(r'\s+', ' ', text)
    
    return text

def main(state):
    logger.debug('Write events node reached')

    if not WRITE_TO_DB:
        return state

    if state.origin_article_id is not None:
        origin_article_id = state.origin_article_id
    else:
        raise ValueError("origin_article_id is missing in the state.")

    # Process general events
    if state.general_event is not None:
        for event in state.general_event:
            title = smart_strip(event.get('title'))
            description = smart_strip(event.get('description'))

            # Skip if required fields are missing
            if description is None:
                logger.debug(f"(Stage -1) Skipping general event due to missing description: {event}")
                continue

            if event.get('event_hotness') is None:  # Unscored event case
                try:
                    logger.debug('(Stage 0) Unscored event case')
                    event_id = add_event_full(
                        fk_origin_article_id=origin_article_id,
                        description=description,
                        event_title=title
                    )
                    
                    # Verify write was successful
                    saved_event = get_event(event_id)
                    if saved_event.empty:
                        logger.error(f"Event wasn't properly saved to database")
                    
                except Exception as e:
                    logger.error(f"Error adding unscored event: {e}")
                    
            else:  # Scored event case
                try:
                    logger.debug('(Stage 1) Scored event case')
                    relevance_score = float(event.get('relevance_score')) if event.get('relevance_score') is not None else None
                    influence_score = float(event.get('influence_score')) if event.get('influence_score') is not None else None
                    novelty_score = float(event.get('novelty_score')) if event.get('novelty_score') is not None else None
                    event_hotness = float(event.get('event_hotness')) if event.get('event_hotness') is not None else None
                    
                    # Log the actual values being sent
                    logger.debug(f"Event scores to be added: relevance={relevance_score}, " +
                                 f"influence={influence_score}, novelty={novelty_score}, " +
                                 f"hotness={event_hotness}")

                    is_selected = 0
                    if event_hotness is not None and event_hotness >= EVENTS_HOTNESS_THRESHOLD:
                        is_selected = 1

                    event_id = add_event_full(
                        fk_origin_article_id=origin_article_id,
                        description=description,
                        event_title=title,
                        relevance_score=relevance_score,
                        influence_score=influence_score,
                        novelty_score=novelty_score,
                        event_hotness=event_hotness,
                        is_selected=is_selected
                    )
                    
                    # Verify the saved values
                    saved_event = get_event(event_id)
                    if saved_event.empty:
                        logger.error(f"Event wasn't properly saved to database")
                    elif (saved_event['relevance_score'] != relevance_score or
                          saved_event['influence_score'] != influence_score or
                          saved_event['novelty_score'] != novelty_score or
                          saved_event['event_hotness'] != event_hotness):
                        logger.error(f"Event scores not saved correctly: Expected {relevance_score}, {influence_score}, " +
                                      f"{novelty_score}, {event_hotness} but got {saved_event['relevance_score']}, " +
                                      f"{saved_event['influence_score']}, {saved_event['novelty_score']}, " +
                                      f"{saved_event['event_hotness']}")
                except Exception as e:
                    logger.error(f"Error adding scored event: {e}")

    # Process person events
    if state.person_event is not None:
        for event in state.person_event:
            person_name = smart_strip(event.get('person_name'))
            citation = smart_strip(event.get('citation'))
            
            # Skip if required fields are missing
            if person_name is None or citation is None:
                logger.warning(f"(Stage -1) Skipping person event due to missing fields: {event}")
                continue
                
            try:
                # Add person first and verify
                fk_person_id = add_person(person_name=person_name) if person_name is not None else None
                if fk_person_id is None:
                    logger.error(f"Failed to add or retrieve person: {person_name}")
                    continue
                    
                if event.get('sentiment') is None:    # Unanalysed event case
                    logger.info('(Stage 0) No sentiment case')
                    opinion_id = add_opinion_full(
                        fk_origin_article_id=origin_article_id,
                        fk_person_id=fk_person_id,
                        citation=citation
                    )
                    
                    # Verify opinion was added
                    saved_opinion = get_opinion(opinion_id)
                    if saved_opinion.empty:
                        logger.error(f"Opinion wasn't properly saved to database")
                        
                elif event.get('inconsistency_flag') is None and event.get('opinion_hotness') is None:  # No inconsistency flag, only sentiment present
                    logger.info('(Stage 1) No inconsistency case')
                    
                    # Type safety conversion
                    sentiment = float(event.get('sentiment')) if event.get('sentiment') is not None else None
                    
                    opinion_id = add_opinion_full(
                        fk_origin_article_id=origin_article_id,
                        fk_person_id=fk_person_id,
                        citation=citation,
                        sentiment_score=sentiment
                    )
                    
                    # Verify sentiment was added correctly
                    saved_opinion = get_opinion(opinion_id)
                    if saved_opinion.empty:
                        logger.error(f"Opinion wasn't properly saved to database")
                    elif saved_opinion['sentiment_score'] != sentiment:
                        logger.error(f"Sentiment score not saved correctly: Expected {sentiment}, got {saved_opinion['sentiment_score']}")
                        
                elif event.get('opinion_hotness') is not None and event.get('inconsistency_flag') is None:
                    logger.info('(Stage 3) Scores without inconsistency case')
                    
                    # Type safety conversions
                    sentiment = float(event.get('sentiment')) if event.get('sentiment') is not None else None
                    
                    # Score conversions with explicit logging
                    controversy_score = float(event.get('controversy_score')) if event.get('controversy_score') is not None else None
                    relevance_score = float(event.get('relevancy_score')) if event.get('relevancy_score') is not None else None
                    contribution_score = float(event.get('contribution_score')) if event.get('contribution_score') is not None else None
                    opinion_hotness = float(event.get('opinion_hotness')) if event.get('opinion_hotness') is not None else None
                    
                    # Log the actual values being sent
                    logger.debug(f"Opinion scores to be added (no inconsistency): controversy={controversy_score}, " +
                                f"relevance={relevance_score}, contribution={contribution_score}, " +
                                f"hotness={opinion_hotness}")
                    
                    is_selected = 0
                    if opinion_hotness is not None and opinion_hotness >= OPINION_HOTNESS_THRESHOLD:
                        is_selected = 1

                    opinion_id = add_opinion_full(
                        fk_origin_article_id=origin_article_id,
                        fk_person_id=fk_person_id,
                        citation=citation,
                        sentiment_score=sentiment,
                        controversy_score=controversy_score,
                        relevancy_score=relevance_score,
                        contribution_score=contribution_score,
                        opinion_hotness=opinion_hotness,
                        is_selected=is_selected
                    )
                    
                    # Verify the saved values with detailed comparison
                    saved_opinion = get_opinion(opinion_id)
                    if saved_opinion.empty:
                        logger.error(f"Opinion wasn't properly saved to database")
                    else:
                        # Build verification report with all scores
                        issues = []
                        if saved_opinion['sentiment_score'] != sentiment:
                            issues.append(f"sentiment: expected {sentiment}, got {saved_opinion['sentiment_score']}")
                        if saved_opinion['controversy_score'] != controversy_score:
                            issues.append(f"controversy: expected {controversy_score}, got {saved_opinion['controversy_score']}")
                        if saved_opinion['relevancy_score'] != relevance_score:
                            issues.append(f"relevance: expected {relevance_score}, got {saved_opinion['relevancy_score']}")
                        if saved_opinion['contribution_score'] != contribution_score:
                            issues.append(f"contribution: expected {contribution_score}, got {saved_opinion['contribution_score']}")
                        if saved_opinion['opinion_hotness'] != opinion_hotness:
                            issues.append(f"hotness: expected {opinion_hotness}, got {saved_opinion['opinion_hotness']}")
                        
                        if issues:
                            logger.error(f"Opinion scores not saved correctly for ID {opinion_id}: {', '.join(issues)}")
                        else:
                            logger.debug(f"All opinion scores verified successfully for ID {opinion_id}")
                        
                elif event.get('opinion_hotness') is None:  # No opinion hotness, only inconsistency flag present
                    logger.info('(Stage 2) No score case')

                    # Type safety conversions
                    sentiment = float(event.get('sentiment')) if event.get('sentiment') is not None else None
                    inconsistency_flag = bool(event.get('inconsistency_flag')) if event.get('inconsistency_flag') is not None else False
                    inconsistency_with_id = event.get('inconsistency_with_id')
                    inconsistency_comment = smart_strip(event.get('inconsistency_comment'))

                    opinion_id = add_opinion_full(
                        fk_origin_article_id=origin_article_id,
                        fk_person_id=fk_person_id,
                        citation=citation,
                        sentiment_score=sentiment,
                        inconsistency_flag=inconsistency_flag,
                        inconsistency_with_id=inconsistency_with_id,
                        inconsistency_comment=inconsistency_comment
                    )
                    
                    # Verify inconsistency data was saved correctly
                    saved_opinion = get_opinion(opinion_id)
                    if saved_opinion.empty:
                        logger.error(f"Opinion wasn't properly saved to database")
                    elif (saved_opinion['sentiment_score'] != sentiment or
                          saved_opinion['inconsistency_flag'] != inconsistency_flag):
                        logger.error(f"Opinion data not saved correctly: Expected sentiment={sentiment}, " +
                                     f"inconsistency_flag={inconsistency_flag}, got " +
                                     f"sentiment={saved_opinion['sentiment_score']}, " +
                                     f"inconsistency_flag={saved_opinion['inconsistency_flag']}")
                        
                elif event.get('opinion_hotness'): # Scored case
                    logger.info('(Stage 3) Scored case')

                    # Type safety conversions
                    sentiment = float(event.get('sentiment')) if event.get('sentiment') is not None else None
                    inconsistency_flag = bool(event.get('inconsistency_flag')) if event.get('inconsistency_flag') is not None else False
                    inconsistency_with_id = event.get('inconsistency_with_id')
                    inconsistency_comment = smart_strip(event.get('inconsistency_comment'))
                    
                    # Score conversions with explicit logging
                    controversy_score = float(event.get('controversy_score')) if event.get('controversy_score') is not None else None
                    relevance_score = float(event.get('relevancy_score')) if event.get('relevancy_score') is not None else None
                    contribution_score = float(event.get('contribution_score')) if event.get('contribution_score') is not None else None
                    opinion_hotness = float(event.get('opinion_hotness')) if event.get('opinion_hotness') is not None else None
                    
                    # Log the actual values being sent
                    logger.debug(f"Opinion scores to be added: controversy={controversy_score}, relevance={relevance_score}, " +
                                f"contribution={contribution_score}, hotness={opinion_hotness}")
                    
                    is_selected = 0
                    if opinion_hotness is not None and opinion_hotness >= OPINION_HOTNESS_THRESHOLD:
                        is_selected = 1

                    opinion_id = add_opinion_full(
                        fk_origin_article_id=origin_article_id,
                        fk_person_id=fk_person_id,
                        citation=citation,
                        sentiment_score=sentiment,
                        inconsistency_flag=inconsistency_flag,
                        inconsistency_with_id=inconsistency_with_id,
                        inconsistency_comment=inconsistency_comment,
                        controversy_score=controversy_score,
                        relevancy_score=relevance_score,
                        contribution_score=contribution_score,
                        opinion_hotness=opinion_hotness,
                        is_selected=is_selected
                    )
                    
                    # Verify the saved values with detailed comparison
                    saved_opinion = get_opinion(opinion_id)
                    if saved_opinion.empty:
                        logger.error(f"Opinion wasn't properly saved to database")
                    else:
                        # Build verification report with all scores
                        issues = []
                        if saved_opinion['controversy_score'] != controversy_score:
                            issues.append(f"controversy: expected {controversy_score}, got {saved_opinion['controversy_score']}")
                        if saved_opinion['relevancy_score'] != relevance_score:
                            issues.append(f"relevance: expected {relevance_score}, got {saved_opinion['relevancy_score']}")
                        if saved_opinion['contribution_score'] != contribution_score:
                            issues.append(f"contribution: expected {contribution_score}, got {saved_opinion['contribution_score']}")
                        if saved_opinion['opinion_hotness'] != opinion_hotness:
                            issues.append(f"hotness: expected {opinion_hotness}, got {saved_opinion['opinion_hotness']}")
                        
                        if issues:
                            logger.error(f"Opinion scores not saved correctly for ID {opinion_id}: {', '.join(issues)}")
                        else:
                            logger.debug(f"All opinion scores verified successfully for ID {opinion_id}")
                else:
                    continue

                # Handle attitude data if present
                if event.get('sentiment_deviation') is not None or event.get('stance') is not None or event.get('person_summary') is not None:
                    logger.info('(Stage 2.1) Summary/attitude full case')
                    topic_id = state.topic_id
                    if topic_id is None:
                        raise ValueError("topic_id is missing in the state.")
                    
                    # Type safety conversions
                    stance = str(event.get('stance')) if event.get('stance') is not None else None
                    sentiment_deviation = float(event.get('sentiment_deviation')) if event.get('sentiment_deviation') is not None else None
                    is_expert_flag = bool(event.get('is_expert_flag')) if event.get('is_expert_flag') is not None else False
                    person_summary = smart_strip(event.get('person_summary'))
                    
                    attitude_id = add_attitude_full(
                        fk_person_id=fk_person_id,
                        fk_topic_id=topic_id,
                        sentiment_deviation=sentiment_deviation,
                        stance=stance,
                        person_summary=person_summary,
                        is_expert_flag=is_expert_flag
                    )
                    
                    # Verify attitude was saved correctly
                    saved_attitude = get_attitude(fk_topic_id=topic_id, fk_person_id=fk_person_id)
                    if saved_attitude.empty:
                        logger.error(f"Attitude wasn't properly saved to database")
                    elif (saved_attitude['sentiment_deviation'] != sentiment_deviation or
                          saved_attitude['stance'] != stance or
                          saved_attitude['is_expert_flag'] != is_expert_flag):
                        logger.error(f"Attitude data not saved correctly: Expected deviation={sentiment_deviation}, " +
                                      f"stance={stance}, expert={is_expert_flag}, got " +
                                      f"deviation={saved_attitude['sentiment_deviation']}, " +
                                      f"stance={saved_attitude['stance']}, " +
                                      f"expert={saved_attitude['is_expert_flag']}")
                    
            except Exception as e:
                logger.error(f"Error processing person event: {e}")

    if MARK_PROCESSED:
        try:
            state.is_processed = 1
            update_article_chunk(
                chunk_id=state.chunk_id,
                is_processed=True
            )
            # Ideally, verify this update was successful too
            logger.info(f"Chunk {state.chunk_id} marked as processed")
        except Exception as e:
            logger.error(f"Error marking chunk as processed: {e}")

    time.sleep(0.5)  # Simple but inefficient approach to ensure DB operations complete
    return state