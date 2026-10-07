# Data-storage database

`DBConnector.py` is the single SQLAlchemy ORM for the MVP schema in
[`app/input.md`](../input.md). It uses PostgreSQL with the `pgvector` extension:

- `DATABASE_URL` overrides the default
  `postgresql+psycopg://news:POSTGRES_PASSWORD@postgres-db:5432/news`.
- `EMBEDDING_DIMENSION` defaults to `768`, matching the usual EmbeddingGemma
  output size. Set it consistently before creating the schema if a different
  embedding configuration is used.
- JSON fields use PostgreSQL `json`, and the two embedding fields use
  `vector(EMBEDDING_DIMENSION)`.

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
- `System` contains health checks.

Bulk update requests include the resource identifier in each item. Bulk
deletes use `{ "ids": ["uuid", "..."] }`; attitude deletes use
`{ "keys": [{ "topic_uuid": "...", "citation_uuid": "..." }] }`.

Topic semantic search is available at `POST /topics/search`; send a JSON array
containing exactly `EMBEDDING_DIMENSION` floats. Embeddings are intentionally
supplied by the caller so an EmbeddingGemma runtime can be added without
coupling the storage service to a model-serving implementation.
