from config import FIELD_LENGTH_POLICY, INCONSISTENCY_TOLERANCE, OPINION_FRESHNESS_THRESHOLD
import time
import utils.logger as log

from pydantic import ValidationError
from graph.states_setup import PersonEvent, SentimentScore

from langchain.output_parsers import PydanticOutputParser
from langchain.prompts import PromptTemplate

from graph.model import llm_invoke
from database.DBConnector import t_get_person_opinions

logger = log.setup_logger(name="inconsistency_detection", log_file="graph.log")

import warnings
warnings.filterwarnings("ignore")


# 
# EQUALS TO STAGE 2
# 

def get_inconsistent_opinions(person_id, sentiment_score, date):    
    df = t_get_person_opinions(person_id)
    df = df.loc[abs(sentiment_score - df['sentiment_score']) > INCONSISTENCY_TOLERANCE]  # filter off consistent opinions
    df = df.loc[(date - df['article_date']).dt.days < OPINION_FRESHNESS_THRESHOLD]  # filter off old opinions
    return df.loc[:, 'opinion_id'].tolist()


parser = PydanticOutputParser(pydantic_object=SentimentScore)

prompt = PromptTemplate.from_template(
    """
    You are a debate and sentiment analysis expert.  
    Given the following topic and text, return **only** a single floating-point number between -1.0 and +1.0, where:
    - -1.0 indicates strongly negative sentiment  
    -  0.0 indicates neutral sentiment  
    - +1.0 indicates strongly positive sentiment  

    Topic: {topic}  
    Text:  
    {content}

    Respond with the number alone (e.g. “-0.75”).
    """
)



def _parse_event_output(response: str):
    """Parse the LLM response and return events."""
    start_time = time.time()  # Start timing the parsing process
    sentiment_score = None

    try:
        # Attempt to parse the response as a float
        score = float(response.strip())
        # Wrap the score in a SentimentScore instance with the correct field name
        sentiment_score = SentimentScore(sentiment=score)
    except ValueError as e:
        logger.warning(f"Error while parsing response as float: {e}")
        raise e  # Re-raise the exception to trigger re-invocation if needed
    except ValidationError as e:
        logger.warning(f"Validation error while creating SentimentScore: {e}")
        raise e  # Re-raise the exception to trigger re-invocation if needed

    end_time = time.time()  # End timing for parsing
    logger.debug("Finished parsing model output.", extra={"execution_time": log.timeUsed(start_time, end_time)})
    return sentiment_score

def get_sentiment_score(topic, content, max_retries=3):
    """Extract events with retry logic for invalid outputs."""
    retries = 0
    start_time = time.time()  # Start timing the extraction process

    # First attempt
    formatted_prompt = prompt.format(topic=topic, content=content)
    response = llm_invoke(formatted_prompt)

    try:
        parsed = _parse_event_output(response.content)
        end_time = time.time()  # End timing
        logger.info(f"Successfully got sentiment score", extra={"execution_time": log.timeUsed(start_time, end_time)})
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
                f"Successfully got sentiment score' after {retries} retries.",
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

def main(state):
    try:
        # Extract required fields from the state
        chunk_id = state.chunk_id
        logger.info(f"Sentiment analysis for chunk {chunk_id} started")
        topic = state.topic
        content = state.content
        person_events = state.person_event
        
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
            
            # Get sentiment score for the person's citation
            citation = person_event_data.get('citation')
            sentiment_score = get_sentiment_score(topic, citation)
            
            if sentiment_score is not None:
                # Update the person_event with the sentiment score
                new_person_event = person_event_data.copy()
                new_person_event.update({'sentiment': sentiment_score.sentiment})
                logger.debug(f"Sentiment score for person_event {idx + 1}: {sentiment_score.sentiment}")
                updated_person_events.append(new_person_event)
            else:
                logger.warning(f"Failed to get sentiment score for person_event {idx + 1}.")
                updated_person_events.append(person_event_data)
            
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