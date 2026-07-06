from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, update

from agromech_api.db.enums import DocumentStatus
from agromech_api.db.models import documents, metadata
from scripts.rebuild_vector_index import rebuild_vector_index, select_document_ids


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


def make_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    return engine


def set_updated_at(engine, document_id: str, updated_at: datetime) -> None:
    with engine.begin() as connection:
        connection.execute(update(documents).where(documents.c.id == document_id).values(updated_at=updated_at))


class RecordingIndexer:
    calls: list[str] = []

    def __init__(self, engine) -> None:
        self.engine = engine

    def index_document(self, document_id: str) -> None:
        self.calls.append(document_id)


class FailingForDocumentIndexer(RecordingIndexer):
    failure_document_id = "doc-fails"

    def index_document(self, document_id: str) -> None:
        if document_id == self.failure_document_id:
            raise RuntimeError("index failed")
        super().index_document(document_id)


def test_select_document_ids_returns_indexed_documents_ordered_by_updated_at(tmp_path):
    engine = make_engine(tmp_path)
    seed_document(engine, document_id="newer", status=DocumentStatus.INDEXED.value)
    seed_document(engine, document_id="failed", status=DocumentStatus.FAILED.value)
    seed_document(engine, document_id="older", status=DocumentStatus.INDEXED.value)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    set_updated_at(engine, "newer", now + timedelta(hours=1))
    set_updated_at(engine, "failed", now - timedelta(hours=1))
    set_updated_at(engine, "older", now)

    assert select_document_ids(engine) == ["older", "newer"]


def test_rebuild_vector_index_dry_run_returns_selected_count_without_indexing(tmp_path):
    engine = make_engine(tmp_path)
    seed_document(engine, document_id="doc-a", status=DocumentStatus.INDEXED.value)
    seed_document(engine, document_id="doc-b", status=DocumentStatus.INDEXED.value)
    RecordingIndexer.calls = []

    summary = rebuild_vector_index(
        engine,
        dry_run=True,
        search_indexer_factory=RecordingIndexer,
        visual_indexer_factory=RecordingIndexer,
    )

    assert summary.selected == 2
    assert summary.succeeded == 0
    assert summary.failed == 0
    assert summary.failures == []
    assert RecordingIndexer.calls == []


def test_rebuild_vector_index_continues_after_document_failure(tmp_path):
    engine = make_engine(tmp_path)
    seed_document(engine, document_id="doc-ok", status=DocumentStatus.INDEXED.value)
    seed_document(engine, document_id="doc-fails", status=DocumentStatus.INDEXED.value)
    seed_document(engine, document_id="doc-after", status=DocumentStatus.INDEXED.value)
    RecordingIndexer.calls = []

    summary = rebuild_vector_index(
        engine,
        include_visual=False,
        search_indexer_factory=FailingForDocumentIndexer,
        visual_indexer_factory=RecordingIndexer,
    )

    assert summary.selected == 3
    assert summary.succeeded == 2
    assert summary.failed == 1
    assert summary.failures == [("doc-fails", "index failed")]
    assert RecordingIndexer.calls == ["doc-ok", "doc-after"]


def test_rebuild_vector_index_can_target_single_document(tmp_path):
    engine = make_engine(tmp_path)
    seed_document(engine, document_id="doc-a", status=DocumentStatus.INDEXED.value)
    seed_document(engine, document_id="doc-b", status=DocumentStatus.INDEXED.value)
    RecordingIndexer.calls = []

    summary = rebuild_vector_index(
        engine,
        document_id="doc-b",
        include_visual=False,
        search_indexer_factory=RecordingIndexer,
        visual_indexer_factory=RecordingIndexer,
    )

    assert summary.selected == 1
    assert summary.succeeded == 1
    assert summary.failed == 0
    assert RecordingIndexer.calls == ["doc-b"]
