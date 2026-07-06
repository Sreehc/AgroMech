# Pgvector Migration Design

Date: 2026-07-06

## Summary

AgroMech will remove Zvec completely and use PostgreSQL with the pgvector extension as the only vector database. Existing Zvec files under `.agromech-data/zvec` will not be migrated. After deployment, vectors will be regenerated from existing Postgres chunks and page assets with a rebuild command.

The migration is a hard cut:

- No Zvec backend compatibility.
- No Zvec configuration.
- No Zvec backup or restore tooling.
- No Zvec tests or documentation as current product behavior.
- PostgreSQL remains the system of record and becomes the vector store.

## Confirmed Decisions

1. Remove Zvec code and configuration entirely.
2. Do not migrate old Zvec JSON/vector files.
3. Rebuild vectors from existing `document_chunks` and `document_assets`.
4. Use dedicated pgvector tables instead of mixing vector storage into existing search/reference tables.
5. Require the local Docker PostgreSQL instance to install and enable `pgvector`.

## Data Model

Add a migration that enables pgvector:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Create a text chunk vector table:

```text
chunk_vector_embeddings
  id
  chunk_id -> document_chunks.id
  document_id -> documents.id
  provider
  model
  embedding_version
  chunk_profile
  embedding_dimension
  embedding vector(1024)
  status
  created_at
```

Create a visual page vector table:

```text
visual_page_vector_embeddings
  id
  asset_id -> document_assets.id
  document_id -> documents.id
  page_number
  provider
  model
  embedding_version
  embedding_dimension
  embedding vector(1024)
  status
  created_at
```

Use cosine distance indexes:

```sql
CREATE INDEX ix_chunk_vector_embeddings_embedding_hnsw ON chunk_vector_embeddings
USING hnsw (embedding vector_cosine_ops);

CREATE INDEX ix_visual_page_vector_embeddings_embedding_hnsw ON visual_page_vector_embeddings
USING hnsw (embedding vector_cosine_ops);
```

`chunk_search_index` remains the keyword/full-text search support table, and its `embedding` JSON column should be removed in the migration. `embedding_references` should be deleted because it represents external vector store references. `visual_page_embeddings` should be replaced by `visual_page_vector_embeddings` to remove stale `vector_store`, `collection`, and `vector_id` semantics.

The active embedding dimensions are currently 1024 for both text and visual embeddings. The pgvector columns should use `vector(1024)`. Future models with different dimensions should use a new migration and a new indexed column or table rather than mixing dimensions in one vector column.

## Write Path

The worker import pipeline keeps its existing document parsing behavior:

```text
process_ingest_task
  -> parse text/table/image/OCR/vision content
  -> write document_chunks and document_assets
  -> SearchIndexer.index_document()
  -> VisualPageIndexer.index_document()
```

`SearchIndexer.index_document(document_id)` should:

1. Load document chunks.
2. Build searchable text.
3. Generate text embeddings.
4. Write `chunk_search_index` rows for keyword retrieval.
5. Write `chunk_vector_embeddings` rows for vector retrieval.
6. Delete old vector rows for the same chunk IDs and active embedding version before inserting replacements.

`VisualPageIndexer.index_document(document_id)` should:

1. Load `page_image` assets.
2. Generate visual page embeddings.
3. Write `visual_page_vector_embeddings` rows.
4. Delete old visual vector rows for the same asset IDs and active visual embedding version before inserting replacements.

`SearchIndexer` and `VisualPageIndexer` should no longer accept or use `vector_store` or `collection` arguments. They should not produce `zvec://<collection>/<id>` vector IDs.

## Rebuild Command

Add a rebuild command:

```bash
.venv/bin/python scripts/rebuild-vector-index.py
```

Default behavior:

- Scan all `documents.status = indexed` documents.
- Re-run `SearchIndexer.index_document(document_id)` for each document.
- Re-run `VisualPageIndexer.index_document(document_id)` when page image assets exist.
- Use one document-level transaction boundary per indexer call.
- Continue after individual document failures.
- Print a final success/failure summary.

Supported options:

```text
--document-id <id>   Rebuild one document.
--include-visual     Include visual page vectors. Enabled by default.
--no-visual          Skip visual page vectors.
--dry-run            Print selected documents without writing.
```

This command is the required post-deployment step because old Zvec data will not be migrated.

## Retrieval Path

Text vector retrieval should query pgvector directly:

```sql
SELECT
  cve.id AS embedding_id,
  cve.chunk_id,
  1 - (cve.embedding <=> :query_embedding) AS score
FROM chunk_vector_embeddings cve
JOIN document_chunks dc ON dc.id = cve.chunk_id
JOIN documents d ON d.id = cve.document_id
WHERE cve.embedding_version = :active_version
  AND cve.status = 'ready'
  AND (d.visibility = 'public' OR d.owner_user_id = :viewer_user_id)
ORDER BY cve.embedding <=> :query_embedding
LIMIT :limit;
```

Visual page retrieval should use the same pattern with `visual_page_vector_embeddings`, joined to `document_assets` and `documents` to build `visual_page` evidence.

The existing hybrid retrieval shape remains:

```text
keyword + structured + vector in parallel
  -> merge candidates
  -> enforce visibility
  -> rerank
  -> final_evidence
  -> retrieval_logs
```

`vector_search()` and `visual_page_search()` should no longer accept `vector_store` or `collection` parameters.

Trace and evidence payloads should include:

```json
{
  "embedding_id": "chunk-vector-row-id",
  "vector_ref": "pgvector://chunk_vector_embeddings/chunk-vector-row-id"
}
```

`embedding_id` is the preferred internal identifier. `vector_ref` remains temporarily for trace/frontend compatibility, but it must no longer reference Zvec.

## Configuration

Remove these settings from `.env.example`, `deploy/env.prod.example`, and `Settings`:

- `VECTOR_BACKEND`
- `ZVEC_PATH`
- `ZVEC_COLLECTION`
- `ZVEC_TEXT_COLLECTION`
- `ZVEC_VISUAL_COLLECTION`
- `ZVEC_BACKUP_PATH`
- `ZVEC_BACKUP_RETENTION_DAYS`

The application should assume pgvector for vector retrieval. Model provider settings remain separate:

- `EMBEDDING_PROVIDER`
- `EMBEDDING_MODEL`
- `EMBEDDING_DIMENSION`
- `VISUAL_EMBEDDING_PROVIDER`
- `VISUAL_EMBEDDING_MODEL`
- `VISUAL_EMBEDDING_DIMENSION`

Add a Python pgvector integration dependency, for example `pgvector`, so SQLAlchemy can bind/query vector values cleanly. If the implementation uses raw SQL casts instead, the dependency can be avoided, but the preferred path is to use the maintained pgvector Python package.

## Health Checks

Remove the Zvec storage health check.

Add a pgvector health check that verifies:

1. Postgres is reachable through the existing database configuration.
2. The `vector` extension is installed in the active database.

`/health/dependencies` should report `pgvector` instead of `zvec`.

## Code Removal

Delete the Zvec implementation and backup tooling:

```text
backend/agromech_api/integrations/vectorstores/zvec.py
backend/agromech_api/integrations/vectorstores/zvec_backup.py
scripts/zvec-backup.py
```

Remove Zvec-specific LangChain adapter code. Keep the provider embedding wrappers and AgroMech retriever wrappers if still useful, but remove `ZvecLangChainVectorStore` and the Zvec component builders.

Remove Zvec-specific imports and branches from:

```text
worker/agromech_worker/main.py
backend/agromech_api/qa/text.py
backend/agromech_api/rag/retrieval/indexing.py
backend/agromech_api/rag/retrieval/hybrid.py
backend/agromech_api/rag/langchain/adapters.py
backend/agromech_api/core/config.py
backend/agromech_api/core/infrastructure.py
```

## Deployment

Deployment sequence:

1. Install pgvector in the Docker PostgreSQL image/container.
2. Run Alembic migrations.
3. Start API and worker.
4. Run `scripts/rebuild-vector-index.py`.
5. Verify:
   - `/health`
   - `/health/dependencies`
   - document upload and indexing
   - text QA
   - image QA with visual retrieval

If the rebuild step fails for some documents, the system should still run for documents whose vectors were rebuilt. Failed document IDs should be visible in rebuild output so they can be retried individually.

## Testing Strategy

Update or add tests for:

- pgvector tables, columns, constraints, and indexes.
- pgvector health check.
- `SearchIndexer` writing `chunk_vector_embeddings`.
- `VisualPageIndexer` writing `visual_page_vector_embeddings`.
- text vector search using pgvector.
- visual page vector search using pgvector.
- `/qa/text` using pgvector-backed retrieval.
- rebuild command dry-run and single-document behavior.
- documentation sync so current docs no longer describe Zvec as active.

Delete or rewrite tests that assert Zvec behavior:

- Zvec store tests.
- Zvec backup tests.
- Zvec LangChain vector store tests.
- Zvec-specific retrieval and QA tests.

If CI does not provide a pgvector-enabled Postgres instance, pgvector integration tests should skip with a clear reason. Deterministic embedding unit tests should still cover non-network logic.

## Open Risks

- The local Docker Postgres image must include pgvector or be replaced with one that does.
- HNSW index creation can take time on large datasets; deployment should account for this.
- Existing documents need an explicit rebuild after migration before vector recall is complete.
- Removing `embedding_references` and `visual_page_embeddings` is a destructive schema change for old vector metadata, which is acceptable because Zvec data will not be migrated.

## Out of Scope

- Migrating `.agromech-data/zvec` files.
- Keeping a compatibility backend for Zvec.
- Adding Neo4j or Graph RAG back to the main retrieval path.
- Supporting multiple embedding dimensions in one pgvector column.
