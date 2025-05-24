from config import FIELD_LENGTH_POLICY, MAX_RETRIES, MIXED_INTERVAL
from config import get_weighted_sentiment, get_stance, get_deviation_score
import time
import utils.logger as log
import pandas as pd

from pydantic import ValidationError
from graph.states_setup import PersonSummary

from langchain.prompts import PromptTemplate

from graph.model import llm_invoke
from graph.prompts import PERSON_SUMMARY_PROMPT, REFINING_PROMPT
from database.DBConnector import t_get_person_topic_sentiment_history, get_person, get_attitude

logger = log.setup_logger(name="person_summary", log_file="graph.log")

import warnings
warnings.filterwarnings("ignore")


# 
# EQUALS TO STAGE 2.1
# 



#
#   Different policies for field length handling
#

def _validate_comment_length(comment_text):
    """Validate the length of an inconsistency comment."""
    return len(comment_text) <= 300

def _truncate_comment(comment_text):
    """Truncate comment to maximum allowed length."""
    return comment_text[:300]

def _refine_comment(comment_text):
    """Refine comment to fit within character limit using the LLM."""
    try:
        refinement_prompt = REFINING_PROMPT.format(max_lenth=300, field_value=comment_text)
        response = llm_invoke(refinement_prompt, usage_type='refine')
        
        # Handle different response structures
        if hasattr(response, 'content'):
            refined_text = response.content
        elif isinstance(response, str):
            refined_text = response
        elif hasattr(response, 'text'):
            refined_text = response.text
        else:
            logger.warning(f"Unexpected response type from LLM: {type(response)}. Using truncation instead.")
            return comment_text[:300]
            
        # Ensure we're within length limits
        return refined_text[:300]
    except Exception as e:
        logger.error(f"Error in refinement process: {e}. Falling back to truncation.")
        return comment_text[:300]  # Fallback to truncation if refinement fails

def _parse_event_output(response: str):
    """Parse the LLM response into a PersonSummary object."""
    start_time = time.time()
    comment_text = response.strip()
    
    try:
        # Apply field length policies BEFORE creating the PersonSummary object
        if len(comment_text) > 300:
            logger.debug(f"Comment exceeds 300 characters (length: {len(comment_text)}). Applying {FIELD_LENGTH_POLICY} policy.")
            if FIELD_LENGTH_POLICY == "TRUNCATE":
                comment_text = _truncate_comment(comment_text)
            elif FIELD_LENGTH_POLICY == "REFINE":
                comment_text = _refine_comment(comment_text)
            elif FIELD_LENGTH_POLICY == "RETRY":
                raise ValueError(f"Comment exceeds 300 characters (length: {len(comment_text)})")
            elif FIELD_LENGTH_POLICY != "IGNORE":
                logger.warning(f"Unknown field length policy: {FIELD_LENGTH_POLICY}. Using truncation.")
                comment_text = _truncate_comment(comment_text)
        
        # Create PersonSummary object with the processed text
        person_summary = PersonSummary(person_summary=comment_text)
        
        end_time = time.time()
        logger.debug("Finished parsing model output.", extra={"execution_time": log.timeUsed(start_time, end_time)})
        return person_summary
        
    except ValidationError as e:
        logger.warning(f"Validation error while creating PersonSummary: {e}")
        raise e
    except Exception as e:
        logger.error(f"Unexpected error while parsing output: {e}")
        raise e

def _get_summary_from_llm(prompt, max_retries=MAX_RETRIES):
    """Make inconsistency comment with retry logic for invalid outputs."""
    retries = 0
    start_time = time.time()
    
    # First attempt
    response = llm_invoke(prompt, usage_type='person_summary')

    # Rest of the function remains the same
    try:
        parsed = _parse_event_output(response.content)
        end_time = time.time()  # End timing
        logger.info(f"Successfully got comment", extra={"execution_time": log.timeUsed(start_time, end_time)})
        return parsed  # Return if parsing and validation succeed
    except Exception as e:
        logger.warning(f"Error during first attempt: {e}", extra={"execution_time": log.timeUsed(start_time, time.time())})

    # Retry logic
    while retries < max_retries - 1:  # Subtract 1 because the first attempt is already done
        if FIELD_LENGTH_POLICY == "IGNORE":
            break  # Skip retries for IGNORE mode
                
        retries += 1
        response = llm_invoke(prompt, usage_type='person_summary')

        try:
            parsed = _parse_event_output(response.content)
            end_time = time.time()
            logger.info(f"Successfully got comment after {retries} retries.",
                extra={"execution_time": log.timeUsed(start_time, end_time)})
            return parsed
        except Exception as e:
            logger.warning(f"Retry {retries}/{max_retries} due to invalid output: {e}",
                extra={"execution_time": log.timeUsed(start_time, time.time())})

    end_time = time.time()  # End timing after retries
    logger.error(
        f"Skipping analysis after {max_retries} retries.",
        extra={"execution_time": log.timeUsed(start_time, end_time)}
    )
    return None  # Skip the current chunk if retries fail

def get_person_summary(
    person_name: str,
    topic: str,
    content: str,
    citation: str,
    stance: str,
    sentiment_deviation: str,
    inconsistency_comment: str = None,
    prev_person_summary: str = None 
):

    prompt = PromptTemplate.from_template(PERSON_SUMMARY_PROMPT).format(
        person=person_name,        
        topic=topic,
        content=content,
        citation=citation,
        stance=stance,
        sentiment_deviation=sentiment_deviation,
        previous_summary=prev_person_summary,
        inconsistency=inconsistency_comment
    )

    return _get_summary_from_llm(prompt)


# Small helper functions

def add_row_and_sort(df, new_row):
    """
    Adds a new row to the DataFrame and sorts it by article_date in descending order.

    Parameters:
    df (pd.DataFrame): The original DataFrame.
    new_row (dict): The new row to add.

    Returns:
    pd.DataFrame: The updated and sorted DataFrame.
    """
    # Add the new row
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

    # Ensure article_date is in datetime format
    df['article_date'] = pd.to_datetime(df['article_date'])

    # Sort by article_date in descending order
    df = df.sort_values(by='article_date', ascending=False).reset_index(drop=True)
    return df


def main(state):
    try:
        chunk_id = state.chunk_id
        logger.info(f"(Stage 2.1) Person attitude analysis for chunk {chunk_id} started")

        topic_id = state.topic_id
        topic = state.topic        
        content = state.content
        person_events = state.person_event
        article_date = state.article_date
        
        if not topic_id or not content or not article_date or not topic:
            logger.warning(f"Chunk {chunk_id} is missing topic, content or article date. Skipping.")
            return state  # Skip processing if required fields are missing
        if not person_events:
            logger.info(f"No person events found for chunk {chunk_id}. Skipping.")
            return state  # Skip processing if no person events are present    
    except Exception as e:
        logger.error(f"Error extracting fields from state for chunk {state.chunk_id}: {e}")
        return state

    start_time = time.time()

    # Process each person_event
    updated_person_events = []
    for idx, person_event_data in enumerate(person_events):
        try:
            logger.debug(f"Processing person_event {idx + 1}/{len(person_events)} for chunk {chunk_id}.")
            
            person_name = person_event_data.get('person_name')
            person_id = person_event_data.get('person_id')
            sentiment = person_event_data.get('sentiment')
            citation = person_event_data.get('citation') 

            # Additional checks for required fields
            if sentiment is None:
                logger.error(f"Missing sentiment score for person_event {idx + 1} in chunk {chunk_id}.")
                updated_person_events.append(person_event_data)
                raise ValueError(f"Missing sentiment score for person_event {idx + 1} in chunk {chunk_id}.")
            if person_id is None:
                logger.info(f"Missing person ID for person_event {idx + 1} in chunk {chunk_id}.")
            if person_name is None and person_id:
                logger.warning(f"Missing person name and ID for person_event {idx + 1} in chunk {chunk_id}.")
                person_name = get_person(person_id=person_id)['person_name']
                if person_name is None:
                    logger.error(f"Person name not found for person_event {idx + 1} in chunk {chunk_id}.")
                    updated_person_events.append(person_event_data)
                    raise ValueError(f"Person name not found for person_event {idx + 1} in chunk {chunk_id}.")                
            elif person_name is None: 
                logger.error(f"Missing person name for person_event {idx + 1} in chunk {chunk_id}.")
                updated_person_events.append(person_event_data)
                raise ValueError(f"Missing person name for person_event {idx + 1} in chunk {chunk_id}.")
            if citation is None:
                logger.error(f"Missing citation for person_event {idx + 1} in chunk {chunk_id}.")
                updated_person_events.append(person_event_data)
                raise ValueError(f"Missing citation for person_event {idx + 1} in chunk {chunk_id}.")

            sentiment_history = t_get_person_topic_sentiment_history(person_id=person_id, topic_id=topic_id) if person_id else None
            sentiment_history = add_row_and_sort(sentiment_history, {'sentiment_score': sentiment, 'article_date': article_date}) if person_id else None

            inconsistency_comment = person_event_data.get('inconsistency_comment', None)
            previous_summary = get_attitude(fk_topic_id=topic_id, fk_person_id=person_id) if person_id else None
            previous_summary = None if previous_summary is None or previous_summary.empty else previous_summary

            new_person_event = person_event_data.copy()

            if sentiment_history is None or (hasattr(sentiment_history, 'empty') and sentiment_history.empty): # CASE - no history, generating new summary
                logger.info(f"No sentiment history found for person {person_name} in chunk {chunk_id}, generating new.")
                
                stance = get_stance(sentiment)
                sentiment_deviation = 0
                new_person_event.update({'sentiment_deviation': sentiment_deviation})
                new_person_event.update({'stance': stance})
            else:  # CASE - history exists
                logger.info(f"Sentiment history found for person {person_name} in chunk {chunk_id}.")

                sentiment_history = get_weighted_sentiment(sentiment_history)
                stance = get_stance(sentiment_history.weighted_sentiment.mean())
                sentiment_deviation = sentiment_history.weighted_sentiment.std()
                new_person_event.update({'sentiment_deviation': sentiment_deviation})
                new_person_event.update({'stance': stance})                

            person_summary = get_person_summary(
                person_name=person_name,
                topic=topic,
                content=content,
                citation=citation,
                stance=stance,
                sentiment_deviation=get_deviation_score(sentiment_deviation),
                inconsistency_comment=inconsistency_comment,
                prev_person_summary=previous_summary
            )

            new_person_event.update(person_summary)
            updated_person_events.append(new_person_event)
            
        except Exception as e:
            logger.error(f"Error processing person_event {idx + 1} for chunk {chunk_id}: {e}")
            updated_person_events.append(person_event_data)  # Add the original data to avoid data loss

    
    state.person_event = updated_person_events

    end_time = time.time()  
    logger.info(
        f"Chunk {chunk_id} processed successfully. "
        f"Processed {len(updated_person_events)} person events.",
        extra={"execution_time": log.timeUsed(start_time, end_time)}
    )
    return state