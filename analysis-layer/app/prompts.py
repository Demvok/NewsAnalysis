from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from pydantic import BaseModel, Field
import json


class ModelCompatibleChatPromptTemplate(ChatPromptTemplate):
    """Chat prompt template that accepts mappings or Pydantic model instances."""

    def invoke(self, input, config=None, **kwargs):
        if isinstance(input, BaseModel):
            input = input.model_dump() if hasattr(input, "model_dump") else input.dict()
        return super().invoke(input, config=config, **kwargs)


# === Citation Extraction Prompt ===


CITATION_EXTRACTION_PROMPT = ModelCompatibleChatPromptTemplate.from_messages([
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
    speakers: list[ExtractedSpeaker] | None = Field(None, description="Speakers mentioned in the article and their citations")

    @staticmethod
    def get_speakers(data: dict) -> list[dict]:
        speakers = []
        for speaker in data.get('speakers', []):
            speaker = speaker.copy()
            speaker.pop('citations', None)
            speakers.append(speaker)
        return speakers

CITATION_EXTRACTION_REFINING_PROMPT = ModelCompatibleChatPromptTemplate.from_messages([
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

TOPIC_EXTRACTION_PROMPT = ModelCompatibleChatPromptTemplate.from_messages([
    (
        "system",
        """
            You are an expert researcher in political communication, public opinion,
            and stance analysis.

            Your task is to analyze the citations attributed to one speaker and identify
            the topics toward which the speaker expresses a position.

            For each citation:

            1. Identify the specific topics or issues discussed in the citation.
            2. For each identified topic, determine how directly the citation expresses
            a position toward that topic.
            3. Determine the speaker's stance toward each topic.
            4. Do not infer a stance when the citation only mentions a topic without
            expressing support, opposition, or another identifiable position.
            5. Do not invent topics or positions that are not supported by the citation.
            6. Use the surrounding citations from the same article only as context for
            understanding ambiguous references, pronouns, or incomplete statements.
            7. Evaluate the stance expressed in the citation, not whether the stance
            is factually correct.
            8. A citation may contain multiple topics and may express different stances
            toward them.
            9. If a citation contains no identifiable topic or stance, return an empty
            list of mentioned_topics.

            Stance scale:
            -10 = strongly against
            -5  = moderately against
            0   = neutral, unclear, or no identifiable stance
            +5  = moderately in favor
            +10 = strongly in favor

            Use intermediate values when appropriate.

            Relevancy score:
            0.0 = the citation does not meaningfully express a position about the topic
            0.5 = the topic is relevant but the position is only partially expressed
            1.0 = the citation directly expresses a clear position about the topic

            Return the result using the provided structured output schema.
        """
    ),
    (
        "human",
        """
            Speaker:
            {speaker_name}

            Role:
            {speaker_role}

            Aliases:
            {aliases}

            Citations:
            {citations}
        """
    ) 
])

class CitationTopic(BaseModel):
    topic_name: str = Field(..., description="A concise, canonical name of the topic or issue discussed in the citation")
    topic_description: str = Field(..., description="What it means for the speaker to be in favor of or against this topic, in a few sentences")
    relevancy_score: float = Field(..., ge=0, le=1, description="How directly the citation expresses a position about this topic, from 0 to 1")
    stance_detected: bool = Field(..., description="Whether the citation expresses an identifiable stance toward the topic")

class AnalysedCitation(CitationExtractionCitation):
    mentioned_topics: list[CitationTopic] = Field(..., description="A list of topics mentioned in the quote, each with their relevancy score and stance")

class TopicExtractionOutput(BaseModel):
    speaker_name: str = Field(..., description="The name of the speaker")
    speaker_role: str = Field(..., description="The role or affiliation of the speaker if he represents an organization")
    aliases: list[str] = Field(..., description="A list of aliases used for the speaker in the article")
    analysed_citations: list[AnalysedCitation] = Field(..., description="Citations attributed to the speaker with their topic evaluations")

CITATION_EVALUATION_PROMPT = ModelCompatibleChatPromptTemplate.from_messages([
    (
        "system",
        """
            You are an expert researcher in political communication, public opinion, and stance analysis. Your goal is to analyze the citations attributed to one speaker and identify the topics toward which the speaker expresses a position. Consider topic description, which tells what it means for the speaker to be in favor of or against this topic.
        """
    ),
    (
        "human",
        """
            Speaker:
            {speaker_name}

            Role:
            {speaker_role}

            Aliases:
            {aliases}

            Citations:
            {analysed_citations}
        """
    )
])

class CitationEvaluatedTopic(CitationTopic):
    stance: int | None = Field(None, ge=-10, le=10, description=(
        """The speaker's stance toward the topic, from -10 to 10.
        -10 means strongly against, 0 means neutral or no identifiable stance,
        and 10 means strongly in favor."""
    ))

class EvaluatedCitation(AnalysedCitation):
    evaluated_topics: list[CitationEvaluatedTopic] = Field(..., description="A list of topics mentioned in the quote, each with their relevancy score and stance")

class CitationEvaluationOutput(BaseModel):
    speaker_name: str = Field(..., description="The name of the speaker")
    speaker_role: str = Field(..., description="The role or affiliation of the speaker if he represents an organization")
    aliases: list[str] = Field(..., description="A list of aliases used for the speaker in the article")
    evaluated_citations: list[EvaluatedCitation] = Field(..., description="Citations attributed to the speaker with their topic evaluations and stances")