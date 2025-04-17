from graph.graph_config import create_app
from database.DBConnector import *
from utils.article_splitter import main as split_articles
from tqdm import tqdm

if __name__ == "__main__":

    # split_articles(t_get_articles_without_chunks_input())

    app = create_app() # Creating LangGraph

    unprocessed_chunks_df = t_get_unprocessed_chunks_input(is_processed=False)

    for _, row in tqdm(unprocessed_chunks_df.iterrows(), desc="Processing chunks", total=unprocessed_chunks_df.shape[0]):

        result = app.invoke(row.to_dict())

        break
    
    print("✅ Done")