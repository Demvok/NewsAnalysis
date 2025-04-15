import os
import logging

import pandas as pd
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document

from tqdm import tqdm
from database.DBConnector import *

# # Initialize logging
# logging.basicConfig(
#     level=logging.INFO,
#     format="%(asctime)s - %(levelname)s - %(message)s",
#     handlers=[
#         logging.FileHandler("data_splitter.log"),
#         logging.StreamHandler(),
#     ]
# )
# logger = logging.getLogger(__name__)

def split_articles_to_chunks(chunk_size=2500, chunk_overlap=200):
    """
    Splits articles into smaller chunks using RecursiveCharacterTextSplitter and tracks chunk boundaries.

    Args:
        input_file (str): Path to the input CSV file with article data.
        output_file (str, optional): Path to save the output chunks. If None, results are not saved.
        chunk_size (int): Maximum number of characters per chunk.
        chunk_overlap (int): Number of overlapping characters between chunks.

    Returns:
        List[dict]: A list of chunked articles with metadata including start_index and end_index.
    """
    
    # try:
    #     # Load input file
    #     df = pd.read_csv(input_file)
    # except FileNotFoundError:
    #     raise FileNotFoundError(f"Input file '{input_file}' not found.")
    # except pd.errors.EmptyDataError:
    #     raise ValueError(f"Input file '{input_file}' is empty.")

    # # Validate necessary columns
    # required_columns = {"content", "title", "date", "#"}
    # if not required_columns.issubset(df.columns):
    #     raise ValueError(f"Input file must contain columns: {required_columns}")

    chunk_data = []

    df = t_get_articles_without_chunks_input()

    text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=chunk_size, chunk_overlap=chunk_overlap, separators=["\n\n", "\n", "."]
    )
    
    # Process each article separately
    for _, row in df.iterrows():
        content = row["content"]
        article_id = row["article_id"]

        # Initialize text splitter
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap, separators=["\n\n", "\n", "."]
        )

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

            chunk_data.append({
                "fk_article_id": article_id,
                "start_index": start_index,
                "end_index": end_index ,
                "is_processed": 0
            })

    chunk_df = pd.DataFrame(chunk_data)
    print("Uploading into database --------------------------------------------------------------")
    add_article_chunk_df(chunk_df)


if __name__ == "__main__":

    # Run data splitter
    try:
        split_articles_to_chunks()
    except Exception as e:
        print(f"Error during data splitting: {e}")
        raise
