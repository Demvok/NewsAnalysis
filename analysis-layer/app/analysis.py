from __future__ import annotations

import logging

from . import LLM, models


logger = logging.getLogger(__name__)

def extract_citations(article: models.Article) -> models.CitationExtractionResult:
    """
    Extracts citations from the given article title and content.

    Args:
        article (Article): An Article object containing the article's title and content.

    Returns:
        list: A list of extracted citations.
    """
    if not article:
        raise ValueError("article is required")

    response = LLM.extract_citations(
        article_title=article.article_title or "",
        article_content=article.article_content or "",
    )
    result = models.CitationExtractionResult.model_validate(response.model_dump())
    logger.info("Extracted %d speakers", len(result.speakers))
    return result



def analyze_speaker_sentiment(speaker: dict):
    """
    Analyzes the sentiment of the given speaker's content.

    Args:
        speaker (dict): A dictionary containing the speaker's information and content.

    Returns:
        dict: A dictionary containing the sentiment analysis results.
    """
    return LLM.sentiment_analysis(speaker=speaker)
