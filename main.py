from graph.graph_config import create_app
from database.DBConnector import *
# from utils.article_splitter import main as split_articles
from tqdm import tqdm
from utils.logger import setup_logger, CONSOLE_LOG

logger = setup_logger(name="main", log_file="graph.log")

if __name__ == "__main__":
    
    # split_articles(t_get_articles_without_chunks_input())
    
    logger.info("Starting LangGraph")
    app = create_app() # Creating LangGraph

    unprocessed_chunks_df = t_get_unprocessed_chunks_input(first_run=True).head(10) # Obviously temporary

    for _, row in tqdm(
            unprocessed_chunks_df.iterrows(),
            total=unprocessed_chunks_df.shape[0],
            disable=not CONSOLE_LOG,
            desc="Processing chunks",
            unit="ch",
            dynamic_ncols=True,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]"):

        result = app.invoke(row.to_dict())

    
    print("✅ Done")