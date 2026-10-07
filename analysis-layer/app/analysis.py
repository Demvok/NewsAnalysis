from analysis-layer.app import LLM


def extract_citations(article):
    """
    Extracts citations from the given article title and content.

    Args:
        article (dict): A dictionary containing the article's title and content.

    Returns:
        list: A list of extracted citations.
    """
    article_title = article.get('title', '')
    article_content = article.get('content', '')

    citations = LLM.extract_citations(article_title=article_title, article_content=article_content)

    return citations


def analyze_speaker_sentiment(speaker):
    """
    Analyzes the sentiment of the given speaker's content.

    Args:
        speaker (dict): A dictionary containing the speaker's information and content.

    Returns:
        dict: A dictionary containing the sentiment analysis results.
    """
    sentiment_result = LLM.sentiment_analysis(speaker=speaker)
    return sentiment_result
