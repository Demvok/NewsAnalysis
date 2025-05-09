from config import FIELD_LENGTH_POLICY, INCONSISTENCY_TOLERANCE, OPINION_FRESHNESS_THRESHOLD
import time
import utils.logger as log

from pydantic import ValidationError
from graph.states_setup import InconsistencyComment

from langchain.output_parsers import PydanticOutputParser
from langchain.prompts import PromptTemplate

from graph.model import llm_invoke
from database.DBConnector import t_get_person_opinions, find_person, get_article

logger = log.setup_logger(name="inconsistency_detection", log_file="graph.log")

import warnings
warnings.filterwarnings("ignore")


# 
# EQUALS TO STAGE 2
# 


parser = PydanticOutputParser(pydantic_object=InconsistencyComment)

prompt = PromptTemplate.from_template(
    """
    You are an expert journalist skilled in deduction and speech analysis.  
    Given the following inputs, compare the new citation to previous ones and comment on any inconsistency.

    Person: {person}  
    Topic: {topic} 
    New citation:  
    - Text: {new.citation}  
    - Date: {new.article_date}  
    - Score: {new.sentiment_score}  
    Previous citations (up to 15):  
    {{#each previous}}
    - Text: {this.citation}  
        Date: {this.article_date}, Score: {this.sentiment_score}
    {{/each}}

    Write **only one paragraph**, max **200 characters**, pointing out the sentiment inconsistency. Do not include quotes or metadata—just the concise comment.
    """
)



def _parse_event_output(response: str):
    """Parse the LLM response and comment."""
    start_time = time.time()  # Start timing the parsing process
    inconsistency_comment = None

    try:
        inconsistency_comment = InconsistencyComment(inconsistency_comment=response.strip())
    except ValidationError as e:
        logger.warning(f"Validation error while creating InconsistencyComment: {e}")
        raise e  # Re-raise the exception to trigger re-invocation if needed
    except Exception as e:
        logger.error(f"Unexpected error while parsing output: {e}")
        raise e

    end_time = time.time()  # End timing for parsing
    logger.debug("Finished parsing model output.", extra={"execution_time": log.timeUsed(start_time, end_time)})
    return inconsistency_comment

def get_inconsistency_comment(person, topic, new_opinion, previous_opinions, max_retries=3):
    """Make inconsistency comment with retry logic for invalid outputs."""
    retries = 0
    start_time = time.time()  # Start timing the extraction process

    # First attempt
    formatted_prompt = prompt.format(
        person=person,
        topic=topic,
        new=new_opinion,
        previous=previous_opinions
    )
    response = llm_invoke(formatted_prompt)

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
            end_time = time.time()  # End timing
            logger.info(
                f"Successfully got comment' after {retries} retries.",
                extra={"execution_time": log.timeUsed(start_time, end_time)}
            )
            return parsed  # Return if parsing and validation succeed
        except Exception as e:
            logger.warning(
                f"Retry {retries}/{max_retries} due to invalid output: {e}",
                extra={"execution_time": log.timeUsed(start_time, time.time())}
            )

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
        logger.info(f"Inconsistency analysis for chunk {chunk_id} started")
        topic = state.topic
        content = state.content
        person_events = state.person_event
        origin_article_id = state.origin_article_id
        article_date = get_article(origin_article_id).loc[0, 'article_date']
        
        if not topic or not content:
            logger.warning(f"Chunk {chunk_id} is missing topic or content. Skipping.")
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
            citation = person_event_data.get('citation')
            sentiment_score = person_event_data.get('sentiment')
            
            if sentiment_score is None:
                logger.error(f"Missing sentiment score for person_event {idx + 1} in chunk {chunk_id}.")
                updated_person_events.append(person_event_data)
                raise ValueError(f"Missing sentiment score for person_event {idx + 1} in chunk {chunk_id}.")

            incosistent_with = get_inconsistent_opinions(person_id=person_id, sentiment_score=sentiment_score, date=article_date)

            if incosistent_with.empty:
                logger.debug(f"No inconsistent opinions found for person_event {idx + 1} in chunk {chunk_id}.")
                updated_person_events.append(person_event_data)
                continue
            else:
                logger.debug(f"Inconsistent opinions found for person_event {idx + 1} in chunk {chunk_id}.")
                new_person_event = person_event_data.copy()
                new_person_event.update({'inconsistency_with_id': incosistent_with})
                new_person_event.update({'inconsistency_flag': True})

                inconsistency_comment = get_inconsistency_comment(
                    person=person_name,
                    topic=topic,
                    new_opinion={'citation': citation, 'article_date': article_date, 'sentiment_score': sentiment_score},
                    previous_opinions=incosistent_with.loc[:, ['citation', 'article_date', 'sentiment_score']].to_dict(orient='records')
                )
                new_person_event.update({'inconsistency_comment': inconsistency_comment})
                
                # inconsistent_opinions = incosistent_with.to_dict(orient='records')
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