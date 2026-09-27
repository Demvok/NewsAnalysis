from pathlib import Path

import pandas as pd

df = pd.read_csv(Path(__file__).with_name('dimArticle.csv'), index_col=0).reset_index('article_id')

def get_article_by_id(article_id):
    article = df[df['article_id'] == article_id]
    if not article.empty:
        return article.iloc[0].to_dict()
    else:
        return None