from sqlalchemy import create_engine, insert, select

from agromech_api.db.enums import ChunkType, DocumentStatus, TaskType
from agromech_api.db.models import (
    chunk_search_index,
    document_chunks,
    documents,
    embedding_references,
    metadata,
)
from agromech_api.ingestion import IngestFailure, QueuedTask
from agromech_api.search_indexing import (
    FailingEmbeddingProvider,
    SearchIndexer,
    keyword_search,
    vector_search,
)
from agromech_worker.main import process_ingest_task


def create_test_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    return engine


def seed_document_with_chunks(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id="doc-1",
                title="Manual",
                original_file_name="manual.txt",
                file_hash="hash-doc-1",
                file_size_bytes=100,
                mime_type="text/plain",
                storage_uri="file:///tmp/manual.txt",
                status=DocumentStatus.PROCESSING.value,
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
                    "content": "Kubota M7040 hydraulic pump fault code E01",
                    "summary": "M7040 hydraulic pump",
                    "source_locator": {"type": "text", "line_start": 1, "line_end": 1},
                },
                {
                    "id": "table-1",
                    "document_id": "doc-1",
                    "chunk_type": ChunkType.TABLE.value,
                    "content": "Fault Code,Action\nE02,Check fuel filter",
                    "summary": "Fault table",
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
                    "content": "Visual description: hydraulic warning light on dashboard",
                    "summary": "hydraulic warning light",
                    "source_locator": {"type": "image", "source_file": "label.png"},
                },
            ],
        )


def test_index_document_creates_fulltext_and_embedding_references(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document_with_chunks(engine)

    result = SearchIndexer(engine).index_document("doc-1")

    assert result.chunk_count == 3
    with engine.connect() as connection:
        search_rows = connection.execute(select(chunk_search_index)).mappings().all()
        embeddings = connection.execute(select(embedding_references)).mappings().all()
    assert {row["chunk_id"] for row in search_rows} == {"text-1", "table-1", "image-1"}
    assert any("M7040" in row["search_text"] for row in search_rows)
    assert any("Faults" in row["search_text"] for row in search_rows)
    assert {row["status"] for row in embeddings} == {"ready"}
    assert {row["vector_store"] for row in embeddings} == {"milvus"}
    assert {row["collection"] for row in embeddings} == {"agromech_chunks"}


def test_keyword_and_vector_search_recall_text_table_and_image_chunks(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document_with_chunks(engine)
    SearchIndexer(engine).index_document("doc-1")

    keyword_results = keyword_search(engine, "E02 fuel filter")
    vector_results = vector_search(engine, "dashboard hydraulic warning")

    assert keyword_results[0]["chunk_id"] == "table-1"
    assert any(result["chunk_id"] == "text-1" for result in keyword_search(engine, "M7040 pump"))
    assert vector_results[0]["chunk_id"] == "image-1"


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
