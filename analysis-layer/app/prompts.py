from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from pydantic import BaseModel, Field, field_validator
import json
from enum import Enum


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
        Extract every attributable statement, opinion, or position in the article.
        Group citations by speaker. Preserve direct wording exactly; for paraphrases
        use the article's wording as exact_quote and classify them accordingly.
        Keep enough context to resolve attribution and meaning. Include aliases
        exactly as used in the article. An alias is a name-like string that can
        help identify the real-world entity, not a role, affiliation, or person type.

        Do not invent speakers, quotes, or unsupported details. A mere mention is
        not a citation unless the article attributes a position or statement to the
        person. Use citation_type exactly as defined by the output schema.
        Classify each speaker as PERSON, ORGANIZATION, GROUP, ANONYMOUS_SOURCE, or
        UNRESOLVED. Use ANONYMOUS_SOURCE
        for anonymous descriptors such as "experts", "some people", "industry
        sources", "others", or "those behind AI data centres".
        Also score influence_score from 0 to 1: how much this speaker's evidence
        affects the article's main story, not how credible the speaker is. A low
        score means the evidence should be preserved but processed as lower priority.
        Set uncertain and explain uncertainty_reason when attribution or evidence is
        ambiguous. confidence_level and extraction_confidence are numbers from 0 to 1.
        Return the complete structured result, including an empty speakers list when
        no attributable statements exist.
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

class CitationType(str, Enum):
    DIRECT = "direct"
    PARTIAL = "partial"
    INDIRECT = "indirect"
    SUMMARY = "summary"
    POSITION = "position"
    MENTION = "mention"

class CitationExtractionCitation(BaseModel):
    exact_quote: str = Field(
        ...,
        min_length=1,
        description=(
            "The exact non-empty quote from the speaker in the article. "
            "If the article does not provide source wording, do not create a citation."
        ),
    )
    context: str = Field(..., description="The context of the quote in the article")
    citation_type: CitationType =  Field(description=(
        """Classify how a person's statement or opinion is presented in the article. 
        Choose exactly one type:\n
        - direct: The person's exact words are reproduced verbatim, normally within quotation marks. 
        The wording is presented as the person's own original speech.\n
        - partial: A combination of direct and indirect quotation. 
        Some words or phrases are reproduced verbatim, while the rest of the statement is paraphrased 
        or integrated into the journalist's sentence.\n
        - indirect: The article explicitly attributes a specific statement to the person, 
        but conveys its content entirely in the journalist's own words rather than reproducing the person's exact wording.\n
        - summary: The article summarizes the general content or main ideas of one or more statements 
        made by the person, without preserving a specific statement or its exact wording. 
        This is broader and less specific than an indirect quotation.\n
        - position: The article attributes a general position, belief, attitude, or stance to the person 
        without presenting a specific statement made by that person. 
        Use this when the text describes what the person believes, supports, opposes, or generally advocates, 
        rather than reporting a particular utterance.\n
        - mention: The person is merely mentioned or associated with the topic, but the article does not 
        attribute any statement, opinion, position, or stance to them. 
        Do not use this type if the text provides evidence of the person's expressed opinion."""
    ))
    summarized_quote: str = Field(..., description="A summarized version of the quote")
    confidence_level: float = Field(
        ...,
        ge=0,
        le=1,
        description="Confidence that the quote accurately represents the speaker's stance, from 0 to 1",
    )
    extraction_confidence: float = Field(
        ...,
        ge=0,
        le=1,
        description="Confidence in the extraction process, from 0 to 1",
    )

    @field_validator("confidence_level", "extraction_confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value: object) -> object:
        """Accept legacy 0-5 model output and store the canonical 0-1 value."""
        if isinstance(value, (int, float)) and 1 < value <= 5:
            return value / 5
        return value

class ExtractedSpeaker(BaseModel):
    speaker_name: str = Field(..., description="The name of the speaker")
    speaker_role: str = Field(..., description="The speaker's affiliation or role, stored separately from identity aliases")
    aliases: list[str] = Field(..., description="Name-like strings referring to the same real-world entity; never roles, affiliations, or type labels")
    speaker_kind: str = Field(
        default="UNRESOLVED",
        description=(
            "One of PERSON, ORGANIZATION, GROUP, ANONYMOUS_SOURCE, UNRESOLVED."
        ),
    )
    influence_score: float = Field(
        default=1,
        ge=0,
        le=1,
        description="Impact of this speaker's evidence on the article's main story.",
    )
    uncertain: bool = False
    uncertainty_reason: str = ""
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
        Audit the extraction against the original article and return a complete
        corrected extraction. Add omitted attributable statements, remove unsupported
        items, fix speaker attribution, and repair quote boundaries/type/context.
        Direct quotes must match the article character-for-character apart from
        surrounding quotation marks. Never fabricate or silently strengthen evidence.
        Preserve valid items and all schema fields. If evidence is insufficient,
        omit the citation rather than guessing.
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

COMBINED_CITATION_ANALYSIS_PROMPT = ModelCompatibleChatPromptTemplate.from_messages([
    (
        "system",
        """
        Analyze all citations for this speaker in one pass. For every citation,
        identify only topics that are actually supported by an attributable position,
        define each topic as a reusable proposition, and assign the speaker's stance
        from -10 (strongly against) to +10 (strongly supports). Do not infer a
        position from a bare mention. Preserve citation fields exactly.

        This is a batch-analysis identity, not necessarily a real-world identity.
        For PERSON, all citations in this batch belong to the same person.
        For ANONYMOUS_SOURCE or UNRESOLVED, the citations may come from many
        different unnamed sources: analyze each citation independently and never
        merge them into one person's position. The resulting attitudes are
        citation-level general-source observations, not a person profile.

        The speaker kind controls effort. For ANONYMOUS_SOURCE or UNRESOLVED,
        do basic topic/stance extraction only, avoid elaborate interpretation, and
        never produce material for historical inconsistency tracking. Mentions and
        citations without usable source text must have no evaluated topics. For
        PERSON speakers belong to one batch identity. Organizations are only valid
        here when explicitly enabled by the workflow; GROUP and non-person sources
        must not be treated as canonical people.

        topic_name must be concise and canonical. topic_description must be a
        proposition that can be meaningfully supported or opposed, including
        important scope, actor, object, or condition. Determine polarity from the
        proposition itself: stance > 0 means the speaker supports that proposition,
        stance < 0 means the speaker opposes it, and stance = 0 means neutral or
        indeterminate. Do not reverse polarity because the quote uses a negative
        verb; define the proposition first, then judge for/against it.
        relevancy_score is 0..1. stance_detected is false and stance is null when
        evidence is inadequate. Return one result for every input citation.
        """
    ),
    (
        "human",
        """
        Speaker: {speaker_name}
        Role: {speaker_role}
        Speaker kind: {speaker_kind}
        Citations: {citations}
        """
    ),
])

TOPIC_EXTRACTION_PROMPT = ModelCompatibleChatPromptTemplate.from_messages([
    (
        "system",
        """
            For each citation, identify each distinct topic that is actually discussed.
            Use the other citations only to resolve references or missing context.
            A topic needs an expressed or clearly attributed position to be useful:
            do not infer a stance from a bare mention. A citation may yield multiple
            topics and different stances. Judge what the speaker expresses, not truth.

            topic_name must be concise, specific, reusable, and free of article-specific
            phrasing. topic_description must define the proposition being evaluated:
            what supporting or opposing it means in this context. Keep it factual and
            compact, retaining essential scope, actor, object, and condition.
            Do not use stock wording or copy the quote. Set stance_detected false and
            return no topic when no position is supported.

            relevancy_score: 0 means no meaningful position, 0.5 partial, 1 clear.
            Use only the -10..10 stance scale in the next evaluation stage.
        """
    ),
    (
        "human",
        """
            Speaker:
            {speaker_name}

            Role:
            {speaker_role}

            Citations:
            {citations}
        """
    ) 
])

class CitationTopic(BaseModel):
    topic_name: str = Field(..., description="A concise, canonical name of the topic or issue discussed in the citation")
    topic_description: str = Field(..., description="What it means for the speaker to be in favor of or against this topic, in a few sentences")
    general_topic_field: str = Field(..., description="A stable broad subject area such as politics, economy, energy, health, or technology")
    relevancy_score: float = Field(..., ge=0, le=1, description="How directly the citation expresses a position about this topic, from 0 to 1")
    stance_detected: bool = Field(..., description="Whether the citation expresses an identifiable stance toward the topic")

class AnalysedCitation(CitationExtractionCitation):
    mentioned_topics: list[CitationTopic] = Field(..., description="A list of topics mentioned in the quote, each with their relevancy score and stance")

class TopicExtractionOutput(BaseModel):
    speaker_name: str = Field(..., description="The name of the speaker")
    speaker_role: str = Field(..., description="The speaker's affiliation or role, separate from identity aliases")
    aliases: list[str] = Field(..., description="Name-like strings referring to this real-world entity; never roles or type labels")
    analysed_citations: list[AnalysedCitation] = Field(..., description="Citations attributed to the speaker with their topic evaluations")

CITATION_EVALUATION_PROMPT = ModelCompatibleChatPromptTemplate.from_messages([
    (
        "system",
        """
            Assign a numeric stance to each evaluated topic using its proposition and
            the citation evidence. -10 is strongly against, 0 is neutral/unclear, and
            +10 is strongly in favor; use intermediate values. Set stance to null when
            stance_detected is false or evidence is inadequate. Do not change citation
            text or topics. Keep topic descriptions specific to the proposition, not a
            generic summary. Return only the structured output.
        """
    ),
    (
        "human",
        """
            Speaker:
            {speaker_name}

            Role:
            {speaker_role}

            Evaluated citations:
            {evaluated_citations}
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


class CombinedEvaluatedCitation(CitationExtractionCitation):
    evaluated_topics: list[CitationEvaluatedTopic] = Field(
        ..., description="Topics and stances supported by this citation"
    )


class CombinedCitationAnalysisOutput(BaseModel):
    speaker_name: str
    speaker_role: str
    evaluated_citations: list[CombinedEvaluatedCitation]


class InconsistencyComparison(BaseModel):
    classification: str = Field(description="CONSISTENT, POSITION_CHANGE, CONTRADICTION, or INSUFFICIENT_EVIDENCE")
    severity: str | None = None
    confidence: float = Field(ge=0, le=1)
    comment: str

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value: object) -> object:
        if isinstance(value, (int, float)) and 1 < value <= 5:
            return value / 5
        return value


INCONSISTENCY_COMPARISON_PROMPT = ModelCompatibleChatPromptTemplate.from_messages([
    ("system", """
        Compare two dated statements by the same person about the same proposition.
        Use CONSISTENT when compatible, POSITION_CHANGE when the later position differs
        without logical incompatibility, CONTRADICTION only for mutually incompatible
        claims, and INSUFFICIENT_EVIDENCE when scope, context, attribution, or dates
        do not support a decision. A changed numeric stance alone is not a contradiction.
        Explain the decisive evidence briefly. Return only the structured assessment.
    """),
    ("human", """
        Topic: {topic_name}
        Earlier statement: {earlier}
        Earlier stance: {earlier_stance}
        New statement: {newer}
        New stance: {newer_stance}
    """),
])