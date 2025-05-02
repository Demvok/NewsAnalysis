from database.DBConnector import add_event, add_opinion, add_person
from utils.logger import setup_logger

# Initialize logger
logger = setup_logger(name="write_event", log_file="graph.log")

def main(state):
    origin_article_id = state.get('origin_article_id')

    # Process general events
    if state.get('general_event') is not None:
        for event in state['general_event']:
            title = event.get('title')  # Actually, this is not used in the DB, so it can be deleted from prompt
            description = event.get('description')

            # Skip if required fields are missing
            if description is None:
                logger.debug(f"Skipping general event due to missing description: {event}")
                continue

            # Save to DB
            add_event(fk_origin_article_id=origin_article_id, description=description)

    # Process person events
    if state.get('person_event') is not None:
        for event in state['person_event']:
            person_name = event.get('person_name')  # Actually, this is not used in the DB, so it can be deleted from prompt
            citation = event.get('citation')
            sentiment = event.get('sentiment', None)  # Default to None if not provided

            # Skip if required fields are missing
            if person_name is None or citation is None:
                logger.debug(f"Skipping person event due to missing fields: {event}")
                continue

            # Save to DB
            add_opinion(fk_origin_article_id=origin_article_id, fk_person_id=add_person(person_name=person_name), citation=citation, sentiment_score=sentiment)

    # state['is_processed'] = 1

    return state