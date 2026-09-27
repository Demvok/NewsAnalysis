from graph.graph_config import create_app
from database.DBConnector import *
from tqdm import tqdm
from utils.logger import setup_logger, CONSOLE_LOG
from config import N_THREADS
import concurrent.futures

logger = setup_logger(name="main", log_file="graph.log")

def process_chunk(chunk_row):
    """Process a single chunk with its own graph instance"""
    # Create a new graph instance for thread safety
    thread_app = create_app()
    try:
        result = thread_app.invoke(chunk_row.to_dict())
        return result
    except Exception as e:
        logger.error(f"Error processing chunk {chunk_row.get('chunk_id', 'unknown')}: {str(e)}")
        return None

if __name__ == "__main__":
    
    logger.info("Starting LangGraph with parallel processing")

    unprocessed_chunks_df = t_get_unprocessed_chunks_input(first_run=True)
    # .head(10) # Obviously temporary
    
    # Convert DataFrame to list of rows for parallel processing
    chunk_rows = [row for _, row in unprocessed_chunks_df.iterrows()]
    
    # Create a ThreadPoolExecutor with n worker threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=N_THREADS) as executor:
        # Process chunks in parallel and track progress
        results = list(tqdm(
            executor.map(process_chunk, chunk_rows),
            total=len(chunk_rows),
            disable=not CONSOLE_LOG,
            desc="Processing chunks",
            unit="chunk",
            dynamic_ncols=True,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]"
        ))
    
    # Count successful processes
    successful = sum(1 for r in results if r is not None)
    logger.info(f"Processed {successful} of {len(chunk_rows)} chunks successfully")
    
    print("✅ Done")