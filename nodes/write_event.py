from database.DBConnector import get_article_chunk, add_event, add_opinion, add_person
from utils.logger import setup_logger

# Initialize logger
logger = setup_logger(name="write_event", log_file="graph.log")

def main(state):
    chunk_id = state['chunk_id']
    origin_article_id = get_article_chunk(chunk_id)['fk_article_id']
    topic = state['topic']
    content = state['content']
    general_events, person_events = [], []

    if state.get('general_event') is not None:
        general_events = state['general_event']
    if state.get('person_event') is not None:
        person_events = state['person_event']

    logger.info(f'Chunk {chunk_id} processed, ({len(general_events)}) general events, ({len(person_events)}) opinions.')

    if state.get('general_event') is not None:
        for event in state['general_event']:
            title = event['title']  # Actually, this is not used in the DB, so it can be deleted from prompt
            description = event['description']

            # Save to DB
            add_event(fk_origin_article_id=origin_article_id, description=description)

    if state.get('person_event') is not None:
        for event in state['person_event']:
            person_name = event['person_name']  # Actually, this is not used in the DB, so it can be deleted from prompt
            citation = event['citation']

            # Save to DB
            add_opinion(fk_origin_article_id=origin_article_id, fk_person_id=add_person(person_name=person_name), citation=citation)

    # state['is_processed'] = 1

    return state