from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from pydantic import BaseModel, Field



# === Citation Extraction Prompt ===


CITATION_EXTRACTION_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        """
        You are an expert journalist specializing in extracting statements
        and opinions expressed by people in news articles.

        Your task is to identify all relevant speakers and extract their citations.

        For each citation:
        - preserve the exact wording when it is a direct quote;
        - identify paraphrased statements;
        - provide the context necessary to understand the citation;
        - summarize the citation;
        - identify the speaker correctly;
        - include aliases used to refer to the same person.

        Do not invent quotes, speakers, or information that is not supported by the article.
        """
    ),
    (
        "human",
        """
        Article title:
        {article_title}

        Article:
        {article_content}
        """
    ),
])

class CitationExtractionCitation(BaseModel):
    exact_quote: str = Field(..., description="The exact quote from the speaker in the article")
    context: str = Field(..., description="The context of the quote in the article")
    citation_type: str = Field(..., description="The type of citation, e.g. direct quote or paraphrase")
    summarized_quote: str = Field(..., description="A summarized version of the quote")
    confidence_level: float = Field(..., description="Confidence that the quote accurately represents the speaker's stance, from 0 to 1")
    extraction_confidence: float = Field(..., description="Confidence in the extraction process, from 0 to 1")

class ExtractedSpeaker(BaseModel):
    speaker_name: str = Field(..., description="The name of the speaker")
    speaker_role: str = Field(..., description="The role or affiliation of the speaker if he represents an organization")
    aliases: list[str] = Field(..., description="A list of aliases used for the speaker in the article")
    citations: list[CitationExtractionCitation] = Field(..., description="Citations attributed to the speaker")

class CitationExtractionOutput(BaseModel):
    article_title: str = Field(..., description="The title of the article")
    status: str = Field(..., description="The processing status: PROCESSED or UNPROCESSED")
    speakers: list[ExtractedSpeaker] = Field(..., description="Speakers mentioned in the article and their citations")

CITATION_EXTRACTION_REFINING_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        """
        You are reviewing the result of an automated citation extraction.

        Compare the extracted citations against the original article.

        Your task is to:
        - identify missing citations;
        - remove citations that are not supported by the article;
        - correct incorrectly attributed citations;
        - correct incomplete or inaccurate quotes;
        - improve speaker identification;
        - preserve correct information from the original extraction.

        Do not invent information that is not supported by the article.

        Return the complete corrected extraction.
        """
    ),
    (
        "human",
        """
        Original article title:
        {article_title}

        Original article:
        {article_content}

        Initial extraction:
        {extraction}
        """
    ),
])


# === Citation Evaluation Prompt ===

CITATION_EVALUATION_PROMPT = ChatPromptTemplate.from_messages([
    (
        'system',
        'You are an expert social analytic and public opinion researcher. Your goal is to view each of the opinions the person has made and form some conclusions. Format the result in json of given type. Quotes can have no topics or stance mentioned, leave it empty. All of the quotes for the person in the origin article are given for context.'
    ),
    (        
        'human',
        "Speaker:\n\n{speaker_name}\n\nRole:\n\n{speaker_role}\n\nAliases:\n\n{aliases}\n\nCitations:\n\n{citations}"
    )
])

class CitationEvaluationTopic(BaseModel):
    topic_name: str = Field(..., description="The name of the topic mentioned in the quote")
    relevancy_score: float = Field(..., description="How much the quote is relevant to the topic, on a scale of 0 to 1")
    stance: int = Field(..., description="Person's stance on the topic, on the scale form -10 to 10, where -10 is strongly against, 0 is neutral, and 10 is strongly in favor")

class EvaluatedCitation(CitationExtractionCitation):
    mentioned_topics: list[CitationEvaluationTopic] = Field(..., description="A list of topics mentioned in the quote, each with their relevancy score and stance")

class CitationEvaluationOutput(BaseModel):
    speaker_name: str = Field(..., description="The name of the speaker")
    speaker_role: str = Field(..., description="The role or affiliation of the speaker if he represents an organization")
    aliases: list[str] = Field(..., description="A list of aliases used for the speaker in the article")
    evaluated_citations: list[EvaluatedCitation] = Field(..., description="Citations attributed to the speaker with their topic evaluations")