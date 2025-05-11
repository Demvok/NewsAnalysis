from config import FIELD_LENGTH_POLICY, INCONSISTENCY_TOLERANCE, OPINION_FRESHNESS_THRESHOLD, MAX_RETRIES
import time
import utils.logger as log

from pydantic import ValidationError
from graph.states_setup import InconsistencyComment

from langchain.output_parsers import PydanticOutputParser
from langchain.prompts import PromptTemplate

from graph.model import llm_invoke
from graph.prompts import INCONSISTENCY_COMMENT_PROMPT
from database.DBConnector import t_get_person_opinions, find_person

logger = log.setup_logger(name="inconsistency_detection", log_file="graph.log")

import warnings
warnings.filterwarnings("ignore")


# 
# EQUALS TO STAGE 2
# 


parser = PydanticOutputParser(pydantic_object=InconsistencyComment)


#
#   Different policies for field length handling
#

def _validate_comment_length(comment_text):
    """Validate the length of an inconsistency comment."""
    return len(comment_text) <= 200

def _truncate_comment(comment_text):
    """Truncate comment to maximum allowed length."""
    return comment_text[:200]

def _refine_comment(comment_text):
    """Refine comment to fit within character limit using the LLM."""
    refinement_prompt = f"Summarize this inconsistency comment in under 200 characters:\n\n{comment_text}"
    response = llm_invoke(refinement_prompt)
    return response.content[:200]

# Format previous opinions function
def format_previous_opinions(previous_opinions):
    """Helper function to format previous opinions consistently"""
    formatted_previous = ""
    for prev in previous_opinions:
        formatted_previous += f"- Text: {prev['citation']}\n  Date: {prev['article_date']}, Score: {prev['sentiment_score']}\n"
    return formatted_previous

def _parse_event_output(response: str):
    """Parse the LLM response and comment."""
    start_time = time.time()
    comment_text = response.strip()
    
    try:
        if FIELD_LENGTH_POLICY == "IGNORE":
            # Skip validation and directly parse
            inconsistency_comment = InconsistencyComment(inconsistency_comment=comment_text)
        
        elif FIELD_LENGTH_POLICY == "RETRY":
            # Validate length and raise error if invalid
            if not _validate_comment_length(comment_text):
                raise ValueError(f"Comment exceeds 200 characters (length: {len(comment_text)})")
            inconsistency_comment = InconsistencyComment(inconsistency_comment=comment_text)
                
        elif FIELD_LENGTH_POLICY == "REFINE":
            # Attempt to refine comment if validation fails
            try:
                inconsistency_comment = InconsistencyComment(inconsistency_comment=comment_text)
            except ValidationError:
                refined_comment = _refine_comment(comment_text)
                inconsistency_comment = InconsistencyComment(inconsistency_comment=refined_comment)
                
        elif FIELD_LENGTH_POLICY == "TRUNCATE":
            # Truncate comment if validation fails
            try:
                inconsistency_comment = InconsistencyComment(inconsistency_comment=comment_text)
            except ValidationError:
                truncated_comment = _truncate_comment(comment_text)
                inconsistency_comment = InconsistencyComment(inconsistency_comment=truncated_comment)
        
        end_time = time.time()
        logger.debug("Finished parsing model output.", extra={"execution_time": log.timeUsed(start_time, end_time)})
        return inconsistency_comment
        
    except ValidationError as e:
        logger.warning(f"Validation error while creating InconsistencyComment: {e}")
        raise e  # Re-raise the exception to trigger re-invocation if needed
    except Exception as e:
        logger.error(f"Unexpected error while parsing output: {e}")
        raise e

def get_inconsistency_comment(person, topic, new_opinion, previous_opinions, max_retries=MAX_RETRIES):
    """Make inconsistency comment with retry logic for invalid outputs."""
    retries = 0
    start_time = time.time()
    
    # Format data for prompt
    formatted_previous = format_previous_opinions(previous_opinions)
    
    # Create prompt using module-level template
    formatted_prompt = PromptTemplate.from_template(INCONSISTENCY_COMMENT_PROMPT).format(
        person=person,
        topic=topic,
        new_citation=new_opinion['citation'],
        new_date=new_opinion['article_date'],
        new_score=new_opinion['sentiment_score'],
        previous_formatted=formatted_previous
    )
    
    # First attempt
    response = llm_invoke(formatted_prompt)

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
        response = llm_invoke(formatted_prompt)

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


def get_inconsistent_opinions(person_id, sentiment_score, date):    
    df = t_get_person_opinions(person_id)
    df = df.loc[abs(sentiment_score - df['sentiment_score']) > INCONSISTENCY_TOLERANCE]  # filter off consistent opinions
    df = df.loc[(date - df['article_date']).dt.days < OPINION_FRESHNESS_THRESHOLD]  # filter off old opinions
    return df



def main(state):
    try:
        # Extract required fields from the state
        chunk_id = state.chunk_id
        logger.info(f"(Stage 2) Inconsistency analysis for chunk {chunk_id} started")
        topic = state.topic
        content = state.content
        person_events = state.person_event
        article_date = state.article_date
        
        if not topic or not content or not article_date:
            logger.warning(f"Chunk {chunk_id} is missing topic, content or article date. Skipping.")
            return state  # Skip processing if required fields are missing

        if not person_events:
            logger.info(f"No person events found for chunk {chunk_id}. Skipping.")
            return state  # Skip processing if no person events are present
        
    except Exception as e:
        logger.error(f"Error extracting fields from state for chunk {state.chunk_id}: {e}")
        return state

    start_time = time.time()  # Start timing the main process

    # Process each person_event and calculate sentiment scores
    updated_person_events = []
    for idx, person_event_data in enumerate(person_events):
        try:
            logger.debug(f"Processing person_event {idx + 1}/{len(person_events)} for chunk {chunk_id}.")
            
            person_name = person_event_data.get('person_name')
            person_id = find_person(person_name=person_name)

            if person_id is None:
                logger.debug(f"Person ID not yet exists for {person_name} in chunk {chunk_id}.")
                updated_person_events.append(person_event_data)
                continue

            citation = person_event_data.get('citation')
            sentiment_score = person_event_data.get('sentiment')
            
            if sentiment_score is None:
                logger.error(f"Missing sentiment score for person_event {idx + 1} in chunk {chunk_id}.")
                updated_person_events.append(person_event_data)
                raise ValueError(f"Missing sentiment score for person_event {idx + 1} in chunk {chunk_id}.")

            inconsistent_with = get_inconsistent_opinions(person_id=person_id, sentiment_score=sentiment_score, date=article_date)

            if inconsistent_with.empty:
                logger.debug(f"No inconsistent opinions found for person_event {idx + 1} in chunk {chunk_id}.")
                new_person_event = person_event_data.copy()
                new_person_event.update({'person_id': person_id})
                # Set inconsistency_flag to False explicitly
                new_person_event.update({'inconsistency_flag': False})
                # Add the updated event data, not the original
                updated_person_events.append(new_person_event)
                continue
            else:
                logger.debug(f"Inconsistent opinions found for person_event {idx + 1} in chunk {chunk_id}.")
                new_person_event = person_event_data.copy()
                new_person_event.update({'person_id': person_id})
                
                # Convert to a list of IDs or records
                inconsistent_ids = inconsistent_with['opinion_id'].tolist()  # Assuming 'opinion_id' exists
                new_person_event.update({'inconsistency_with_id': inconsistent_ids})
                new_person_event.update({'inconsistency_flag': True})

                inconsistency_comment = get_inconsistency_comment(
                    person=person_name,
                    topic=topic,
                    new_opinion={'citation': citation, 'article_date': article_date, 'sentiment_score': sentiment_score},
                    previous_opinions=inconsistent_with.loc[:, ['citation', 'article_date', 'sentiment_score']].to_dict(orient='records')
                )
                
                # Extract the string from the Pydantic model instead of storing the whole object
                if inconsistency_comment is not None:
                    new_person_event.update({'inconsistency_comment': inconsistency_comment.inconsistency_comment})
                else:
                    new_person_event.update({'inconsistency_comment': None})
                
                updated_person_events.append(new_person_event)            
            
        except Exception as e:
            logger.error(f"Error processing person_event {idx + 1} for chunk {chunk_id}: {e}")
            updated_person_events.append(person_event_data)  # Add the original data to avoid data loss

    # Update the state with the processed person events
    state.person_event = updated_person_events

    end_time = time.time()  # End timing for the main process
    logger.info(
        f"Chunk {chunk_id} processed successfully. "
        f"Processed {len(updated_person_events)} person events.",
        extra={"execution_time": log.timeUsed(start_time, end_time)}
    )
    return state