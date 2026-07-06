# Pgvector Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Zvec completely and make PostgreSQL with pgvector the only vector storage path for text and visual retrieval.

**Architecture:** Add pgvector-backed tables in Postgres, write embeddings directly into those tables during indexing, and query them with cosine distance SQL. Delete Zvec adapters, configuration, health checks, backup tooling, and tests; rebuild existing vectors from Postgres chunks/assets after migration.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy Core, Alembic, psycopg, pgvector, PostgreSQL pgvector extension, pytest, existing AgroMech ingestion/RAG modules.

---

## File Structure

- Modify `pyproject.toml`: add Python `pgvector` dependency.
- Modify `.env.example` and `deploy/env.prod.example`: remove Zvec/vector backend settings.
- Modify `backend/agromech_api/core/config.py`: remove Zvec settings and `VECTOR_BACKEND` branching.
- Modify `backend/agromech_api/db/models.py`: remove external vector reference tables; add pgvector tables.
- Create `backend/alembic/versions/0012_replace_zvec_with_pgvector.py`: enable extension, add pgvector tables/indexes, drop stale tables/columns.
- Modify `backend/agromech_api/rag/retrieval/indexing.py`: write/query pgvector tables directly.
- Modify `backend/agromech_api/rag/retrieval/hybrid.py`: remove vector store/collection parameters and update trace model config.
- Modify `backend/agromech_api/qa/text.py`: build embedding providers only; remove Zvec vector component wiring.
- Modify `worker/agromech_worker/main.py`: remove Zvec vector store construction.
- Modify `backend/agromech_api/rag/langchain/adapters.py`: delete `ZvecLangChainVectorStore` and component builders.
- Modify `backend/agromech_api/core/infrastructure.py`: replace Zvec health check with pgvector extension check.
- Create `scripts/rebuild-vector-index.py`: rebuild text and visual vector rows from existing documents.
- Delete `backend/agromech_api/integrations/vectorstores/zvec.py`.
- Delete `backend/agromech_api/integrations/vectorstores/zvec_backup.py`.
- Delete `scripts/zvec-backup.py`.
- Update tests under `backend/tests/` to remove Zvec assumptions and assert pgvector behavior.
- Update docs: `README.md`, `docs/README.md`, `docs/tech-design.md`, `docs/database-design.md`, `docs/api-spec.md`, `docs/deployment.md`, `docs/prd.md`, `docs/history.md`.

---

### Task 1: Add Dependency And Remove Zvec Settings

**Files:**
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Modify: `deploy/env.prod.example`
- Modify: `backend/agromech_api/core/config.py`
- Test: `backend/tests/test_infrastructure_config.py`

- [ ] **Step 1: Write failing settings tests**

Edit `backend/tests/test_infrastructure_config.py`:

```python
def test_settings_no_longer_exposes_zvec_configuration() -> None:
    settings = Settings()

    assert not hasattr(settings, "vector_backend")
    assert not hasattr(settings, "zvec_path")
    assert not hasattr(settings, "zvec_collection")
    assert not hasattr(settings, "zvec_text_collection")
    assert not hasattr(settings, "zvec_visual_collection")
    assert not hasattr(settings, "zvec_backup_path")
    assert not hasattr(settings, "zvec_backup_retention_days")


def test_settings_keep_embedding_provider_configuration() -> None:
    settings = Settings(
        embedding_provider="local",
        embedding_model="text-embedding-v4",
        embedding_dimension=1024,
        visual_embedding_provider="local",
        visual_embedding_model="qwen3-vl-embedding",
        visual_embedding_dimension=1024,
    )

    assert settings.embedding_provider == "local"
    assert settings.embedding_dimension == 1024
    assert settings.visual_embedding_provider == "local"
    assert settings.visual_embedding_dimension == 1024
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.venv/bin/python -m pytest backend/tests/test_infrastructure_config.py::test_settings_no_longer_exposes_zvec_configuration backend/tests/test_infrastructure_config.py::test_settings_keep_embedding_provider_configuration -q`

Expected: first test FAILS because `Settings` still exposes Zvec fields.

- [ ] **Step 3: Add pgvector Python dependency**

In `pyproject.toml`, add `pgvector>=0.3.0` to `[project].dependencies`:

```toml
  "pgvector>=0.3.0",
```

Run: `.venv/bin/python -m pip install -e ".[dev]"`

Expected: install succeeds and `python -c "import pgvector"` exits 0.

- [ ] **Step 4: Remove Zvec settings**

In `backend/agromech_api/core/config.py`, delete these fields:

```python
    vector_backend: str = "zvec"
    zvec_path: str = "./.agromech-data/zvec"
    zvec_collection: str = "agromech_chunks"
    zvec_text_collection: str = "agromech_text_chunks"
    zvec_visual_collection: str = "agromech_visual_pages"
    zvec_backup_path: str = "./.agromech-data/backups/zvec"
    zvec_backup_retention_days: int = 7
```

Delete the validator branch:

```python
        if self.vector_backend == "zvec":
            require_settings(self, ["zvec_path", "zvec_collection"], mode="VECTOR_BACKEND=zvec")
```

Update the nearby comment from:

```python
        real backend (OSS, Zvec, Neo4j, Bailian) is switched on.
```

to:

```python
        real backend (OSS, Neo4j, Bailian) is switched on.
```

- [ ] **Step 5: Remove Zvec env entries**

Delete these keys from `.env.example` and `deploy/env.prod.example`:

```text
VECTOR_BACKEND
ZVEC_PATH
ZVEC_COLLECTION
ZVEC_TEXT_COLLECTION
ZVEC_VISUAL_COLLECTION
ZVEC_BACKUP_PATH
ZVEC_BACKUP_RETENTION_DAYS
```

- [ ] **Step 6: Run settings tests**

Run: `.venv/bin/python -m pytest backend/tests/test_infrastructure_config.py::test_settings_no_longer_exposes_zvec_configuration backend/tests/test_infrastructure_config.py::test_settings_keep_embedding_provider_configuration -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .env.example deploy/env.prod.example backend/agromech_api/core/config.py backend/tests/test_infrastructure_config.py
git commit -m "chore: remove zvec configuration"
```

---

### Task 2: Add Pgvector Data Model And Migration

**Files:**
- Modify: `backend/agromech_api/db/models.py`
- Create: `backend/alembic/versions/0012_replace_zvec_with_pgvector.py`
- Modify: `backend/tests/test_data_model.py`
- Modify: `backend/tests/test_migrations.py`

- [ ] **Step 1: Write failing data model tests**

Edit `backend/tests/test_data_model.py`:

```python
def test_pgvector_tables_replace_external_vector_references() -> None:
    assert "embedding_references" not in metadata.tables
    assert "visual_page_embeddings" not in metadata.tables
    assert "chunk_vector_embeddings" in metadata.tables
    assert "visual_page_vector_embeddings" in metadata.tables


def test_chunk_vector_embeddings_table_declares_pgvector_fields() -> None:
    table = metadata.tables["chunk_vector_embeddings"]
    columns = table.c

    assert "chunk_id" in columns
    assert "document_id" in columns
    assert "embedding" in columns
    assert "embedding_version" in columns
    assert "chunk_profile" in columns
    assert "embedding_dimension" in columns
    assert "status" in columns


def test_visual_page_vector_embeddings_table_declares_pgvector_fields() -> None:
    table = metadata.tables["visual_page_vector_embeddings"]
    columns = table.c

    assert "asset_id" in columns
    assert "document_id" in columns
    assert "page_number" in columns
    assert "embedding" in columns
    assert "embedding_version" in columns
    assert "embedding_dimension" in columns
    assert "status" in columns
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.venv/bin/python -m pytest backend/tests/test_data_model.py -q`

Expected: FAIL because old tables still exist and new tables do not.

- [ ] **Step 3: Update SQLAlchemy models**

In `backend/agromech_api/db/models.py`, import the vector type:

```python
from pgvector.sqlalchemy import Vector
```

Delete the `embedding_references` and `visual_page_embeddings` table definitions and their indexes.

Add:

```python
chunk_vector_embeddings = Table(
    "chunk_vector_embeddings",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("chunk_id", ForeignKey("document_chunks.id", ondelete="CASCADE"), nullable=False),
    Column("document_id", ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
    Column("provider", String(80), nullable=False),
    Column("model", String(120), nullable=False),
    Column("embedding_version", String(160), nullable=False),
    Column("chunk_profile", String(80), nullable=False),
    Column("embedding_dimension", Integer, nullable=False),
    Column("embedding", Vector(1024), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index(
    "ix_chunk_vector_embeddings_chunk_version",
    chunk_vector_embeddings.c.chunk_id,
    chunk_vector_embeddings.c.embedding_version,
    unique=True,
)
Index("ix_chunk_vector_embeddings_document_id", chunk_vector_embeddings.c.document_id)

visual_page_vector_embeddings = Table(
    "visual_page_vector_embeddings",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("asset_id", ForeignKey("document_assets.id", ondelete="CASCADE"), nullable=False),
    Column("document_id", ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
    Column("page_number", Integer),
    Column("provider", String(80), nullable=False),
    Column("model", String(120), nullable=False),
    Column("embedding_version", String(160), nullable=False),
    Column("embedding_dimension", Integer, nullable=False),
    Column("embedding", Vector(1024), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index(
    "ix_visual_page_vector_embeddings_asset_version",
    visual_page_vector_embeddings.c.asset_id,
    visual_page_vector_embeddings.c.embedding_version,
    unique=True,
)
Index("ix_visual_page_vector_embeddings_document_id", visual_page_vector_embeddings.c.document_id)
```

- [ ] **Step 4: Write Alembic migration**

Create `backend/alembic/versions/0012_replace_zvec_with_pgvector.py`:

```python
"""replace zvec with pgvector

Revision ID: 0012_pgvector
Revises: 0011_document_visibility
Create Date: 2026-07-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = "0012_pgvector"
down_revision = "0011_document_visibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "chunk_vector_embeddings" not in tables:
        op.create_table(
            "chunk_vector_embeddings",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("chunk_id", sa.String(length=36), sa.ForeignKey("document_chunks.id", ondelete="CASCADE"), nullable=False),
            sa.Column("document_id", sa.String(length=36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("provider", sa.String(length=80), nullable=False),
            sa.Column("model", sa.String(length=120), nullable=False),
            sa.Column("embedding_version", sa.String(length=160), nullable=False),
            sa.Column("chunk_profile", sa.String(length=80), nullable=False),
            sa.Column("embedding_dimension", sa.Integer(), nullable=False),
            sa.Column("embedding", Vector(1024), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_chunk_vector_embeddings_chunk_version", "chunk_vector_embeddings", ["chunk_id", "embedding_version"], unique=True)
        op.create_index("ix_chunk_vector_embeddings_document_id", "chunk_vector_embeddings", ["document_id"])

    if "visual_page_vector_embeddings" not in tables:
        op.create_table(
            "visual_page_vector_embeddings",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("asset_id", sa.String(length=36), sa.ForeignKey("document_assets.id", ondelete="CASCADE"), nullable=False),
            sa.Column("document_id", sa.String(length=36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("page_number", sa.Integer()),
            sa.Column("provider", sa.String(length=80), nullable=False),
            sa.Column("model", sa.String(length=120), nullable=False),
            sa.Column("embedding_version", sa.String(length=160), nullable=False),
            sa.Column("embedding_dimension", sa.Integer(), nullable=False),
            sa.Column("embedding", Vector(1024), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_visual_page_vector_embeddings_asset_version", "visual_page_vector_embeddings", ["asset_id", "embedding_version"], unique=True)
        op.create_index("ix_visual_page_vector_embeddings_document_id", "visual_page_vector_embeddings", ["document_id"])

    if bind.dialect.name == "postgresql":
        op.create_index(
            "ix_chunk_vector_embeddings_embedding_hnsw",
            "chunk_vector_embeddings",
            ["embedding"],
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        )
        op.create_index(
            "ix_visual_page_vector_embeddings_embedding_hnsw",
            "visual_page_vector_embeddings",
            ["embedding"],
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        )

    if "embedding_references" in tables:
        op.drop_table("embedding_references")
    if "visual_page_embeddings" in tables:
        op.drop_table("visual_page_embeddings")
    if "chunk_search_index" in tables:
        chunk_columns = {column["name"] for column in inspector.get_columns("chunk_search_index")}
        if "embedding" in chunk_columns:
            op.drop_column("chunk_search_index", "embedding")


def downgrade() -> None:
    raise RuntimeError("Downgrade from pgvector migration is not supported; rebuild vectors from chunks/assets instead.")
```

- [ ] **Step 5: Update migration tests**

Edit `backend/tests/test_migrations.py` to assert the new revision exists and new tables are part of metadata. Add:

```python
def test_pgvector_migration_file_exists() -> None:
    path = Path("backend/alembic/versions/0012_replace_zvec_with_pgvector.py")
    assert path.exists()
    contents = path.read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS vector" in contents
    assert "chunk_vector_embeddings" in contents
    assert "visual_page_vector_embeddings" in contents
```

- [ ] **Step 6: Run model and migration tests**

Run: `.venv/bin/python -m pytest backend/tests/test_data_model.py backend/tests/test_migrations.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/agromech_api/db/models.py backend/alembic/versions/0012_replace_zvec_with_pgvector.py backend/tests/test_data_model.py backend/tests/test_migrations.py
git commit -m "feat: add pgvector data model"
```

---

### Task 3: Write Pgvector Rows During Indexing

**Files:**
- Modify: `backend/agromech_api/rag/retrieval/indexing.py`
- Modify: `backend/agromech_api/ingestion/runner.py`
- Modify: `worker/agromech_worker/main.py`
- Modify: `backend/tests/test_search_indexing.py`

- [ ] **Step 1: Write failing indexing tests**

In `backend/tests/test_search_indexing.py`, replace Zvec write tests with:

```python
from agromech_api.db.models import chunk_vector_embeddings, visual_page_vector_embeddings


def test_index_document_writes_pgvector_rows(engine) -> None:
    document_id, chunk_id = seed_searchable_document(engine, content="M7040 hydraulic pump warning")
    provider = DeterministicEmbeddingProvider()

    result = SearchIndexer(
        engine,
        embedding_provider=provider,
        embedding_version="emb_test",
        chunk_profile="chunk-v1",
        embedding_dimension=256,
    ).index_document(document_id)

    assert result.chunk_count == 1
    with engine.connect() as connection:
        rows = connection.execute(select(chunk_vector_embeddings)).mappings().all()
        search_rows = connection.execute(select(chunk_search_index)).mappings().all()

    assert len(rows) == 1
    assert rows[0]["chunk_id"] == chunk_id
    assert rows[0]["document_id"] == document_id
    assert rows[0]["provider"] == provider.provider
    assert rows[0]["embedding_version"] == "emb_test"
    assert rows[0]["status"] == "ready"
    assert len(rows[0]["embedding"]) == 256
    assert len(search_rows) == 1


def test_visual_page_indexer_writes_pgvector_rows(engine, tmp_path) -> None:
    document_id, asset_id = seed_page_image_asset(engine, tmp_path)
    provider = DeterministicVisualEmbeddingProvider(dimension=4)

    result = VisualPageIndexer(
        engine,
        embedding_provider=provider,
        embedding_version="vis_test",
        embedding_dimension=4,
    ).index_document(document_id)

    assert result.chunk_count == 1
    with engine.connect() as connection:
        rows = connection.execute(select(visual_page_vector_embeddings)).mappings().all()

    assert len(rows) == 1
    assert rows[0]["asset_id"] == asset_id
    assert rows[0]["document_id"] == document_id
    assert rows[0]["embedding_version"] == "vis_test"
    assert rows[0]["status"] == "ready"
    assert len(rows[0]["embedding"]) == 4
```

Add these helpers to `backend/tests/test_search_indexing.py` if equivalent helpers are not already present:

```python
def seed_searchable_document(engine, *, content: str) -> tuple[str, str]:
    document_id = str(uuid4())
    chunk_id = str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            documents.insert().values(
                id=document_id,
                title="Hydraulic Manual",
                original_file_name="manual.txt",
                file_hash=document_id,
                file_size_bytes=128,
                mime_type="text/plain",
                storage_uri=f"file:///{document_id}.txt",
                status=DocumentStatus.INDEXED.value,
                created_by_role="admin",
                visibility="public",
            )
        )
        connection.execute(
            document_chunks.insert().values(
                id=chunk_id,
                document_id=document_id,
                chunk_type=ChunkType.TEXT.value,
                content=content,
                source_locator={"type": "text", "line_start": 1},
            )
        )
    return document_id, chunk_id


def seed_page_image_asset(engine, tmp_path) -> tuple[str, str]:
    document_id = str(uuid4())
    asset_id = str(uuid4())
    image_path = tmp_path / "page-1.png"
    image_path.write_bytes(b"fake-image")
    with engine.begin() as connection:
        connection.execute(
            documents.insert().values(
                id=document_id,
                title="Visual Manual",
                original_file_name="manual.pdf",
                file_hash=document_id,
                file_size_bytes=128,
                mime_type="application/pdf",
                storage_uri=f"file:///{document_id}.pdf",
                status=DocumentStatus.INDEXED.value,
                created_by_role="admin",
                visibility="public",
            )
        )
        connection.execute(
            document_assets.insert().values(
                id=asset_id,
                document_id=document_id,
                asset_type=AssetType.PAGE_IMAGE.value,
                storage_uri=image_path.as_uri(),
                mime_type="image/png",
                page_number=1,
                source_locator={"type": "pdf_page", "page": 1},
                ocr_text="hydraulic page",
                visual_observation={"description": "hydraulic pump page"},
            )
        )
    return document_id, asset_id
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.venv/bin/python -m pytest backend/tests/test_search_indexing.py::test_index_document_writes_pgvector_rows backend/tests/test_search_indexing.py::test_visual_page_indexer_writes_pgvector_rows -q`

Expected: FAIL because indexers still write external vector references.

- [ ] **Step 3: Update SearchIndexer constructor and write path**

In `backend/agromech_api/rag/retrieval/indexing.py`:

- Remove `vector_store` and `collection` constructor parameters from `SearchIndexer`.
- Remove `LocalVectorStore`.
- Import `chunk_vector_embeddings`.
- Delete all `embedding_references` writes.
- Add vector rows:

```python
vector_rows.append(
    {
        "id": str(uuid4()),
        "chunk_id": chunk["id"],
        "document_id": document_id,
        "provider": self.embedding_provider.provider,
        "model": self.embedding_provider.model,
        "embedding_version": self.embedding_version,
        "chunk_profile": self.chunk_profile,
        "embedding_dimension": len(embedding),
        "embedding": embedding,
        "status": "ready",
    }
)
```

Before insert, delete old rows:

```python
connection.execute(
    delete(chunk_vector_embeddings).where(
        chunk_vector_embeddings.c.chunk_id.in_(chunk_ids),
        chunk_vector_embeddings.c.embedding_version == self.embedding_version,
    )
)
```

Then insert:

```python
if vector_rows:
    connection.execute(insert(chunk_vector_embeddings), vector_rows)
```

- [ ] **Step 4: Update VisualPageIndexer write path**

In `backend/agromech_api/rag/retrieval/indexing.py`:

- Remove `vector_store` and `collection` constructor parameters from `VisualPageIndexer`.
- Import `visual_page_vector_embeddings`.
- Delete all `visual_page_embeddings` writes.
- Add rows with the generated `embedding`:

```python
rows.append(
    {
        "id": str(uuid4()),
        "asset_id": asset["id"],
        "document_id": document_id,
        "page_number": asset["page_number"],
        "provider": self.embedding_provider.provider,
        "model": self.embedding_provider.model,
        "embedding_version": self.embedding_version,
        "embedding_dimension": len(embedding),
        "embedding": embedding,
        "status": "ready",
    }
)
```

Delete old visual rows:

```python
connection.execute(
    delete(visual_page_vector_embeddings).where(
        visual_page_vector_embeddings.c.asset_id.in_(asset_ids),
        visual_page_vector_embeddings.c.embedding_version == self.embedding_version,
    )
)
```

- [ ] **Step 5: Update cleanup on delete**

In `backend/agromech_api/ingestion/runner.py`, replace imports and cleanup:

```python
from agromech_api.db.models import chunk_vector_embeddings, visual_page_vector_embeddings
```

In `cleanup_deleted_document`, delete chunk vectors before chunks:

```python
if chunk_ids:
    connection.execute(delete(chunk_vector_embeddings).where(chunk_vector_embeddings.c.chunk_id.in_(chunk_ids)))
```

Also delete visual vectors by document:

```python
connection.execute(delete(visual_page_vector_embeddings).where(visual_page_vector_embeddings.c.document_id == document_id))
```

- [ ] **Step 6: Simplify worker indexer construction**

In `worker/agromech_worker/main.py`, remove:

```python
from agromech_api.integrations.vectorstores.zvec import build_vector_store
```

Replace the production indexer construction with:

```python
from agromech_api.integrations.embeddings.text import build_embedding_provider

settings = get_settings()
text_embeddings = build_embedding_provider(settings)
indexer = SearchIndexer(
    active_engine,
    embedding_provider=text_embeddings,
)
```

- [ ] **Step 7: Run indexing tests**

Run: `.venv/bin/python -m pytest backend/tests/test_search_indexing.py -q`

Expected: PASS after removing or updating old Zvec assertions.

- [ ] **Step 8: Commit**

```bash
git add backend/agromech_api/rag/retrieval/indexing.py backend/agromech_api/ingestion/runner.py worker/agromech_worker/main.py backend/tests/test_search_indexing.py
git commit -m "feat: write embeddings to pgvector tables"
```

---

### Task 4: Query Pgvector For Text And Visual Retrieval

**Files:**
- Modify: `backend/agromech_api/rag/retrieval/indexing.py`
- Modify: `backend/agromech_api/rag/retrieval/hybrid.py`
- Modify: `backend/agromech_api/qa/text.py`
- Modify: `backend/tests/test_search_indexing.py`
- Modify: `backend/tests/test_hybrid_retrieval.py`
- Modify: `backend/tests/test_retrieval_trace.py`
- Modify: `backend/tests/test_text_qa.py`

- [ ] **Step 1: Write failing text vector search test**

Add to `backend/tests/test_search_indexing.py`:

```python
def test_vector_search_returns_pgvector_refs(engine) -> None:
    document_id, chunk_id = seed_searchable_document(engine, content="dashboard hydraulic warning")
    SearchIndexer(
        engine,
        embedding_provider=DeterministicEmbeddingProvider(),
        embedding_version="emb_test",
        embedding_dimension=256,
    ).index_document(document_id)

    results = vector_search(
        engine,
        "dashboard hydraulic warning",
        active_embedding_version="emb_test",
        embedding_provider=DeterministicEmbeddingProvider(),
    )

    assert results[0]["chunk_id"] == chunk_id
    assert results[0]["embedding_id"]
    assert results[0]["vector_ref"].startswith("pgvector://chunk_vector_embeddings/")
```

- [ ] **Step 2: Write failing visual vector search test**

Add:

```python
def test_visual_page_search_returns_pgvector_refs(engine, tmp_path) -> None:
    document_id, asset_id = seed_page_image_asset(engine, tmp_path)
    provider = DeterministicVisualEmbeddingProvider(dimension=4)
    VisualPageIndexer(
        engine,
        embedding_provider=provider,
        embedding_version="vis_test",
        embedding_dimension=4,
    ).index_document(document_id)

    results = visual_page_search(
        engine,
        "hydraulic page",
        active_embedding_version="vis_test",
        embedding_provider=provider,
    )

    assert results[0]["asset_id"] == asset_id
    assert results[0]["embedding_id"]
    assert results[0]["vector_ref"].startswith("pgvector://visual_page_vector_embeddings/")
```

- [ ] **Step 3: Run tests to verify failure**

Run: `.venv/bin/python -m pytest backend/tests/test_search_indexing.py::test_vector_search_returns_pgvector_refs backend/tests/test_search_indexing.py::test_visual_page_search_returns_pgvector_refs -q`

Expected: FAIL because search functions still expose old Zvec paths or do not query new tables.

- [ ] **Step 4: Implement text vector query**

In `backend/agromech_api/rag/retrieval/indexing.py`, remove `zvec_vector_search` and update `vector_search` signature:

```python
def vector_search(
    engine: Engine,
    query: str,
    *,
    limit: int = 10,
    active_embedding_version: str | None = None,
    embedding_provider=None,
    viewer_user_id: str | None = None,
) -> list[dict[str, object]]:
```

Use `chunk_vector_embeddings` rows for deterministic tests and PostgreSQL ordering for production:

```python
provider = embedding_provider or DeterministicEmbeddingProvider()
query_embedding = provider.embed(query)
active_version = active_embedding_version or get_settings().embedding_version
with engine.connect() as connection:
    rows = connection.execute(
        select(
            chunk_vector_embeddings.c.id.label("embedding_id"),
            chunk_vector_embeddings.c.chunk_id,
            chunk_vector_embeddings.c.embedding,
            chunk_vector_embeddings.c.embedding_version,
            document_chunks.c.chunk_type,
        )
        .join(document_chunks, document_chunks.c.id == chunk_vector_embeddings.c.chunk_id)
        .join(documents, documents.c.id == chunk_vector_embeddings.c.document_id)
        .where(chunk_vector_embeddings.c.embedding_version == active_version)
        .where(chunk_vector_embeddings.c.status == "ready")
        .where(_visual_visibility_condition(viewer_user_id))
    ).mappings().all()
scored = []
for row in rows:
    score = cosine_similarity(query_embedding, row["embedding"])
    if score > 0:
        embedding_id = str(row["embedding_id"])
        scored.append(
            {
                "chunk_id": row["chunk_id"],
                "score": score,
                "chunk_type": row["chunk_type"],
                "embedding_version": row["embedding_version"],
                "embedding_id": embedding_id,
                "vector_ref": f"pgvector://chunk_vector_embeddings/{embedding_id}",
            }
        )
return sorted(scored, key=lambda item: item["score"], reverse=True)[:limit]
```

This keeps SQLite/unit-test compatibility by scoring in Python. A later optimization can add dialect-specific SQL ordering for Postgres; keep behavior correct first.

- [ ] **Step 5: Implement visual page query**

Update `visual_page_search` to remove `vector_store` and `collection` parameters. Load rows from `visual_page_vector_embeddings`, score in Python with `cosine_similarity`, then join metadata:

```python
rows = connection.execute(
    select(
        visual_page_vector_embeddings.c.id.label("embedding_id"),
        visual_page_vector_embeddings.c.asset_id,
        visual_page_vector_embeddings.c.document_id,
        visual_page_vector_embeddings.c.page_number,
        visual_page_vector_embeddings.c.embedding,
        visual_page_vector_embeddings.c.embedding_version,
    )
    .join(documents, documents.c.id == visual_page_vector_embeddings.c.document_id)
    .where(visual_page_vector_embeddings.c.embedding_version == active_version)
    .where(visual_page_vector_embeddings.c.status == "ready")
    .where(_visual_visibility_condition(viewer_user_id))
).mappings().all()
```

For each returned evidence item set:

```python
"embedding_id": embedding_id,
"vector_ref": f"pgvector://visual_page_vector_embeddings/{embedding_id}",
```

- [ ] **Step 6: Remove vector store plumbing from hybrid retrieval**

In `backend/agromech_api/rag/retrieval/hybrid.py`:

- Remove `vector_store`, `vector_collection`, and `graph_service` parameters from `VectorRetrievalAgent.run`, `hybrid_retrieve`, `hybrid_retrieve_with_trace`, `collect_candidates`, and `collect_channel_results`.
- Call:

```python
vector_search(
    engine,
    query,
    limit=limit * 2,
    embedding_provider=embedding_provider,
    viewer_user_id=viewer_user_id,
)
```

Update `trace_model_config`:

```python
"vector_backend": "pgvector",
"vector_collection": None,
```

- [ ] **Step 7: Remove Zvec wiring from QA**

In `backend/agromech_api/qa/text.py`:

- Replace `build_text_vector_components` usage with `build_embedding_provider(settings)` when vector retrieval is needed.
- Replace `build_visual_vector_components` usage with `build_visual_embedding_provider(settings)`.
- Do not pass `vector_store` or `vector_collection` into retrieval calls.

Use:

```python
from agromech_api.integrations.embeddings.text import build_embedding_provider
from agromech_api.integrations.embeddings.visual import build_visual_embedding_provider
```

- [ ] **Step 8: Update retrieval tests**

Run affected tests:

```bash
.venv/bin/python -m pytest \
  backend/tests/test_search_indexing.py \
  backend/tests/test_hybrid_retrieval.py \
  backend/tests/test_retrieval_trace.py \
  backend/tests/test_text_qa.py \
  -q
```

Expected: PASS after replacing old expected values:

```python
assert log["model_config"]["vector_backend"] == "pgvector"
assert result["vector_ref"].startswith("pgvector://")
```

- [ ] **Step 9: Commit**

```bash
git add backend/agromech_api/rag/retrieval/indexing.py backend/agromech_api/rag/retrieval/hybrid.py backend/agromech_api/qa/text.py backend/tests/test_search_indexing.py backend/tests/test_hybrid_retrieval.py backend/tests/test_retrieval_trace.py backend/tests/test_text_qa.py
git commit -m "feat: retrieve vectors from pgvector"
```

---

### Task 5: Replace Zvec Health Check With Pgvector Health Check

**Files:**
- Modify: `backend/agromech_api/core/infrastructure.py`
- Modify: `backend/tests/test_infrastructure_config.py`
- Modify: `backend/tests/test_dependency_health.py`
- Modify: `docs/api-spec.md`

- [ ] **Step 1: Write failing health test**

In `backend/tests/test_infrastructure_config.py`, replace Zvec health tests with:

```python
def test_pgvector_health_check_reports_ok_when_extension_exists(monkeypatch) -> None:
    from agromech_api.core.infrastructure import check_pgvector_extension

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, statement):
            return self

        def scalar_one_or_none(self):
            return "vector"

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    check = check_pgvector_extension(FakeEngine())

    assert check.name == "pgvector"
    assert check.status == "ok"


def test_pgvector_health_check_reports_unavailable_when_extension_missing() -> None:
    from agromech_api.core.infrastructure import check_pgvector_extension

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, statement):
            return self

        def scalar_one_or_none(self):
            return None

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    check = check_pgvector_extension(FakeEngine())

    assert check.name == "pgvector"
    assert check.status == "unavailable"
    assert "extension missing" in check.error
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.venv/bin/python -m pytest backend/tests/test_infrastructure_config.py::test_pgvector_health_check_reports_ok_when_extension_exists backend/tests/test_infrastructure_config.py::test_pgvector_health_check_reports_unavailable_when_extension_missing -q`

Expected: FAIL because `check_pgvector_extension` does not exist.

- [ ] **Step 3: Implement pgvector health check**

In `backend/agromech_api/core/infrastructure.py`, import:

```python
from sqlalchemy import text
from agromech_api.core.database import get_engine
```

Add:

```python
def check_pgvector_extension(engine=None) -> DependencyCheck:
    active_engine = engine or get_engine()
    target = "postgres:extension/vector"
    try:
        with active_engine.connect() as connection:
            extension = connection.execute(
                text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
            ).scalar_one_or_none()
        if extension == "vector":
            return DependencyCheck("pgvector", "ok", target)
        return DependencyCheck("pgvector", "unavailable", target, "vector extension missing")
    except Exception as exc:
        return DependencyCheck("pgvector", "unavailable", target, str(exc))
```

Replace:

```python
checks.append(check_zvec_storage(settings))
```

with:

```python
checks.append(check_pgvector_extension())
```

Delete `check_zvec_storage`.

- [ ] **Step 4: Update dependency health expectations**

In `backend/tests/test_dependency_health.py`, replace `zvec` fixtures with:

```python
DependencyCheck(name="pgvector", status="ok", target="postgres:extension/vector")
```

and unavailable examples with:

```python
DependencyCheck(name="pgvector", status="unavailable", target="postgres:extension/vector", error="vector extension missing")
```

In `docs/api-spec.md`, replace the dependency list text so it says `postgres`, `neo4j` when enabled, `file_storage`, `pgvector`, and `bailian`.

- [ ] **Step 5: Run health tests**

Run: `.venv/bin/python -m pytest backend/tests/test_infrastructure_config.py backend/tests/test_dependency_health.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/agromech_api/core/infrastructure.py backend/tests/test_infrastructure_config.py backend/tests/test_dependency_health.py docs/api-spec.md
git commit -m "feat: add pgvector health check"
```

---

### Task 6: Add Rebuild Vector Index Command

**Files:**
- Create: `scripts/rebuild-vector-index.py`
- Test: `backend/tests/test_rebuild_vector_index.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_rebuild_vector_index.py`:

```python
from agromech_api.db.enums import DocumentStatus
from agromech_api.db.models import documents
from scripts.rebuild_vector_index import RebuildSummary, rebuild_vector_index, select_document_ids


def test_select_document_ids_defaults_to_indexed_documents(engine) -> None:
    seed_document(engine, document_id="doc-indexed", status=DocumentStatus.INDEXED.value)
    seed_document(engine, document_id="doc-failed", status=DocumentStatus.FAILED.value)

    assert select_document_ids(engine) == ["doc-indexed"]


def test_rebuild_vector_index_dry_run_does_not_call_indexers(engine) -> None:
    seed_document(engine, document_id="doc-indexed", status=DocumentStatus.INDEXED.value)
    calls: list[str] = []

    summary = rebuild_vector_index(
        engine,
        dry_run=True,
        search_indexer_factory=lambda engine: calls.append("search") or None,
        visual_indexer_factory=lambda engine: calls.append("visual") or None,
    )

    assert summary == RebuildSummary(selected=1, succeeded=0, failed=0, failures=[])
    assert calls == []


def test_rebuild_vector_index_continues_after_document_failure(engine) -> None:
    seed_document(engine, document_id="doc-a", status=DocumentStatus.INDEXED.value)
    seed_document(engine, document_id="doc-b", status=DocumentStatus.INDEXED.value)

    class SearchIndexer:
        def index_document(self, document_id: str):
            if document_id == "doc-a":
                raise RuntimeError("boom")
            return None

    class VisualIndexer:
        def index_document(self, document_id: str):
            return None

    summary = rebuild_vector_index(
        engine,
        search_indexer_factory=lambda engine: SearchIndexer(),
        visual_indexer_factory=lambda engine: VisualIndexer(),
    )

    assert summary.selected == 2
    assert summary.succeeded == 1
    assert summary.failed == 1
    assert summary.failures == [("doc-a", "boom")]
```

Add a local helper in the same file:

```python
def seed_document(engine, *, document_id: str, status: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            documents.insert().values(
                id=document_id,
                title=document_id,
                original_file_name=f"{document_id}.txt",
                file_hash=document_id,
                file_size_bytes=1,
                mime_type="text/plain",
                storage_uri=f"file:///{document_id}.txt",
                status=status,
                created_by_role="admin",
                visibility="public",
            )
        )
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.venv/bin/python -m pytest backend/tests/test_rebuild_vector_index.py -q`

Expected: FAIL because the script module does not exist.

- [ ] **Step 3: Create importable script module**

Because hyphenated script filenames are not importable, create `scripts/rebuild_vector_index.py` with implementation and `scripts/rebuild-vector-index.py` as a thin CLI wrapper.

Create `scripts/rebuild_vector_index.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, select

from agromech_api.core.database import get_engine
from agromech_api.db.enums import DocumentStatus
from agromech_api.db.models import documents
from agromech_api.rag.retrieval.indexing import SearchIndexer, VisualPageIndexer


@dataclass(frozen=True)
class RebuildSummary:
    selected: int
    succeeded: int
    failed: int
    failures: list[tuple[str, str]]


def select_document_ids(engine: Engine, *, document_id: str | None = None) -> list[str]:
    query = select(documents.c.id).where(documents.c.status == DocumentStatus.INDEXED.value)
    if document_id:
        query = query.where(documents.c.id == document_id)
    query = query.order_by(documents.c.updated_at)
    with engine.connect() as connection:
        return list(connection.execute(query).scalars().all())


def rebuild_vector_index(
    engine: Engine,
    *,
    document_id: str | None = None,
    include_visual: bool = True,
    dry_run: bool = False,
    search_indexer_factory=SearchIndexer,
    visual_indexer_factory=VisualPageIndexer,
) -> RebuildSummary:
    document_ids = select_document_ids(engine, document_id=document_id)
    if dry_run:
        return RebuildSummary(selected=len(document_ids), succeeded=0, failed=0, failures=[])

    succeeded = 0
    failures: list[tuple[str, str]] = []
    search_indexer = search_indexer_factory(engine)
    visual_indexer = visual_indexer_factory(engine)
    for current_document_id in document_ids:
        try:
            search_indexer.index_document(current_document_id)
            if include_visual:
                visual_indexer.index_document(current_document_id)
        except Exception as exc:
            failures.append((current_document_id, str(exc)))
            continue
        succeeded += 1
    return RebuildSummary(
        selected=len(document_ids),
        succeeded=succeeded,
        failed=len(failures),
        failures=failures,
    )
```

Create `scripts/rebuild-vector-index.py`:

```python
#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "worker"))

from agromech_api.core.database import get_engine  # noqa: E402
from scripts.rebuild_vector_index import rebuild_vector_index  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild pgvector indexes from existing AgroMech documents.")
    parser.add_argument("--document-id", help="Rebuild a single document.")
    parser.add_argument("--include-visual", action="store_true", default=True, help="Include visual page vectors.")
    parser.add_argument("--no-visual", action="store_false", dest="include_visual", help="Skip visual page vectors.")
    parser.add_argument("--dry-run", action="store_true", help="List selected document count without writing.")
    args = parser.parse_args()

    summary = rebuild_vector_index(
        get_engine(),
        document_id=args.document_id,
        include_visual=args.include_visual,
        dry_run=args.dry_run,
    )
    print(f"selected={summary.selected} succeeded={summary.succeeded} failed={summary.failed}")
    for document_id, error in summary.failures:
        print(f"failed document_id={document_id} error={error}")
    return 1 if summary.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run rebuild tests**

Run: `.venv/bin/python -m pytest backend/tests/test_rebuild_vector_index.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/rebuild_vector_index.py scripts/rebuild-vector-index.py backend/tests/test_rebuild_vector_index.py
git commit -m "feat: add pgvector rebuild command"
```

---

### Task 7: Delete Zvec Code And Tests

**Files:**
- Delete: `backend/agromech_api/integrations/vectorstores/zvec.py`
- Delete: `backend/agromech_api/integrations/vectorstores/zvec_backup.py`
- Delete: `scripts/zvec-backup.py`
- Delete or rewrite: `backend/tests/test_zvec_store.py`
- Delete or rewrite: `backend/tests/test_zvec_backup.py`
- Modify: `backend/tests/test_backend_structure.py`
- Modify: `backend/tests/test_langchain_adapters.py`

- [ ] **Step 1: Verify current Zvec references**

Run:

```bash
rg -n "zvec|Zvec|ZVEC|VECTOR_BACKEND" backend worker scripts frontend docs README.md .env.example deploy/env.prod.example
```

Expected: output lists remaining references before deletion.

- [ ] **Step 2: Delete Zvec files**

Run:

```bash
rm backend/agromech_api/integrations/vectorstores/zvec.py
rm backend/agromech_api/integrations/vectorstores/zvec_backup.py
rm scripts/zvec-backup.py
rm backend/tests/test_zvec_store.py
rm backend/tests/test_zvec_backup.py
```

- [ ] **Step 3: Remove Zvec LangChain adapter**

In `backend/agromech_api/rag/langchain/adapters.py`, delete:

- `VectorStore` import if only used by Zvec wrapper.
- `ZvecLangChainVectorStore`.
- `build_text_vector_components`.
- `build_visual_vector_components`.
- `active_text_collection`.

Keep:

- `ProviderEmbeddings`
- `VisualProviderEmbeddings`
- `AgroMechTextRetriever`
- `AgroMechVisualPageRetriever`
- answer chain helpers

- [ ] **Step 4: Update backend structure tests**

In `backend/tests/test_backend_structure.py`, replace imports of Zvec classes/functions with pgvector model assertions:

```python
from agromech_api.db.models import chunk_vector_embeddings, visual_page_vector_embeddings


def test_pgvector_tables_are_importable() -> None:
    assert chunk_vector_embeddings.name == "chunk_vector_embeddings"
    assert visual_page_vector_embeddings.name == "visual_page_vector_embeddings"
```

- [ ] **Step 5: Update LangChain adapter tests**

In `backend/tests/test_langchain_adapters.py`, delete Zvec vector store tests and keep provider/retriever tests. Add:

```python
def test_provider_embeddings_wraps_project_embedding_provider() -> None:
    class Provider:
        provider = "local"
        model = "deterministic"

        def embed(self, text: str) -> list[float]:
            return [float(len(text))]

    embeddings = ProviderEmbeddings(Provider())

    assert embeddings.embed_query("abc") == [3.0]
    assert embeddings.embed_documents(["a", "abcd"]) == [[1.0], [4.0]]
```

- [ ] **Step 6: Run reference scan**

Run:

```bash
rg -n "zvec|Zvec|ZVEC|VECTOR_BACKEND" backend worker scripts frontend .env.example deploy/env.prod.example
```

Expected: no output.

- [ ] **Step 7: Run affected tests**

Run:

```bash
.venv/bin/python -m pytest \
  backend/tests/test_backend_structure.py \
  backend/tests/test_langchain_adapters.py \
  -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add -A backend/agromech_api/integrations/vectorstores scripts backend/tests/test_zvec_store.py backend/tests/test_zvec_backup.py backend/tests/test_backend_structure.py backend/tests/test_langchain_adapters.py backend/agromech_api/rag/langchain/adapters.py
git commit -m "chore: delete zvec implementation"
```

---

### Task 8: Update Documentation And Deployment Notes

**Files:**
- Modify: `README.md`
- Modify: `docs/README.md`
- Modify: `docs/tech-design.md`
- Modify: `docs/database-design.md`
- Modify: `docs/api-spec.md`
- Modify: `docs/deployment.md`
- Modify: `docs/prd.md`
- Modify: `docs/history.md`
- Modify: `backend/tests/test_docs_sync.py`

- [ ] **Step 1: Write failing docs sync test**

In `backend/tests/test_docs_sync.py`, add:

```python
def test_current_docs_do_not_describe_zvec_as_active() -> None:
    paths = [
        "README.md",
        "docs/README.md",
        "docs/tech-design.md",
        "docs/database-design.md",
        "docs/api-spec.md",
        "docs/deployment.md",
        "docs/prd.md",
    ]
    for path in paths:
        text = Path(path).read_text(encoding="utf-8")
        assert "Zvec" not in text
        assert "zvec" not in text
```

- [ ] **Step 2: Run test to verify failure**

Run: `.venv/bin/python -m pytest backend/tests/test_docs_sync.py::test_current_docs_do_not_describe_zvec_as_active -q`

Expected: FAIL because docs still mention Zvec.

- [ ] **Step 3: Update README architecture and stack**

In `README.md`, replace vector references:

```text
Postgres · pgvector · 文件存储 · RabbitMQ
```

and:

```text
| 向量检索 | PostgreSQL + pgvector（文本和视觉向量与业务数据同库） |
```

- [ ] **Step 4: Update tech design**

In `docs/tech-design.md`:

- Replace “向量：Zvec” with “向量：PostgreSQL + pgvector”.
- Replace “使用百炼 embedding 查询 Zvec” with “使用百炼 embedding 查询 pgvector”.
- Replace `VECTOR_BACKEND=zvec` in config with pgvector extension requirement.
- Replace Zvec troubleshooting with pgvector extension/index rebuild troubleshooting:

```text
- pgvector 不可用：确认 Postgres 已安装 pgvector，数据库内存在 `vector` extension，并已运行 Alembic migration。
- 向量召回为空：运行 `scripts/rebuild-vector-index.py` 重建文本和视觉向量。
```

- [ ] **Step 5: Update database design**

In `docs/database-design.md`, replace `embedding_references` and `visual_page_embeddings` sections with:

```text
### `chunk_vector_embeddings`

文本、表格和图片 chunk 的 pgvector 向量表。通过 `chunk_id` 回连 `document_chunks`，通过 `embedding_version` 区分模型版本，`embedding vector(1024)` 存储实际向量。

### `visual_page_vector_embeddings`

PDF 页面图和图片页面的 pgvector 向量表。通过 `asset_id` 回连 `document_assets`，用于视觉页面检索。
```

- [ ] **Step 6: Update deployment docs**

In `docs/deployment.md`, add deployment step:

```text
Postgres 容器必须安装 pgvector，并在目标数据库启用 `CREATE EXTENSION IF NOT EXISTS vector`。迁移完成后运行 `scripts/rebuild-vector-index.py` 重建现有文档向量。
```

- [ ] **Step 7: Update remaining docs**

In `docs/README.md`, `docs/api-spec.md`, `docs/prd.md`, and `docs/history.md`, replace current active Zvec mentions with pgvector. Historical notes may mention that Zvec was removed only if phrased as past history:

```text
2026-07-06：向量库从 Zvec 硬切到 PostgreSQL + pgvector，旧 Zvec 文件不迁移，向量通过重建命令生成。
```

- [ ] **Step 8: Run docs scan and tests**

Run:

```bash
rg -n "Zvec|zvec" README.md docs .env.example deploy/env.prod.example backend worker scripts
.venv/bin/python -m pytest backend/tests/test_docs_sync.py -q
```

Expected: `rg` output only allowed in `docs/history.md` if written as past migration history. If the test forbids history mentions too, keep the migration note in the committed plan/spec only and remove Zvec from current docs entirely. Pytest PASS.

- [ ] **Step 9: Commit**

```bash
git add README.md docs backend/tests/test_docs_sync.py
git commit -m "docs: document pgvector vector storage"
```

---

### Task 9: Full Verification And Cleanup

**Files:**
- Review all changed files.

- [ ] **Step 1: Run full Zvec reference scan**

Run:

```bash
rg -n "zvec|Zvec|ZVEC|VECTOR_BACKEND" . \
  -g '!docs/superpowers/specs/2026-07-06-pgvector-migration-design.md' \
  -g '!docs/superpowers/plans/2026-07-06-pgvector-migration.md' \
  -g '!frontend/node_modules/**' \
  -g '!**/__pycache__/**'
```

Expected: no current-code references. If `docs/history.md` contains a past-tense migration note, verify it is intentional and not referenced by runtime tests.

- [ ] **Step 2: Run backend and worker tests**

Run:

```bash
.venv/bin/python -m pytest backend/tests worker/tests -q
```

Expected: PASS, with pgvector integration tests skipped only when the local test database does not have the `vector` extension.

- [ ] **Step 3: Run frontend tests**

Run:

```bash
npm run test --prefix frontend
```

Expected: PASS.

- [ ] **Step 4: Run lint**

Run:

```bash
scripts/lint.sh
```

Expected: PASS.

- [ ] **Step 5: Run full project verification**

Run:

```bash
scripts/test-all.sh
```

Expected: PASS. If integration tests require a running pgvector-enabled Postgres and it is not available in the current environment, record the exact skipped/failed tests and verify unit-level coverage before asking for infrastructure.

- [ ] **Step 6: Final commit if verification required small fixes**

If Step 1-5 required small follow-up fixes:

```bash
git add -A
git commit -m "test: verify pgvector migration"
```

If no fixes were needed, do not create an empty commit.

---

## Self-Review Checklist

- Data model: Task 2 covers pgvector extension, new tables, indexes, and stale table/column removal.
- Write path: Task 3 covers text and visual vector writes plus document delete cleanup.
- Retrieval path: Task 4 covers text search, visual search, hybrid trace, QA wiring, and compatibility `vector_ref`.
- Configuration: Task 1 removes Zvec settings and adds dependency.
- Health: Task 5 replaces Zvec health with pgvector extension checks.
- Rebuild: Task 6 adds dry-run, single-document, visual toggle, failure continuation, and CLI output.
- Zvec removal: Task 7 deletes implementation, backup tooling, and adapter/test references.
- Docs/deploy: Task 8 updates current docs and environment examples.
- Verification: Task 9 includes reference scan, backend/worker/frontend tests, lint, and full test suite.
