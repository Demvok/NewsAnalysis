from config import FIELD_LENGTH_POLICY, MAX_RETRIES
import time
import utils.logger as log

from pydantic import ValidationError
from graph.states_setup import OpinionScores

from langchain.output_parsers import PydanticOutputParser
from langchain.prompts import PromptTemplate

from graph.model import llm_invoke
from graph.prompts import OPINION_SCORING_PROMPT

logger = log.setup_logger(name="opinion_scoring", log_file="graph.log")

import warnings
warnings.filterwarnings("ignore")


# 
# EQUALS TO STAGE 3
# 

parser = PydanticOutputParser(pydantic_object=OpinionScores)
prompt = PromptTemplate.from_template(OPINION_SCORING_PROMPT)


def _parse_scoring_output(response: str):
    """Parse the LLM response and extract opinion scores."""
    start_time = time.time()
    scores = None

    try:
        # Extract the scores from the response
        lines = response.strip().split('\n')
        relevancy = None
        contribution = None
        controversy = None
        
        for line in lines:
            line = line.lower().strip()
            if "relevancy:" in line:
                relevancy = float(line.split("relevancy:")[1].strip())
            elif "contribution:" in line:
                contribution = float(line.split("contribution:")[1].strip())
            elif "controversy:" in line:
                controversy = float(line.split("controversy:")[1].strip())
            
            # Alternative parsing for JSON-like format
            elif "\"relevancy\":" in line or "'relevancy':" in line:
                relevancy = float(line.split(":")[1].strip().rstrip(','))
            elif "\"contribution\":" in line or "'contribution':" in line:
                contribution = float(line.split(":")[1].strip().rstrip(','))
            elif "\"controversy\":" in line or "'controversy':" in line:
                controversy = float(line.split(":")[1].strip().rstrip(','))
        
        # Create OpinionScores object if all scores are found
        if relevancy is not None and contribution is not None and controversy is not None:
            scores = OpinionScores(
                relevancy=relevancy,
                contribution=contribution,
                controversy=controversy
            )
    except ValueError as e:
        logger.warning(f"Error parsing response as float: {e}")
        raise e
    except ValidationError as e:
        logger.warning(f"Validation error while creating OpinionScores: {e}")
        raise e

    end_time = time.time()
    logger.debug("Finished parsing model output.", extra={"execution_time": log.timeUsed(start_time, end_time)})
    return scores


def get_opinion_scores(
        person_name,
        topic,
        citation,
        inconsistency_flag,
        inconsistency_comment, 
        sentiment_deviation,
        person_summary,
        max_retries=MAX_RETRIES
    ):
    """Calculate opinion scores with retry logic for invalid outputs."""
    retries = 0
    start_time = time.time()
    
    # First attempt
    formatted_prompt = prompt.format(
        person=person_name,
        topic=topic,
        citation=citation,
        inconsistency_flag=inconsistency_flag,
        inconsistency=inconsistency_comment,
        sentiment_deviation=sentiment_deviation,
        person_summary=person_summary
    )
    response = llm_invoke(formatted_prompt)

    try:
        parsed = _parse_scoring_output(response.content)
        end_time = time.time()
        logger.info(f"Successfully calculated opinion scores", 
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
        response = llm_invoke(formatted_prompt)

        try:
            parsed = _parse_scoring_output(response.content)
            end_time = time.time()
            logger.info(f"Successfully calculated opinion scores after {retries} retries.",
                        extra={"execution_time": log.timeUsed(start_time, end_time)})
            return parsed
        except Exception as e:
            logger.warning(f"Retry {retries}/{max_retries} due to invalid output: {e}",
                          extra={"execution_time": log.timeUsed(start_time, time.time())})

    end_time = time.time()
    logger.error(f"Skipping opinion scoring after {max_retries} retries.",
                extra={"execution_time": log.timeUsed(start_time, end_time)})
    return None


def calculate_opinion_hotness(relevancy, contribution, controversy):
    """Calculate opinion hotness as a weighted sum of scores."""
    # Mockup formula - replace with your actual formula
    return 0.4 * relevancy + 0.3 * contribution + 0.3 * controversy


def main(state):
    try:
        chunk_id = state.chunk_id
        logger.info(f"(Stage 3) Opinion scoring for chunk {chunk_id} started")
        topic = state.topic
        person_events = state.person_event
        
        if not topic:
            logger.warning(f"Chunk {chunk_id} is missing topic. Skipping.")
            return state

        if not person_events:
            logger.info(f"No person events found for chunk {chunk_id}. Skipping.")
            return state
        
    except Exception as e:
        logger.error(f"Error extracting fields from state for chunk {state.chunk_id}: {e}")
        return state

    start_time = time.time()

    # Process each person_event and calculate opinion scores
    updated_person_events = []
    for idx, person_event_data in enumerate(person_events):
        try:
            logger.debug(f"Processing person_event {idx + 1}/{len(person_events)} for chunk {chunk_id}.")
            
            citation = person_event_data.get('citation')
            inconsistency_flag = person_event_data.get('inconsistency_flag', False)
            inconsistency_comment = person_event_data.get('inconsistency_comment', '')
            sentiment_deviation = person_event_data.get('sentiment_deviation', 0.0)
            
            # Get person summary
            person_name = person_event_data.get('person_name')
            person_summary = person_event_data.get('person_summary', None)
            
            # Get opinion scores
            opinion_scores = get_opinion_scores(
                person_name=person_name,
                topic=topic,
                citation=citation,
                inconsistency_flag=inconsistency_flag,
                inconsistency_comment=inconsistency_comment,
                sentiment_deviation=sentiment_deviation,
                person_summary=person_summary
            )
            
            if opinion_scores is not None:
                # Calculate opinion hotness
                opinion_hotness = calculate_opinion_hotness(
                    opinion_scores.relevancy,
                    opinion_scores.contribution,
                    opinion_scores.controversy
                )
                
                # Update the person_event with scores
                new_person_event = person_event_data.copy()
                new_person_event.update({
                    'relevancy_score': opinion_scores.relevancy,
                    'contribution_score': opinion_scores.contribution,
                    'controversy_score': opinion_scores.controversy,
                    'opinion_hotness': opinion_hotness
                })
                updated_person_events.append(new_person_event)
            else:
                logger.warning(f"Failed to get opinion scores for person_event {idx + 1}.")
                updated_person_events.append(person_event_data)
            
        except Exception as e:
            logger.error(f"Error processing person_event {idx + 1} for chunk {chunk_id}: {e}")
            updated_person_events.append(person_event_data)  # Add the original data to avoid data loss

    # Update the state with the processed person events
    state.person_event = updated_person_events

    end_time = time.time()
    logger.info(
        f"Chunk {chunk_id} processed successfully. "
        f"Processed {len(updated_person_events)} person events.",
        extra={"execution_time": log.timeUsed(start_time, end_time)}
    )
    return state