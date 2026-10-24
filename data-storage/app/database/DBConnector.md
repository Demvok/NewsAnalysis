# Data-storage database

`DBConnector.py` is the single SQLAlchemy ORM for the MVP schema in
[`app/input.md`](../input.md). It uses PostgreSQL with the `pgvector` extension:

- `DATABASE_URL` overrides the default
  `postgresql+psycopg://news:POSTGRES_PASSWORD@postgres-db:5432/news`.
- `EMBEDDING_DIMENSION` defaults to `768`, matching the usual EmbeddingGemma
  output size. Set it consistently before creating the schema if a different
  embedding configuration is used.
- JSON fields use PostgreSQL `json`, and the embedding fields use
  `vector(EMBEDDING_DIMENSION)`.
- Person aliases are normalized by the storage API into objects containing
  `alias` and the person's current `affiliation`. If the affiliation is not
  already represented, it is added as an alias entry. Their combined,
  freshly generated embedding is persisted in `dimPerson.aliases_vector` as
  `vector(EMBEDDING_DIMENSION)`. Embeddings are generated through the
  OpenAI-compatible endpoint configured
  by `EMBEDDING_URL` (defaulting to LM Studio at
  `http://host.docker.internal:1234/v1`) using `EMBEDDING_MODEL`
  (defaulting to `text-embedding-embeddinggemma-300m`).

## Schema lifecycle

From `data-storage`:

```powershell
python scripts\create_db.py
python scripts\recreate_db.py
```

The recreate operation drops all MVP tables before creating them again. The
same operations are available as `POST /database/create` and
`POST /database/recreate`. The API also creates missing tables during startup.

## API endpoints

The Swagger UI at `/docs` groups endpoints by the following tags:

- `Articles`, `People`, `Citations`, `Topics`, and `Attitudes` provide
  create, list, get, replace, and delete operations.
- `Articles - Bulk`, `People - Bulk`, `Citations - Bulk`, `Topics - Bulk`,
  and `Attitudes - Bulk` provide bulk create (`POST`), update (`PATCH`), and
  delete (`DELETE`) operations.
- `Database` contains schema lifecycle operations.
- `Vectors` contains text embedding, batch embedding, and alias semantic search
  endpoints, plus bulk re-embedding and vector-index creation.
- `System` contains health checks.

Bulk update requests include the resource identifier in each item. Bulk
deletes use `{ "ids": ["uuid", "..."] }`; attitude deletes use
`{ "keys": [{ "topic_uuid": "...", "citation_uuid": "..." }] }`.

Topic semantic search is available at `POST /topics/search`; send a JSON array
containing exactly `EMBEDDING_DIMENSION` floats.

Vector endpoints:

- `POST /vectors/embed` embeds one text.
- `POST /vectors/embed-batch` embeds multiple texts.
- `POST /aliases/search` embeds a text and searches aliases.
- `POST /aliases/search/vector` searches aliases using a supplied vector.
- `POST /people/search` embeds text and searches people using `aliases_vector`.
- `POST /people/search/vector` searches people using a supplied vector.
- `GET /people/{person_uuid}/aliases` lists aliases and copied affiliations.
- `POST /people/{person_uuid}/aliases/reembed` normalizes aliases and
  regenerates that person's `aliases_vector`.
- `POST /vectors/reembed-all` regenerates every person, citation, and topic
  vector from its source text.
- `POST /vectors/reindex-all` creates/ensures HNSW cosine indexes for all
  vector fields.
