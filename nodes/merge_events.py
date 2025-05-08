from database.DBConnector import get_article_chunk
from utils.logger import setup_logger

# Initialize logger
logger = setup_logger(name="merge_event", log_file="graph.log")

def main(state):
    logger.debug('Merge events node reached')
    chunk_id = state.chunk_id
    if chunk_id is None:
        logger.error("Chunk ID is missing in the state.")
        return state

    state.origin_article_id = get_article_chunk(chunk_id)['fk_article_id']
    general_events, person_events = [], []

    if state.general_event is not None:
        general_events = state.general_event
    if state.person_event is not None:
        person_events = state.person_event

    logger.info(f'Chunk {chunk_id} processed, ({len(general_events)}) general events, ({len(person_events)}) opinions.')
        
    return state