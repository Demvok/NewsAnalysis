# NewsAnalysis — Copilot Instructions

## 1. Project Overview

NewsAnalysis is a modular AI-powered system for analyzing news articles and extracting structured information about public persons, their statements, topics, attitudes, and changes or contradictions in their positions over time.

The system is designed around a deterministic data-processing pipeline rather than an autonomous AI agent.

The primary analytical goal is:

> Extract statements made by public persons from news articles, associate those statements with canonical persons and semantic topics, determine their stance toward those topics, and identify meaningful changes or contradictions in their positions over time.

The system must preserve traceability from every analytical conclusion back to the original article and citation that provided the evidence.

The project is intended to evolve toward:

* automated article ingestion;
* structured semantic analysis;
* historical attitude tracking;
* inconsistency and position-change detection;
* visualization;
* and, later, an agentic interface for querying the accumulated knowledge.

The current implementation is a modular Docker-based system in which independent services communicate through APIs.

---

# 2. Core Architectural Principles

The following principles are mandatory unless there is a strong technical reason to deviate from them.

## 2.1 Separation of responsibilities

The system consists of independent modules with clearly defined responsibilities.

Current modules:

```text
data-ingestion
    ↓
orchestrator
    ↓
analysis-layer
    ↓
data-storage
    ↓
PostgreSQL + pgvector
```

Future modules:

```text
visualization
agent
```

The database itself is infrastructure and must not contain application-level business logic.

`data-storage` is the API layer over the database.

`analysis-layer` contains analytical logic and LLM pipelines.

`data-ingestion` obtains and normalizes external article data.

`orchestrator` coordinates workflows and processing state.

`visualization` presents already structured data.

`agent` will later provide an interactive interface over the existing structured knowledge.

---

## 2.2 The orchestrator is deterministic

The orchestrator is responsible for workflow coordination, not analytical reasoning.

It should:

* determine what needs to be processed;
* invoke appropriate services;
* manage processing states;
* retry failed operations when appropriate;
* prevent duplicate processing;
* resume interrupted workflows;
* and coordinate multi-stage analysis.

It must NOT:

* implement citation extraction;
* implement topic classification;
* implement stance classification;
* implement entity resolution;
* implement inconsistency reasoning.

Those responsibilities belong to `analysis-layer`.

---

## 2.3 LLM is an analytical component, not the source of truth

LLM output must never be treated as an authoritative database.

LLM is used for operations such as:

```text
extract
refine
classify
summarize
resolve
compare
```

The resulting structured information must be persisted in PostgreSQL.

Important conclusions must always be traceable to source evidence.

The system should always be able to answer:

> Why was this conclusion produced?

by following relationships such as:

```text
Inconsistency
    ↓
Attitude A / Attitude B
    ↓
Citation A / Citation B
    ↓
Article A / Article B
```

---

# 3. Main Data Model

The conceptual data model is:

```text
Article
   │
   └── Citation
          │
          ├── Person
          │
          └── Attitude
                 │
                 └── Topic
```

Historical comparison operates on attitudes:

```text
Person
   │
   ├── Attitude A ── Topic X ── Citation A ── Article A
   │
   ├── Attitude B ── Topic X ── Citation B ── Article B
   │
   └──────────── Inconsistency ──────────────┘
```

The core semantic entities are:

### Article

A source document containing the information being analyzed.

### Person

A canonical real-world person.

### Person Mention / Person Candidate

A temporary representation of a person mention extracted from an article before entity resolution.

### Citation

A statement or opinion attributed to a person and extracted from an article.

### Topic

A canonical semantic subject toward which a person may have an attitude.

### Attitude

A person's stance toward a specific topic as expressed by a specific citation.

### Inconsistency

A relationship between two or more attitudes that indicates a meaningful contradiction or potentially significant change in position.

---

# 4. Person Resolution

Do not implement a complex classical NER/coreference pipeline unless explicitly requested.

The preferred approach is:

```text
Article
   ↓
LLM extraction
   ↓
Person mentions
   ↓
Person candidates
   ↓
Entity resolution
   ↓
Canonical Person
```

Examples:

```text
"Joe Biden"
"Joseph Biden"
"President Biden"
"the former president"
```

may refer to the same canonical person.

The initial extraction should preserve the original surface form.

A person mention should not immediately be assumed to be a canonical person.

Prefer a two-stage model:

```text
PersonMention / PersonCandidate
        ↓
Entity Resolution
        ↓
DimPerson
```

Entity resolution may use:

1. exact or normalized matching;
2. aliases;
3. metadata;
4. semantic similarity;
5. LLM comparison of candidate entities.

Embeddings should be used for candidate retrieval rather than treated as an authoritative identity decision.

When the system is uncertain, it is preferable to preserve ambiguity rather than create a false merge.

Never silently merge two persons solely because their names are similar.

---

# 5. Citation Extraction Pipeline

Citation extraction is a multi-stage process.

Preferred workflow:

```text
Article
   ↓
Citation Extraction Prompt
   ↓
Raw Citation Candidates
   ↓
Refining Prompt
   ↓
Validated CitationExtractionOutput
   ↓
Database
```

The original article content must remain available during refinement.

The refinement stage may correct:

* attribution;
* citation boundaries;
* citation type;
* context;
* summary;
* confidence.

However, the original quote must remain unchanged once accepted.

Important citation fields include:

```text
exact_quote
context
citation_type
summarized_quote
confidence_level
extraction_confidence
```

`exact_quote` is primary evidence.

`summarized_quote` is derived information and must never replace `exact_quote`.

Do not generate a citation if the evidence is insufficient.

---

# 6. Citation Types

Citation type must use a strict controlled vocabulary.

The classification must distinguish between direct statements and statements reported or paraphrased by someone else.

Examples of conceptual categories include:

```text
DIRECT
DIRECT_QUOTE
INDIRECT
PARAPHRASE
REPORTED_STATEMENT
```

The exact Pydantic enum currently defined in the project is authoritative.

When modifying the enum, preserve semantic distinctions and update all dependent prompts and validators.

Do not allow arbitrary natural-language citation types.

---

# 7. Batch Analysis Strategy

The preferred unit of analytical processing is:

```text
one article + one person + all citations belonging to that person
```

Example:

```text
Article A

Person X
    Citation 1
    Citation 2
    Citation 3

Person Y
    Citation 4
    Citation 5
```

The system should analyze:

```text
Person X + Citation 1 + Citation 2 + Citation 3
```

as one semantic unit.

Do NOT automatically analyze every citation independently.

The reason is that multiple citations from the same article may form a single coherent opinion and require context from one another.

Batch processing should not mix unrelated persons.

---

# 8. Topic Extraction and Canonicalization

Topics must be canonical entities.

The system must avoid creating duplicate topics such as:

```text
"US support for Ukraine"
"American support for Ukraine"
"US aid to Ukraine"
"American military aid to Ukraine"
```

when they represent the same semantic topic.

Topic creation should therefore follow:

```text
New topic candidate
        ↓
Embedding search
        ↓
Top-K existing topics
        ↓
Semantic comparison
        ↓
Existing topic OR new topic
```

Embedding similarity is a retrieval mechanism, not the final decision.

If a potentially similar topic exists, an additional semantic evaluation may determine whether:

* it is the same topic;
* it is a broader/narrower topic;
* it is related but distinct;
* or it is unrelated.

Do not rely exclusively on a fixed cosine-similarity threshold.

The threshold should be configurable.

---

# 9. Topic Representation

A topic should contain at least:

```text
topic_uuid
topic_name
topic_description
topic_description_vector
general_topic_field
```

`topic_name` should be concise and canonical.

`topic_description` should explain what the topic means.

The description should be sufficiently precise to distinguish the topic from nearby concepts.

For example:

```text
Topic:
"US military aid to Ukraine"

Description:
"United States provision of military assistance, weapons,
equipment, or related defense support to Ukraine."
```

The topic description should also support stance interpretation.

Where appropriate, the analytical representation should make clear how a person can meaningfully support or oppose the topic.

---

# 10. Attitude Model

An attitude represents a person's stance toward a canonical topic based on a specific citation.

Conceptually:

```text
Citation
    +
Topic
    ↓
Attitude
```

An attitude should include:

```text
stance
relevancy_score
stance_summary
stance_confidence
```

Possible stance categories must be controlled through a strict schema.

Do not infer a contradiction merely because two stance labels differ.

For example:

```text
SUPPORT → NEUTRAL
```

does not automatically mean contradiction.

A change of position and a contradiction are separate concepts.

---

# 11. Position Change vs Contradiction

This distinction is critical.

A person can legitimately change their position over time.

Therefore:

```text
different historical positions
≠
contradiction
```

The system should distinguish at least:

```text
CONSISTENT
POSITION_CHANGE
CONTRADICTION
INSUFFICIENT_EVIDENCE
```

A contradiction should only be recorded when the available evidence supports the conclusion that the two statements are meaningfully incompatible.

The analysis should consider:

* semantic meaning;
* topic;
* context;
* temporal distance;
* qualification;
* conditional statements;
* scope;
* whether the person was discussing the same proposition;
* whether the apparent change can reasonably be explained by context.

Do not use:

```text
stance_a != stance_b
```

as the sole contradiction criterion.

---

# 12. Inconsistency Detection

Inconsistency analysis is triggered when a new attitude is added.

Preferred workflow:

```text
New Attitude
    ↓
Person
    ↓
Canonical Topic
    ↓
Historical attitudes for same person + topic
    ↓
Configured time window
    ↓
Candidate historical attitudes
    ↓
Semantic comparison
    ↓
Consistency / Position Change / Contradiction
```

The default historical period should be configurable.

The system should not compare a new attitude against every attitude ever expressed by the person.

First narrow candidates using:

```text
same person
+
same canonical topic
+
time window
```

Only then perform semantic comparison.

This reduces:

* computational cost;
* LLM calls;
* false positives.

---

# 13. Inconsistency Data Model

Inconsistency is a relationship between pieces of evidence, not a simple property of one attitude.

Prefer a separate entity/table such as:

```text
FctInconsistency
```

with conceptual fields:

```text
inconsistency_uuid
person_uuid
topic_uuid

citation_a_uuid
citation_b_uuid

attitude_a
attitude_b

classification
severity
confidence

inconsistency_comment
detected_at
```

If attitudes receive their own stable UUIDs, inconsistency should preferably reference attitude UUIDs rather than duplicating attitude data.

The system must preserve the evidence pair used for the conclusion.

---

# 14. Confidence Model

Different confidence values represent different concepts and must not be conflated.

Use separate concepts where appropriate:

```text
extraction_confidence
    Confidence that the extracted citation is valid.

citation_confidence
    Confidence in attribution and citation interpretation.

relevancy_score
    Degree to which the citation is relevant to the topic.

stance_confidence
    Confidence in the assigned stance.

entity_resolution_confidence
    Confidence that a mention corresponds to the selected person.

topic_resolution_confidence
    Confidence that a candidate topic matches an existing canonical topic.

inconsistency_confidence
    Confidence in the detected contradiction.
```

Do not use a generic `confidence` field when the underlying concepts are materially different.

Low-confidence evidence should be prevented from automatically producing high-confidence conclusions.

---

# 15. Provenance and Traceability

Every derived analytical result must be traceable.

The system should preserve:

```text
Article
    ↓
Citation
    ↓
Topic
    ↓
Attitude
    ↓
Inconsistency
```

A user should be able to inspect an inconsistency and determine:

1. Which person was involved.
2. Which topic was involved.
3. Which two citations were compared.
4. Which articles contained those citations.
5. What the original quotes were.
6. What the system summarized from them.
7. What the stance classification was.
8. Why the system classified the statements as inconsistent.

Never discard source evidence after generating a derived result.

---

# 16. Database Principles

PostgreSQL is the primary relational database.

pgvector is used for semantic retrieval.

Use relational foreign keys for authoritative entity relationships.

Use vector search for:

* topic candidate retrieval;
* person candidate retrieval where useful;
* semantic search over citations;
* future analytical retrieval.

Do not use embeddings as replacements for relational identity.

For example:

```text
person_uuid
topic_uuid
citation_uuid
article_id
```

are authoritative identifiers.

Vectors are search representations only.

---

# 17. Current Database Model

The current model contains:

```text
DimArticle
DimPerson
DimCitation
DimTopic
FctAttitude
```

This model should evolve toward:

```text
DimArticle
DimPerson
DimPersonMention / DimPersonCandidate
DimCitation
DimTopic
FctAttitude
FctInconsistency
```

Potential processing state tables may also be introduced if necessary.

Avoid unnecessary dimensional-model complexity.

The names `Dim*` and `Fct*` are retained because the project follows a dimensional/data-warehouse-inspired semantic organization.

---

# 18. Processing State

Article processing must be resumable.

Do not rely exclusively on in-memory state.

An article may pass through states conceptually similar to:

```text
NEW
INGESTED
CITATIONS_EXTRACTED
PERSONS_RESOLVED
TOPICS_ANALYZED
ATTITUDES_ANALYZED
INCONSISTENCIES_ANALYZED
COMPLETED
FAILED
```

The exact enum may differ, but states must be explicit and machine-readable.

A failed stage should be retryable without unnecessarily repeating completed stages.

For example:

```text
CITATIONS_EXTRACTED
        ↓
PERSON_RESOLUTION_FAILED
```

A retry should normally begin with person resolution rather than re-extracting citations.

---

# 19. Idempotency

Every workflow stage should be designed to avoid duplicate data.

Repeated execution of:

```text
process(article_id)
```

must not create duplicate:

* articles;
* persons;
* citations;
* topics;
* attitudes;
* inconsistencies.

Prefer database constraints and deterministic identifiers where possible.

Do not rely only on application-level checks.

---

# 20. Transaction Boundaries

Each logically atomic database operation should use an appropriate transaction.

Do not leave partially written analytical states.

For example, when persisting an attitude:

```text
citation
+
topic relation
+
attitude
```

must be written consistently.

However, do not create enormous transactions around an entire article pipeline.

Long-running LLM calls should generally occur outside database transactions.

Preferred pattern:

```text
read data
    ↓
LLM processing
    ↓
validate result
    ↓
short DB transaction
    ↓
commit
```

---

# 21. API Design

Services communicate over HTTP APIs.

Each service should expose a clean contract.

Do not allow one service to directly access another service's database.

For example:

```text
analysis-layer
    → data-storage API
```

is correct.

Direct:

```text
analysis-layer
    → PostgreSQL
```

is not correct if PostgreSQL is owned by `data-storage`.

The database should have a single logical owner.

API schemas should use explicit Pydantic models.

Do not pass unstructured dictionaries between services when a stable schema can be defined.

---

# 22. Analysis Layer

The analysis layer contains reusable analytical operations.

Examples:

```text
citation extraction
citation refinement
person resolution
topic resolution
attitude analysis
inconsistency analysis
summarization
```

Each operation should be independently callable and testable.

Avoid putting workflow orchestration inside individual analytical functions.

Bad:

```python
analyze_article()
    extract()
    save()
    resolve()
    save()
    compare()
    save()
```

Preferred:

```python
extract_citations()
resolve_persons()
resolve_topics()
analyze_attitudes()
detect_inconsistencies()
```

The orchestrator decides when these functions are called.

---

# 23. LLM Prompt Design

Prompts must be explicit and structured.

Prefer:

```text
ChatPromptTemplate
+
Pydantic structured output
```

over manually parsing JSON.

LLM outputs must be validated.

If a field has a controlled vocabulary, use a Pydantic `Enum` or equivalent strict schema.

Prompts should explain:

* task;
* definitions;
* constraints;
* edge cases;
* output semantics.

Avoid vague instructions such as:

```text
"Analyze this person."
```

Prefer precise definitions.

For every analytical category, define what qualifies and what does not qualify.

---

# 24. LLM Pipeline Design

Each LLM call should perform one logically coherent task.

Prefer:

```text
Extraction
→ Refinement
→ Evaluation
→ Resolution
→ Comparison
```

rather than one giant prompt that attempts to perform everything.

This makes the system:

* easier to test;
* easier to debug;
* easier to benchmark;
* easier to replace with another model;
* easier to reproduce.

Avoid unnecessary LLM calls.

Use deterministic processing where deterministic logic is sufficient.

---

# 25. Embedding Strategy

Embeddings should support retrieval, not replace reasoning.

Current important vector representations include:

```text
topic_description_vector
summarized_quote_vector
```

Potential future vectors:

```text
person description
citation
stance summary
inconsistency representation
```

Embedding model configuration must be centralized.

Embedding dimensions must be consistent with the PostgreSQL `pgvector` schema.

Do not silently change embedding models without considering:

* vector dimensionality;
* semantic compatibility;
* existing stored vectors;
* re-embedding requirements.

---

# 26. Error Handling

Errors should be categorized.

Examples:

```text
validation error
API error
database error
LLM error
timeout
rate/resource error
semantic resolution failure
```

Do not silently swallow errors.

Each failed stage should produce enough information to diagnose:

* article ID;
* processing stage;
* error type;
* relevant request/correlation ID;
* retryability.

LLM failures should be retryable where appropriate.

Validation failures should not automatically be retried indefinitely.

---

# 27. Logging

Use structured logging.

Important fields should include:

```text
timestamp
service
article_id
person_uuid
citation_uuid
topic_uuid
workflow_stage
request_id
model
prompt/version
duration
status
error
```

Do not log full article contents or sensitive information unnecessarily.

LLM prompts and responses may be logged selectively for development/debugging, but production logging should avoid uncontrolled data duplication.

---

# 28. Prompt and Model Versioning

Analytical results depend on:

* prompt;
* model;
* model parameters;
* embedding model;
* schema version.

Where practical, persist metadata such as:

```text
model_name
prompt_version
analysis_version
```

This allows future comparison of results after changing prompts or models.

Do not silently assume that two results generated by different prompt/model versions are equivalent.

---

# 29. Testing Strategy

Each analytical component must be independently testable.

Important test categories:

### Unit tests

Test:

* Pydantic validation;
* citation parsing;
* normalization;
* status transitions;
* similarity thresholds;
* database mappings.

### Integration tests

Test:

```text
analysis-layer
    ↔
data-storage
    ↔
PostgreSQL
```

### Pipeline tests

Use a small fixed corpus of representative articles.

Test:

```text
article
→ extraction
→ refinement
→ person resolution
→ topic resolution
→ attitude
→ inconsistency
```

### Regression tests

Keep representative LLM outputs and expected structured results.

When prompts change, regression tests should reveal unexpected changes.

Do not require exact natural-language equality when semantic equivalence is more appropriate.

---

# 30. Development Workflow

Development is incremental.

Do not require the entire Docker system to be rebuilt after every code change.

Each module should be independently runnable and testable.

During development:

```text
implement one operation
    ↓
unit test
    ↓
API test
    ↓
integration test
    ↓
connect to orchestrator
```

Prefer short feedback loops.

Docker images should be rebuilt only when dependencies or image-level configuration change.

Source code should be mounted or otherwise configured for efficient development where appropriate.

---

# 31. Docker Architecture

Each logical module should have its own container.

Current services conceptually include:

```text
postgres
data-storage
data-ingestion
analysis-layer
orchestrator
```

Future:

```text
visualization
agent
```

Services should communicate using Docker Compose service names rather than hardcoded localhost addresses.

Inside containers:

```text
http://data-storage:8000
```

is conceptually correct.

Do not use:

```text
http://localhost:8000
```

to communicate with another container.

`localhost` refers to the current container.

---

# 32. Configuration

Configuration should come from environment variables or service-specific configuration.

Separate:

* application configuration;
* secrets;
* infrastructure configuration;
* model configuration.

Never hardcode:

* API keys;
* database passwords;
* service URLs;
* model paths;
* credentials.

Configuration should be explicit and validated at startup.

---

# 33. Future Agent Module

The future agent module is NOT part of the deterministic analysis pipeline.

Its responsibility will be to interact with the existing knowledge base.

Potential operations:

```text
find_person
find_topic
search_citations
get_attitudes
get_historical_positions
get_inconsistencies
compare_periods
```

The agent should preferably call deterministic APIs/tools rather than directly manipulating the database.

The agent should not recreate the analytical pipeline unnecessarily.

For example:

```text
User:
"How did X's position on topic Y change?"

Agent:
    → resolve X
    → resolve Y
    → retrieve historical attitudes
    → retrieve supporting citations
    → synthesize answer
```

The agent is an interface over structured knowledge, not the database itself.

---

# 34. Future Visualization Module

Visualization should consume structured API data.

Potential visualizations include:

```text
person → topic → stance
stance timeline
position changes
inconsistency graph
citation evidence
topic similarity
semantic embedding space
```

Visualization must not contain analytical business logic.

For example, the frontend should not determine whether two citations are contradictory.

That decision belongs to the analysis layer.

---

# 35. Embedding Visualization

If high-dimensional embeddings are visualized, dimensionality reduction methods such as:

```text
PCA
UMAP
t-SNE
```

may be used.

A 3D visualization must be understood as a projection, not as a literal representation of the original embedding space.

Do not interpret arbitrary geometric movement in a 3D projection as direct semantic truth.

Visualization is exploratory and should not replace analytical evaluation.

---

# 36. Data Quality Principles

Prefer preserving uncertain information over fabricating certainty.

If the system cannot confidently determine:

* who said something;
* what topic it concerns;
* what the stance is;
* whether two statements are contradictory;

the result should indicate uncertainty.

Do not force every input into a classification.

Use explicit states such as:

```text
UNKNOWN
UNCERTAIN
INSUFFICIENT_EVIDENCE
```

where appropriate.

---

# 37. Important Semantic Rules

Never assume:

```text
different wording = different topic
```

Never assume:

```text
similar wording = same person
```

Never assume:

```text
different stance = contradiction
```

Never assume:

```text
high embedding similarity = same entity
```

Never assume:

```text
LLM confidence = factual truth
```

The system must combine:

* structured data;
* source evidence;
* deterministic constraints;
* semantic retrieval;
* LLM reasoning.

---

# 38. Code Quality Rules

Prefer simple, explicit code.

Avoid premature abstractions.

Do not introduce a framework or dependency unless it solves a concrete project requirement.

Use type hints consistently.

Prefer Pydantic models for service/API boundaries.

Prefer SQLAlchemy models for persistence.

Keep database models separate from API DTOs where the distinction matters.

Avoid leaking ORM models directly through public APIs.

Functions should have one clear responsibility.

Large functions should be decomposed.

Do not duplicate business rules across services.

---

# 39. Backward Compatibility

When changing schemas or APIs:

1. Identify existing consumers.
2. Update schemas deliberately.
3. Add migrations where necessary.
4. Update tests.
5. Update prompts and structured output definitions.
6. Update dependent services.
7. Do not silently break API contracts.

Database migrations should be preferred over destructive recreation when existing analytical data matters.

---

# 40. Decision-Making Priority

When proposing or implementing a change, prioritize in this order:

1. Correctness of analytical semantics.
2. Preservation of source evidence.
3. Data integrity.
4. Reproducibility.
5. Clear service boundaries.
6. Testability.
7. Performance.
8. Development convenience.
9. Additional abstraction.

Do not sacrifice semantic correctness merely to reduce code.

Do not introduce architectural complexity merely because it is technically possible.

---

# 41. Preferred End-to-End Workflow

The target workflow is:

```text
                    ARTICLE
                       │
                       ▼
              ┌────────────────┐
              │ Data Ingestion │
              └───────┬────────┘
                      │
                      ▼
               DimArticle
                      │
                      ▼
             Citation Extraction
                      │
                      ▼
                Refinement
                      │
                      ▼
                  Citations
                      │
                      ├───────────────┐
                      ▼               ▼
              Person Candidates   Citation data
                      │
                      ▼
               Person Resolution
                      │
                      ▼
                Canonical Person
                      │
                      ▼
          Person + Article Citation Batch
                      │
                      ▼
              Topic Identification
                      │
                      ▼
               Topic Resolution
                      │
                      ▼
              Canonical Topics
                      │
                      ▼
               Attitude Analysis
                      │
                      ▼
                  Attitudes
                      │
                      ▼
            Historical Retrieval
                      │
                      ▼
            Inconsistency Analysis
                      │
              ┌───────┴────────┐
              ▼                ▼
          Position Change   Contradiction
                               │
                               ▼
                         Explanation
```

The orchestrator coordinates this process.

---

# 42. Target Service Responsibilities

## data-ingestion

Responsible for:

* collecting articles;
* parsing external sources;
* normalizing article metadata;
* detecting duplicate URLs where appropriate;
* submitting articles to the storage service.

Must not perform semantic analysis.

---

## data-storage

Responsible for:

* persistence;
* retrieval;
* database transactions;
* relational queries;
* vector search;
* API contracts for data access.

Must not make LLM decisions.

---

## analysis-layer

Responsible for:

* LLM prompts;
* structured output;
* citation extraction;
* citation refinement;
* person resolution;
* topic resolution;
* attitude analysis;
* inconsistency analysis;
* embedding generation where analytically required.

Must not own the database.

---

## orchestrator

Responsible for:

* workflow execution;
* stage ordering;
* retries;
* processing states;
* idempotency coordination;
* triggering analysis;
* recovery from failures.

Must not contain analytical reasoning.

---

## visualization

Responsible for:

* querying analytical APIs;
* displaying evidence;
* timelines;
* relationships;
* trends;
* embeddings;
* inconsistencies.

Must not modify analytical conclusions.

---

## agent

Future responsibility:

* natural-language interaction;
* retrieval;
* tool/API selection;
* synthesis;
* explanation.

Must not become a replacement for deterministic backend logic.

---

# 43. When Modifying the Project

Before making a significant change, inspect:

```text
database schema
API schemas
Pydantic models
existing prompts
workflow states
service dependencies
tests
Docker configuration
```

Do not modify one layer while assuming the others will automatically remain compatible.

When adding a new field:

* identify whether it is source data, derived data, or metadata;
* determine its owner;
* define validation;
* update persistence;
* update API schemas;
* update prompts if necessary;
* update tests.

---

# 44. When Unsure

Prefer asking for clarification when a decision changes:

* database semantics;
* API contracts;
* entity identity;
* analytical definitions;
* or workflow behavior.

Do not silently choose a semantic interpretation when multiple interpretations are plausible.

For implementation details that do not affect semantics, choose the simplest maintainable solution.

---

# 45. Current Architectural Goal

The immediate goal is NOT to implement every future feature.

The immediate goal is to establish a reliable deterministic analytical pipeline:

```text
Article
→ Citation
→ Person
→ Topic
→ Attitude
→ Historical Comparison
→ Inconsistency
```

with:

```text
Dockerized services
+
API boundaries
+
PostgreSQL
+
pgvector
+
Pydantic schemas
+
structured LLM output
+
reproducible processing
+
source traceability
```

Only after this foundation is stable should the project expand toward:

* visualization;
* autonomous agents;
* more advanced retrieval;
* large-scale automation;
* distributed processing.

---

# 46. Guiding Principle

The system should transform unstructured news into a structured, traceable representation of public-person positions over time.

The central design principle is:

> Preserve evidence first, derive structured knowledge second, and generate interpretations only from that structured evidence.

Every implementation decision should support this principle.

# Incremental Implementation

This document describes the target architecture, not a request to
rewrite the entire existing project.

Do not implement all architectural changes at once.

When asked to implement a change:
1. Inspect the existing implementation first.
2. Identify the smallest coherent implementation step.
3. Preserve currently working functionality.
4. Do not refactor unrelated modules.
5. Do not introduce future modules unless explicitly requested.
6. Do not make semantic decisions that are not specified.
7. For major architectural changes, propose a plan before modifying code.
8. Keep each implementation step independently testable.


Phase 1 — Database
✓ schema finalized
✓ models implemented
✓ migrations
✓ constraints
✓ tests

Phase 2 — Storage API
✓ CRUD
✓ search
✓ vector search
✓ batch operations
✓ tests

Phase 3 — Analysis
✓ extraction
✓ refinement
✓ person resolution
✓ topic resolution
✓ attitude

Phase 4 — Inconsistency
✓ historical retrieval
✓ candidate selection
✓ comparison
✓ persistence

Phase 5 — Orchestration
✓ workflow
✓ state machine
✓ retries
✓ recovery
✓ idempotency