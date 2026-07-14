import pytest
from sqlalchemy import create_engine, insert, select
from sqlalchemy.dialects import postgresql

from agromech_api.db.enums import ChunkType, DocumentStatus, TaskType
from agromech_api.db.models import (
    chunk_vector_embeddings,
    chunk_search_index,
    document_assets,
    document_chunks,
    documents,
    ingest_tasks,
    metadata,
    visual_page_vector_embeddings,
)
from agromech_api.ingestion import IngestFailure, QueuedTask
from agromech_api.rag.retrieval.indexing import (
    DeterministicEmbeddingProvider,
    DeterministicVisualEmbeddingProvider,
    FailingEmbeddingProvider,
    SearchIndexer,
    VisualPageIndexer,
    keyword_search,
    vector_search,
    visual_page_search,
)
from agromech_api.rag.retrieval.filters import build_retrieval_filters
from agromech_api.core.config import Settings
from agromech_worker.main import run_once
from agromech_worker.main import process_ingest_task


def create_test_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    return engine


def seed_searchable_document(engine, *, content: str = "Kubota M7040 hydraulic pump fault code E01") -> tuple[str, str]:
    default_text = "Kubota M7040 hydraulic pump fault code E01"
    image_content = (
        "Visual description: hydraulic warning light on dashboard"
        if content == default_text
        else "Visual description: generic instrument panel"
    )
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id="doc-1",
                visibility="public",
                title="M7040 维修手册",
                original_file_name="manual.txt",
                file_hash="hash-doc-1",
                file_size_bytes=100,
                mime_type="text/plain",
                storage_uri="file:///tmp/manual.txt",
                status=DocumentStatus.INDEXED.value,
                created_by_role="admin",
            )
        )
        connection.execute(
            insert(document_chunks),
            [
                {
                    "id": "text-1",
                    "document_id": "doc-1",
                    "chunk_type": ChunkType.TEXT.value,
                    "content": content,
                    "summary": "M7040 hydraulic pump",
                    "source_locator": {"type": "text", "line_start": 1, "line_end": 1},
                },
                {
                    "id": "table-1",
                    "document_id": "doc-1",
                    "chunk_type": ChunkType.TABLE.value,
                    "content": "Fault Code,Action,Fluid Spec\nE02,Check fuel filter,Hydraulic oil ISO VG 46",
                    "summary": "Fault and oil specification table",
                    "worksheet_name": "Faults",
                    "row_start": 1,
                    "row_end": 2,
                    "source_locator": {
                        "type": "xlsx",
                        "worksheet_name": "Faults",
                        "row_start": 1,
                        "row_end": 2,
                    },
                },
                {
                    "id": "image-1",
                    "document_id": "doc-1",
                    "chunk_type": ChunkType.IMAGE.value,
                    "content": image_content,
                    "summary": "hydraulic warning light" if content == default_text else "instrument panel",
                    "source_locator": {"type": "image", "source_file": "label.png"},
                },
            ],
        )
    return "doc-1", "text-1"


def seed_page_image_asset(engine, tmp_path) -> tuple[str, str]:
    page_path = tmp_path / "page-1.png"
    page_path.write_bytes(b"fake image bytes")
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id="doc-visual",
                visibility="public",
                title="M7040 Page Manual",
                original_file_name="manual.pdf",
                file_hash="hash-doc-visual",
                file_size_bytes=100,
                mime_type="application/pdf",
                storage_uri="file:///tmp/manual.pdf",
                status=DocumentStatus.PROCESSING.value,
                created_by_role="admin",
            )
        )
        connection.execute(
            insert(document_assets).values(
                id="asset-page-1",
                document_id="doc-visual",
                asset_type="page_image",
                storage_uri=f"file://{page_path}",
                mime_type="image/png",
                page_number=1,
                source_locator={"type": "pdf_page", "page": 1},
                ocr_text="Hydraulic pump location diagram",
                visual_observation={"vision": {"description": "hydraulic pump location"}},
            )
        )
    return "doc-visual", "asset-page-1"


def test_index_document_writes_pgvector_rows(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_searchable_document(engine)

    result = SearchIndexer(engine, embedding_version="emb_test").index_document("doc-1")

    assert result.chunk_count == 3
    with engine.connect() as connection:
        search_rows = connection.execute(select(chunk_search_index)).mappings().all()
        embeddings = connection.execute(select(chunk_vector_embeddings)).mappings().all()
    assert {row["chunk_id"] for row in search_rows} == {"text-1", "table-1", "image-1"}
    assert any("M7040" in row["search_text"] for row in search_rows)
    assert any("Faults" in row["search_text"] for row in search_rows)
    assert len(embeddings) == 3
    assert {row["chunk_id"] for row in embeddings} == {"text-1", "table-1", "image-1"}
    assert {row["document_id"] for row in embeddings} == {"doc-1"}
    assert {row["provider"] for row in embeddings} == {"local"}
    assert {row["embedding_version"] for row in embeddings} == {"emb_test"}
    assert {row["status"] for row in embeddings} == {"ready"}
    assert {row["embedding_dimension"] for row in embeddings} == {1024}
    assert all(len(row["embedding"]) == 1024 for row in embeddings)


def test_index_document_replaces_pgvector_rows_for_same_chunk_and_version(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_searchable_document(engine)
    SearchIndexer(engine).index_document("doc-1")
    SearchIndexer(engine).index_document("doc-1")

    with engine.connect() as connection:
        embeddings = connection.execute(select(chunk_vector_embeddings)).mappings().all()
    assert len(embeddings) == 3


def test_visual_page_indexer_writes_pgvector_rows(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_page_image_asset(engine, tmp_path)

    class FakeVisualEmbeddingProvider:
        provider = "bailian"
        model = "qwen3-vl-embedding"

        def embed_image(self, path, *, text=None):
            assert path.name == "page-1.png"
            assert text == "Hydraulic pump location diagram"
            return [1.0] + [0.0] * 1023

        def embed_query(self, text):
            return [1.0] + [0.0] * 1023

    result = VisualPageIndexer(
        engine,
        embedding_provider=FakeVisualEmbeddingProvider(),
        embedding_version="vis_v1",
        embedding_dimension=1024,
    ).index_document("doc-visual")

    assert result.chunk_count == 1
    with engine.connect() as connection:
        rows = connection.execute(select(visual_page_vector_embeddings)).mappings().all()
    assert len(rows) == 1
    assert rows[0]["asset_id"] == "asset-page-1"
    assert rows[0]["document_id"] == "doc-visual"
    assert rows[0]["embedding_version"] == "vis_v1"
    assert rows[0]["status"] == "ready"
    assert rows[0]["embedding_dimension"] == 1024
    assert len(rows[0]["embedding"]) == 1024


def test_index_document_records_embedding_version_profile_and_dimension(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_searchable_document(engine)

    SearchIndexer(
        engine,
        embedding_version="emb_v2",
        chunk_profile="chunk-v2",
        embedding_dimension=1024,
    ).index_document("doc-1")

    with engine.connect() as connection:
        search_rows = connection.execute(select(chunk_search_index)).mappings().all()
        embeddings = connection.execute(select(chunk_vector_embeddings)).mappings().all()
    assert {row["embedding_version"] for row in search_rows} == {"emb_v2"}
    assert {row["chunk_profile"] for row in search_rows} == {"chunk-v2"}
    assert {row["embedding_dimension"] for row in search_rows} == {1024}
    assert {row["embedding_version"] for row in embeddings} == {"emb_v2"}
    assert {row["chunk_profile"] for row in embeddings} == {"chunk-v2"}
    assert {row["embedding_dimension"] for row in embeddings} == {1024}


def test_vector_search_returns_pgvector_refs(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
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
        filters=build_retrieval_filters(request_filters={}, viewer_user_id=None),
        active_embedding_version="emb_test",
        embedding_provider=DeterministicEmbeddingProvider(),
    )

    assert results[0]["chunk_id"] == chunk_id
    assert results[0]["embedding_id"]
    assert results[0]["vector_ref"].startswith("pgvector://chunk_vector_embeddings/")


def test_vector_search_applies_model_filter_before_limit(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    query_embedding = [1.0] + [0.0] * 1023
    allowed_embedding = [0.8, 0.6] + [0.0] * 1022
    with engine.begin() as connection:
        connection.execute(
            insert(documents),
            [
                {
                    "id": "doc-forbidden",
                    "visibility": "public",
                    "title": "L3901 Manual",
                    "original_file_name": "l3901.txt",
                    "file_hash": "hash-forbidden",
                    "file_size_bytes": 100,
                    "mime_type": "text/plain",
                    "storage_uri": "file:///tmp/l3901.txt",
                    "brand": "Kubota",
                    "model": "L3901",
                    "status": DocumentStatus.INDEXED.value,
                    "created_by_role": "admin",
                },
                {
                    "id": "doc-allowed",
                    "visibility": "public",
                    "title": "M7040 Manual",
                    "original_file_name": "m7040.txt",
                    "file_hash": "hash-allowed",
                    "file_size_bytes": 100,
                    "mime_type": "text/plain",
                    "storage_uri": "file:///tmp/m7040.txt",
                    "brand": "Kubota",
                    "model": "M7040",
                    "status": DocumentStatus.INDEXED.value,
                    "created_by_role": "admin",
                },
            ],
        )
        connection.execute(
            insert(document_chunks),
            [
                {
                    "id": "chunk-forbidden",
                    "document_id": "doc-forbidden",
                    "chunk_type": ChunkType.TEXT.value,
                    "content": "globally highest dense match",
                    "source_locator": {"type": "text", "line_start": 1, "line_end": 1},
                },
                {
                    "id": "chunk-allowed",
                    "document_id": "doc-allowed",
                    "chunk_type": ChunkType.TEXT.value,
                    "content": "second highest dense match",
                    "source_locator": {"type": "text", "line_start": 1, "line_end": 1},
                },
            ],
        )
        connection.execute(
            insert(chunk_vector_embeddings),
            [
                {
                    "id": "embedding-forbidden",
                    "chunk_id": "chunk-forbidden",
                    "document_id": "doc-forbidden",
                    "provider": "test",
                    "model": "fixed",
                    "embedding_version": "emb_adversarial",
                    "chunk_profile": "chunk-v1",
                    "embedding_dimension": 1024,
                    "embedding": query_embedding,
                    "status": "ready",
                },
                {
                    "id": "embedding-allowed",
                    "chunk_id": "chunk-allowed",
                    "document_id": "doc-allowed",
                    "provider": "test",
                    "model": "fixed",
                    "embedding_version": "emb_adversarial",
                    "chunk_profile": "chunk-v1",
                    "embedding_dimension": 1024,
                    "embedding": allowed_embedding,
                    "status": "ready",
                },
            ],
        )

    class FixedQueryEmbeddingProvider:
        def embed(self, _query):
            return query_embedding

    unfiltered = vector_search(
        engine,
        "hydraulic",
        filters=build_retrieval_filters(request_filters={}, viewer_user_id=None),
        limit=1,
        active_embedding_version="emb_adversarial",
        embedding_provider=FixedQueryEmbeddingProvider(),
    )
    filtered = vector_search(
        engine,
        "hydraulic",
        filters=build_retrieval_filters(
            request_filters={"model": "M7040"}, viewer_user_id=None
        ),
        limit=1,
        active_embedding_version="emb_adversarial",
        embedding_provider=FixedQueryEmbeddingProvider(),
    )

    assert [result["chunk_id"] for result in unfiltered] == ["chunk-forbidden"]
    assert [result["chunk_id"] for result in filtered] == ["chunk-allowed"]


def test_visual_page_search_returns_pgvector_refs(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
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


def test_vector_search_uses_pgvector_distance_operator_for_postgres() -> None:
    captured = {}

    class FakeDialect:
        name = "postgresql"

    class FakeResult:
        def mappings(self):
            return self

        def all(self):
            return [
                {
                    "embedding_id": "embedding-1",
                    "chunk_id": "chunk-1",
                    "embedding_version": "emb_test",
                    "chunk_type": ChunkType.TEXT.value,
                    "score": 0.82,
                }
            ]

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def execute(self, statement):
            captured["sql"] = str(statement.compile(dialect=postgresql.dialect()))
            captured["params"] = statement.compile(dialect=postgresql.dialect()).params
            return FakeResult()

    class FakeEngine:
        dialect = FakeDialect()

        def connect(self):
            return FakeConnection()

    class FakeProvider:
        def embed(self, text):
            assert text == "hydraulic"
            return [1.0, 0.0]

    results = vector_search(
        FakeEngine(),
        "hydraulic",
        filters=build_retrieval_filters(request_filters={}, viewer_user_id=None),
        active_embedding_version="emb_test",
        embedding_provider=FakeProvider(),
    )

    assert "<=>" in captured["sql"]
    assert "LIMIT" in captured["sql"]
    assert len(captured["params"]["query_embedding"]) == 1024
    assert results == [
        {
            "chunk_id": "chunk-1",
            "score": 0.82,
            "chunk_type": ChunkType.TEXT.value,
            "embedding_version": "emb_test",
            "embedding_id": "embedding-1",
            "vector_ref": "pgvector://chunk_vector_embeddings/embedding-1",
        }
    ]


def test_visual_page_search_uses_pgvector_distance_operator_for_postgres() -> None:
    captured = {}

    class FakeDialect:
        name = "postgresql"

    class FakeResult:
        def mappings(self):
            return self

        def all(self):
            return [
                {
                    "embedding_id": "visual-embedding-1",
                    "asset_id": "asset-1",
                    "document_id": "doc-1",
                    "page_number": 1,
                    "embedding_version": "vis_test",
                    "storage_uri": "file:///tmp/page.png",
                    "source_locator": {"type": "pdf_page", "page": 1},
                    "ocr_text": "hydraulic diagram",
                    "visual_observation": {"description": "pump"},
                    "score": 0.77,
                }
            ]

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def execute(self, statement):
            captured["sql"] = str(statement.compile(dialect=postgresql.dialect()))
            captured["params"] = statement.compile(dialect=postgresql.dialect()).params
            return FakeResult()

    class FakeEngine:
        dialect = FakeDialect()

        def connect(self):
            return FakeConnection()

    class FakeProvider:
        def embed_query(self, text):
            assert text == "hydraulic page"
            return [1.0, 0.0]

    results = visual_page_search(
        FakeEngine(),
        "hydraulic page",
        active_embedding_version="vis_test",
        embedding_provider=FakeProvider(),
    )

    assert "<=>" in captured["sql"]
    assert "LIMIT" in captured["sql"]
    assert len(captured["params"]["query_embedding"]) == 1024
    assert results[0]["asset_id"] == "asset-1"
    assert results[0]["vector_ref"] == "pgvector://visual_page_vector_embeddings/visual-embedding-1"


def test_vector_search_filters_to_active_embedding_version(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    document_id, chunk_id = seed_searchable_document(engine, content="dashboard hydraulic warning")
    SearchIndexer(
        engine,
        embedding_provider=DeterministicEmbeddingProvider(),
        embedding_version="emb_active",
        embedding_dimension=256,
    ).index_document(document_id)
    SearchIndexer(
        engine,
        embedding_provider=DeterministicEmbeddingProvider(),
        embedding_version="emb_old",
        embedding_dimension=256,
    ).index_document(document_id)

    results = vector_search(
        engine,
        "dashboard hydraulic warning",
        filters=build_retrieval_filters(request_filters={}, viewer_user_id=None),
        active_embedding_version="emb_active",
        embedding_provider=DeterministicEmbeddingProvider(),
    )

    assert results
    assert results[0]["chunk_id"] == chunk_id
    assert {result["embedding_version"] for result in results} == {"emb_active"}


def test_run_once_uses_configured_embedding_provider(tmp_path, monkeypatch) -> None:
    engine = create_test_engine(tmp_path)
    source_path = tmp_path / "manual.txt"
    source_path.write_text("Kubota M7040 hydraulic pump", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        file_storage_backend="local",
        graph_backend="local",
        model_provider="local",
        embedding_provider="local",
        embedding_dimension=256,
    )
    monkeypatch.setattr("agromech_worker.main.get_settings", lambda: settings)
    captured = {}

    class FakeEmbeddingProvider:
        pass

    class FakeVisualEmbeddingProvider:
        pass

    class FakeSearchIndexer:
        def __init__(self, active_engine, *, embedding_provider):
            captured["engine"] = active_engine
            captured["embedding_provider"] = embedding_provider

        def index_document(self, document_id):
            return type("IndexResult", (), {"chunk_count": 1})()

    class FakeVisualPageIndexer:
        def __init__(self, active_engine, *, embedding_provider):
            captured["visual_engine"] = active_engine
            captured["visual_embedding_provider"] = embedding_provider

    fake_embedding_provider = FakeEmbeddingProvider()
    fake_visual_embedding_provider = FakeVisualEmbeddingProvider()
    monkeypatch.setattr(
        "agromech_api.integrations.embeddings.text.build_embedding_provider",
        lambda active_settings: fake_embedding_provider,
    )
    monkeypatch.setattr(
        "agromech_api.integrations.embeddings.visual.build_visual_embedding_provider",
        lambda active_settings: fake_visual_embedding_provider,
    )
    monkeypatch.setattr("agromech_worker.main.SearchIndexer", FakeSearchIndexer)
    monkeypatch.setattr("agromech_worker.main.VisualPageIndexer", FakeVisualPageIndexer)

    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id="doc-1",
                title="Manual",
                original_file_name="manual.txt",
                file_hash="hash-doc-1",
                file_size_bytes=source_path.stat().st_size,
                mime_type="text/plain",
                storage_uri=f"file://{source_path}",
                visibility="public",
                status=DocumentStatus.QUEUED.value,
                created_by_role="admin",
            )
        )
        connection.execute(
            insert(ingest_tasks).values(
                id="task-1",
                document_id="doc-1",
                task_type=TaskType.INGEST.value,
                status="queued",
                attempt_count=0,
                stage="queued",
            )
        )

    result = run_once(engine=engine)

    assert result == "succeeded"
    assert captured == {
        "engine": engine,
        "embedding_provider": fake_embedding_provider,
        "visual_engine": engine,
        "visual_embedding_provider": fake_visual_embedding_provider,
    }


def test_keyword_search_recalls_text_table_and_image_chunks(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_searchable_document(engine)
    SearchIndexer(engine).index_document("doc-1")

    keyword_results = keyword_search(engine, "E02 fuel filter")

    assert keyword_results[0]["chunk_id"] == "table-1"
    assert any(result["chunk_id"] == "text-1" for result in keyword_search(engine, "M7040 pump"))
    assert any(result["chunk_id"] == "image-1" for result in keyword_search(engine, "dashboard hydraulic warning"))


def test_keyword_search_recalls_document_title_and_table_fields(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_searchable_document(engine)
    SearchIndexer(engine).index_document("doc-1")

    title_results = keyword_search(engine, "维修手册")
    table_results = keyword_search(engine, "Fault Code Action")
    fluid_results = keyword_search(engine, "hydraulic oil ISO VG 46")

    assert {result["chunk_id"] for result in title_results} == {"text-1", "table-1", "image-1"}
    assert table_results[0]["chunk_id"] == "table-1"
    assert fluid_results[0]["chunk_id"] == "table-1"


def test_indexing_failure_prevents_worker_from_succeeding(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    source_path = tmp_path / "manual.txt"
    source_path.write_text("Kubota M7040 hydraulic pump", encoding="utf-8")
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id="doc-1",
                title="Manual",
                original_file_name="manual.txt",
                file_hash="hash-doc-1",
                file_size_bytes=source_path.stat().st_size,
                mime_type="text/plain",
                storage_uri=f"file://{source_path}",
                visibility="public",
                status=DocumentStatus.PROCESSING.value,
                created_by_role="admin",
            )
        )

    try:
        process_ingest_task(
            engine,
            QueuedTask(
                id="task-1",
                document_id="doc-1",
                task_type=TaskType.INGEST.value,
                attempt_count=0,
                stage="processing",
            ),
            indexer=SearchIndexer(engine, embedding_provider=FailingEmbeddingProvider()),
        )
    except IngestFailure as exc:
        assert exc.code == "embedding_failed"
        assert exc.stage == "index"
    else:
        raise AssertionError("expected indexing failure")

    with engine.connect() as connection:
        document_status = connection.execute(select(documents.c.status)).scalar_one()
    assert document_status == DocumentStatus.PROCESSING.value
