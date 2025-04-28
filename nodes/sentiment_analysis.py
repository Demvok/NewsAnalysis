import os
import time
import utils.logger as log

from pydantic import ValidationError
from graph.states_setup import PersonEvent, SentimentScore

from langchain.output_parsers import PydanticOutputParser
from langchain.prompts import PromptTemplate

from graph.model import llm_invoke

logger = log.setup_logger(name="event_classifier", log_file="graph.log")
FIELD_LENGTH_POLICY = os.getenv("FIELD_LENGTH_POLICY").upper()

import warnings
warnings.filterwarnings("ignore")


# 
# EQUALS TO STAGE 1
# 


parser = PydanticOutputParser(pydantic_object=SentimentScore)

prompt = PromptTemplate.from_template(
    """
    You are a debate and sentiment analysis expert.  
    Given the following topic and text, return **only** a single floating-point number between -1.0 and +1.0, where:
    - -1.0 indicates strongly negative sentiment  
    -  0.0 indicates neutral sentiment  
    - +1.0 indicates strongly positive sentiment  

    Topic: {{topic}}  
    Text:  
    {{content}}

    Respond with the number alone (e.g. “-0.75”).
    """
).partial(format_instructions=parser.get_format_instructions())



def _parse_event_output(response: str):
    """Parse the LLM response and return events."""
    start_time = time.time()  # Start timing the parsing process
    sentiment_score = None

    try:
        sentiment_score = parser.parse(response)
    except Exception as e:
        logger.warning(f"Error while parsing response: {e}")
        raise e  # Re-raise the exception to trigger re-invocation if needed

    end_time = time.time()  # End timing for parsing
    logger.debug(
        "Finished parsing event output.",
        extra={"execution_time": log.timeUsed(start_time, end_time)}
    )
    return events

def get_sentiment_score(topic, content, max_retries=3):
    """Extract events with retry logic for invalid outputs."""
    retries = 0
    start_time = time.time()  # Start timing the extraction process

    # First attempt
    formatted_prompt = prompt.format(topic=topic, chunk=content)
    response = llm_invoke(formatted_prompt)

    try:
        parsed = _parse_event_output(response.content)
        if parsed['general_event'] is None and parsed['person_event'] is None:
            # Skip chunk if both events are None
            logger.warning(
                f"Skipping chunk due to None output on the first attempt.",
                extra={"execution_time": log.timeUsed(start_time, time.time())}
            )
            return None
        end_time = time.time()  # End timing
        logger.info(
            f"Successfully extracted events for topic '{topic}'",
            extra={"execution_time": log.timeUsed(start_time, end_time)}
        )
        return parsed  # Return if parsing and validation succeed
    except Exception as e:
        logger.warning(
            f"Error during first attempt: {e}",
            extra={"execution_time": log.timeUsed(start_time, time.time())}
        )

    # Retry logic for other policies (if applicable)
    while retries < max_retries - 1:  # Subtract 1 because the first attempt is already done
        if FIELD_LENGTH_POLICY == "IGNORE":
            break  # Skip retries for IGNORE mode
                
        retries += 1
        response = llm_invoke(formatted_prompt)

        try:
            parsed = _parse_event_output(response.content)
            end_time = time.time()  # End timing
            logger.info(
                f"Successfully extracted events for topic '{topic}' after {retries} retries.",
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
        f"Skipping extraction after {max_retries} retries.",
        extra={"execution_time": log.timeUsed(start_time, end_time)}
    )
    return None  # Skip the current chunk if retries fail

def main(state):
    logger.info(f"Sentiment analysis for chunk {state['chunk_id']} started")
    try:
        chunk_id = state.get('chunk_id')
        topic = state.get('topic')
        content = state.get('content')
        if state.get('PeronEvent') is not None:
            person_event = PersonEvent(**state['PersonEvent'])
        else:
            return None  # Skip if PersonEvent is not present
    except Exception as e:
        logger.warning(f"Error extracting topic or content: {e}")
        return state
    


    start_time = time.time()  # Start timing the main process
    # Extract events
    extracted = get_sentiment_score(topic, content)

    if extracted is None:
        end_time = time.time()  # End timing for skipped chunk
        logger.warning(
            f"Skipping chunk {state['chunk_id']} due to None output or repeated failures.",
            extra={"execution_time": log.timeUsed(start_time, end_time)}
        )
        return state  # Skip updating state if extraction fails

    # Update the state with extracted events
    state['general_event'] = state.get('general_event', [])
    state['person_event'] = state.get('person_event', [])

    if extracted['general_event'] is not None:
        state['general_event'].append(extracted['general_event'].dict())  # Add only valid general_event

    if extracted['person_event'] is not None:
        state['person_event'].append(extracted['person_event'].dict())  # Add only valid person_event

    end_time = time.time()  # End timing for successful processing
    logger.info(
        f"Chunk {state['chunk_id']} processed, ({len(state['general_event'])}) general events, ({len(state['person_event'])}) opinions.",
        extra={"execution_time": log.timeUsed(start_time, end_time)}
    )
    return state