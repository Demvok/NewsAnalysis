import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))) # <-- Not sure about this

import pandas as pd
from langchain.text_splitter import RecursiveCharacterTextSplitter

from database.DBConnector import add_article_chunk_df, t_get_articles_without_chunks_input
from utils.logger import setup_logger
logger = setup_logger(name='article_splitter', log_file="article_splitter.log")
from config import CHUNK_SIZE, CHUNK_OVERLAP


def main(df, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP):
    # Check if chunk_size is -1 (special case: no splitting)
    if chunk_size == -1:
        # Process each article separately without splitting
        for _, row in df.iterrows():
            logger.debug("Creating single chunk for article_id: %s", row["article_id"])        
            article_id = row["article_id"]
            content = row["content"]
            
            # Create a single chunk for the entire article
            chunk_df = pd.DataFrame([{
                "fk_article_id": article_id,
                "start_index": 0,
                "end_index": len(content),
                "is_processed": 0
            }])
            add_article_chunk_df(chunk_df)
    else:
        # Normal splitting process
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap, separators=["\n\n", "\n", "."]
        )    
        # Process each article separately
        for _, row in df.iterrows():
            logger.debug("Starting data splitting process for article_id: %s", row["article_id"])        
            article_id = row["article_id"]
            content = row["content"]
            chunks = []

            # Split article into chunks
            docs = text_splitter.split_text(content)

            search_start = 0
            for chunk in docs:
                index = content.find(chunk, search_start)
                if index == -1:
                    # іноді через пробіли/переноси не знаходиться — можна зробити щось складніше
                    index = content.find(chunk)
                start_index = index
                end_index = start_index + len(chunk)
                search_start = end_index  # оновлюємо позицію для наступного пошуку

                chunks.append({
                    "fk_article_id": article_id,
                    "start_index": start_index,
                    "end_index": end_index,
                    "is_processed": 0
                })
            chunk_df = pd.DataFrame(chunks)
            add_article_chunk_df(chunk_df)


if __name__ == "__main__":
    # Run data splitter
    try:
        logger.warning("Starting solo data splitting process")
        main(t_get_articles_without_chunks_input())
    except Exception as e:
        print(f"Error during data splitting: {e}")
        raise
