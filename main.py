from graph.graph_config import create_app
from database.DBConnector import *
from utils.article_splitter import main as split_articles
from tqdm import tqdm
tqdm.pandas()


# FUTURE DEPRECATION: подумав, що по факту це треба переробити у запит sql у dbloader
def get_unprocessed_chunks_input():
    extracted_df = pd.DataFrame(columns=['chunk_id', 'content'])
    ids = find_article_chunk_by_conditions(is_processed=0)
    extracted_df['chunk_id'] = ids
    extracted_df['topic'] = get_article_chunk_df(ids)['fk_article_id']
    extracted_df['topic'] = extracted_df['topic'].apply(lambda x: get_article(x)['fk_topic_id']).apply(lambda x: get_topic(x)['topic_name'])
    extracted_df['content'] = extracted_df['chunk_id'].apply(lambda x: t_get_article_chunk_content(x))
    return extracted_df


if __name__ == "__main__":

    # split_articles(t_get_articles_without_chunks_input())

    app = create_app() # Creating LangGraph

    unprocessed_chunks_df = get_unprocessed_chunks_input()


    result = app.invoke()
    print("✅ Done")