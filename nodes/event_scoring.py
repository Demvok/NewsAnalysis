from config import FIELD_LENGTH_POLICY, MAX_RETRIES
import time
import utils.logger as log

from pydantic import ValidationError
from graph.states_setup import EventScores

from langchain.output_parsers import PydanticOutputParser
from langchain.prompts import PromptTemplate

from graph.model import llm_invoke
from graph.prompts import EVENT_SCORING_PROMPT

logger = log.setup_logger(name="event_scoring", log_file="graph.log")

import warnings
warnings.filterwarnings("ignore")


# 
# EQUALS TO STAGE 1
#

parser = PydanticOutputParser(pydantic_object=EventScores)
prompt = PromptTemplate.from_template(EVENT_SCORING_PROMPT)


def _parse_scoring_output(response: str):
    """Parse the LLM response and extract event scores."""
    start_time = time.time()
    scores = None

    try:
        # Extract the scores from the response
        lines = response.strip().split('\n')
        relevance = None
        influence = None
        novelty = None
        
        for line in lines:
            line = line.lower().strip()
            if "relevance:" in line:
                relevance = float(line.split("relevance:")[1].strip())
            elif "influence:" in line:
                influence = float(line.split("influence:")[1].strip())
            elif "novelty:" in line:
                novelty = float(line.split("novelty:")[1].strip())
            
            # Alternative parsing for JSON-like format
            elif "\"relevance\":" in line or "'relevance':" in line:
                relevance = float(line.split(":")[1].strip().rstrip(','))
            elif "\"influence\":" in line or "'influence':" in line:
                influence = float(line.split(":")[1].strip().rstrip(','))
            elif "\"novelty\":" in line or "'novelty':" in line:
                novelty = float(line.split(":")[1].strip().rstrip(','))
        
        # Create EventScores object if all scores are found
        if relevance is not None and influence is not None and novelty is not None:
            scores = EventScores(
                relevance=relevance,
                influence=influence,
                novelty=novelty
            )
    except ValueError as e:
        logger.warning(f"Error parsing response as float: {e}")
        raise e
    except ValidationError as e:
        logger.warning(f"Validation error while creating EventScores: {e}")
        raise e

    end_time = time.time()
    logger.debug("Finished parsing model output.", extra={"execution_time": log.timeUsed(start_time, end_time)})
    return scores


def get_event_scores(
        title,
        description,
        topic,
        content,
        max_retries=MAX_RETRIES
    ):
    """Calculate event scores with retry logic for invalid outputs."""
    retries = 0
    start_time = time.time()
    
    # First attempt
    formatted_prompt = prompt.format(
        title=title,
        description=description,
        topic=topic,
        content=content
    )
    response = llm_invoke(formatted_prompt, usage_type='event_scoring')

    try:
        parsed = _parse_scoring_output(response.content)
        end_time = time.time()
        logger.info(f"Successfully calculated event scores", 
                    extra={"execution_time": log.timeUsed(start_time, end_time)})
        return parsed
    except Exception as e:
        logger.warning(f"Error during first attempt: {e}", 
                       extra={"execution_time": log.timeUsed(start_time, time.time())})

    # Retry logic
    while retries < max_retries - 1:
        if FIELD_LENGTH_POLICY == "IGNORE":
            break
                
        retries += 1
        response = llm_invoke(formatted_prompt, usage_type='event_scoring')

        try:
            parsed = _parse_scoring_output(response.content)
            end_time = time.time()
            logger.info(f"Successfully calculated event scores after {retries} retries.",
                        extra={"execution_time": log.timeUsed(start_time, end_time)})
            return parsed
        except Exception as e:
            logger.warning(f"Retry {retries}/{max_retries} due to invalid output: {e}",
                          extra={"execution_time": log.timeUsed(start_time, time.time())})

    end_time = time.time()
    logger.error(f"Skipping event scoring after {max_retries} retries.",
                extra={"execution_time": log.timeUsed(start_time, end_time)})
    return None


def calculate_event_hotness(relevance, influence, novelty):
    """Calculate event hotness as a weighted sum of scores."""
    return 0.35 * relevance + 0.4 * influence + 0.25 * novelty


def main(state):
    try:
        chunk_id = state.chunk_id
        logger.info(f"(Stage 1) Event scoring for chunk {chunk_id} started")
        topic = state.topic
        content = state.content
        general_events = state.general_event
        
        if not topic or not content:
            logger.warning(f"Chunk {chunk_id} is missing topic or content. Skipping.")
            return state

        if not general_events:
            logger.info(f"No general events found for chunk {chunk_id}. Skipping.")
            return state
        
    except Exception as e:
        logger.error(f"Error extracting fields from state for chunk {state.chunk_id}: {e}")
        return state

    start_time = time.time()

    # Process each general_event and calculate event scores
    updated_general_events = []
    for idx, general_event_data in enumerate(general_events):
        try:
            logger.debug(f"Processing general_event {idx + 1}/{len(general_events)} for chunk {chunk_id}.")
            
            title = general_event_data.get('title')
            description = general_event_data.get('description')
            
            # Skip if missing required fields
            if not title or not description:
                logger.warning(f"Missing title or description for general_event {idx + 1}. Skipping.")
                updated_general_events.append(general_event_data)
                continue
            
            # Get event scores
            event_scores = get_event_scores(
                title=title,
                description=description,
                topic=topic,
                content=content
            )
            
            if event_scores is not None:
                # Calculate event hotness
                event_hotness = calculate_event_hotness(
                    event_scores.relevance,
                    event_scores.influence,
                    event_scores.novelty
                )
                
                # Update the general_event with scores
                new_general_event = general_event_data.copy()
                new_general_event.update({
                    'relevance_score': event_scores.relevance,
                    'influence_score': event_scores.influence,
                    'novelty_score': event_scores.novelty,
                    'event_hotness': event_hotness
                })
                updated_general_events.append(new_general_event)
            else:
                logger.warning(f"Failed to get event scores for general_event {idx + 1}.")
                updated_general_events.append(general_event_data)
            
        except Exception as e:
            logger.error(f"Error processing general_event {idx + 1} for chunk {chunk_id}: {e}")
            updated_general_events.append(general_event_data)  # Add the original data to avoid data loss

    # Update the state with the processed general events
    state.general_event = updated_general_events

    end_time = time.time()
    logger.info(
        f"Chunk {chunk_id} processed successfully. "
        f"Processed {len(updated_general_events)} general events.",
        extra={"execution_time": log.timeUsed(start_time, end_time)}
    )
    return state