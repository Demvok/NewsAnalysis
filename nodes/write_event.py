from database.DBConnector import *



def main(state):
    chunk_id = state['chunk_id']
    topic = state['topic']
    content = state['content']

    print('Processing chunk:', chunk_id)

    # Update the database with the processed chunk
    # update_article_chunk(chunk_id, is_processed=1)

    # Write the events to the database
    # write_events_to_db(chunk_id, topic, content)

    return state.update({"is_processed": 1})