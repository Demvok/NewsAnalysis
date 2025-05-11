from config import FIELD_LENGTH_POLICY
from graph.graph_config import create_app
from database.DBConnector import *
# from utils.article_splitter import main as split_articles
# from tqdm import tqdm
from utils.logger import setup_logger

logger = setup_logger(name="main", log_file="graph.log")

if __name__ == "__main__":

    # split_articles(t_get_articles_without_chunks_input())

    app = create_app() # Creating LangGraph

    if FIELD_LENGTH_POLICY == "IGNORE":
        logger.debug("Ignoring field length policy")
    elif FIELD_LENGTH_POLICY == "RETRY":
        logger.debug("Retrying field length policy")
    elif FIELD_LENGTH_POLICY == "REFINE":
        logger.debug("Refining field length policy")
    elif FIELD_LENGTH_POLICY == "TRUNCATE":
        logger.debug("Truncating field length policy")
    else:
        logger.error(f"Unknown field length policy: {FIELD_LENGTH_POLICY}")
        raise ValueError(f"Unknown field length policy: {FIELD_LENGTH_POLICY}")

    unprocessed_chunks_df = t_get_unprocessed_chunks_input(is_processed=False)
    # .head(10) # Obviously temporary

    for _, row in unprocessed_chunks_df.iterrows():

        result = app.invoke(row.to_dict())

    
    print("✅ Done")