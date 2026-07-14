from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, insert

from agromech_api.db.enums import ChunkType, DocumentStatus
from agromech_api.db.models import chunk_search_index, document_chunks, documents, metadata
from agromech_api.rag.retrieval.bm25 import (
    PostgresBm25Retriever,
    ReferenceBm25Retriever,
    bm25_tokens,
    build_bm25_retriever,
)
from agromech_api.rag.retrieval.filters import build_retrieval_filters


def _document_row(
    document_id: str,
    *,
    model: str | None = None,
    status: str = DocumentStatus.INDEXED.value,
    visibility: str = "public",
    deleted_at: datetime | None = None,
) -> dict[str, object]:
    return {
        "id": document_id,
        "title": document_id,
        "original_file_name": f"{document_id}.txt",
        "file_hash": f"hash-{document_id}",
        "file_size_bytes": 1,
        "mime_type": "text/plain",
        "storage_uri": f"file:///{document_id}",
        "brand": "Kubota",
        "model": model,
        "document_type": "repair_manual",
        "language": "zh-CN",
        "document_version": "2024",
        "status": status,
        "visibility": visibility,
        "created_by_role": "admin",
        "deleted_at": deleted_at,
    }


def _seed_search_rows(
    engine: Engine,
    rows: list[tuple[str, str, str | None]],
) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(documents),
            [_document_row(f"doc-{chunk_id}", model=model) for chunk_id, _, model in rows],
        )
        connection.execute(
            insert(document_chunks),
            [
                {
                    "id": chunk_id,
                    "document_id": f"doc-{chunk_id}",
                    "chunk_type": ChunkType.TEXT.value,
                    "content": search_text,
                    "source_locator": {"type": "text", "line_start": 1, "line_end": 1},
                }
                for chunk_id, search_text, _ in rows
            ],
        )
        connection.execute(
            insert(chunk_search_index),
            [
                {
                    "id": f"idx-{chunk_id}",
                    "chunk_id": chunk_id,
                    "document_id": f"doc-{chunk_id}",
                    "chunk_type": ChunkType.TEXT.value,
                    "search_text": search_text,
                    "embedding_version": "v1",
                    "chunk_profile": "chunk-v1",
                    "embedding_dimension": 1024,
                }
                for chunk_id, search_text, _ in rows
            ],
        )


def _sqlite_engine(tmp_path, name: str) -> Engine:
    engine = create_engine(f"sqlite:///{tmp_path / name}")
    metadata.create_all(engine)
    return engine


def _filters(**values: str | None):
    return build_retrieval_filters(request_filters=values, viewer_user_id=None)


def test_bm25_tokens_preserve_alphanumeric_identifiers_and_segment_chinese() -> None:
    tokens = bm25_tokens("M7040 E01 HH-123_A 液压泵检查")

    assert {"m7040", "e01", "hh-123_a", "液压泵", "检查"} <= set(tokens)
    assert "m" not in tokens
    assert "7040" not in tokens


def test_reference_bm25_uses_standard_k1_and_b_formula(tmp_path) -> None:
    engine = _sqlite_engine(tmp_path, "formula.db")
    _seed_search_rows(
        engine,
        [
            ("chunk-a", "hydraulic pump pump", "A"),
            ("chunk-b", "hydraulic sensor", "B"),
            ("chunk-c", "electrical sensor", "C"),
        ],
    )

    hits = ReferenceBm25Retriever().search(
        engine,
        "hydraulic pump",
        filters=_filters(),
        limit=10,
    )

    assert [hit.chunk_id for hit in hits] == ["chunk-a", "chunk-b"]
    assert [hit.rank for hit in hits] == [1, 2]
    assert hits[0].score == pytest.approx(1.6691453431260639)
    assert hits[1].score == pytest.approx(0.4991762683023676)


def test_reference_bm25_ranks_relevant_chinese_and_code_tokens(tmp_path) -> None:
    engine = _sqlite_engine(tmp_path, "chinese.db")
    _seed_search_rows(
        engine,
        [
            ("chunk-m", "M7040 E01 液压泵 hydraulic pump 检查", "M7040"),
            ("chunk-l", "L3901 电气传感器 electrical sensor 检查", "L3901"),
        ],
    )

    hits = ReferenceBm25Retriever().search(
        engine,
        "M7040 E01 液压泵",
        filters=_filters(),
        limit=10,
    )

    assert [hit.chunk_id for hit in hits] == ["chunk-m"]
    assert hits[0].rank == 1
    assert hits[0].score > 0


def test_reference_bm25_deduplicates_repeated_query_terms(tmp_path) -> None:
    engine = _sqlite_engine(tmp_path, "duplicate-query.db")
    _seed_search_rows(
        engine,
        [
            ("chunk-a", "hydraulic pump pump", "A"),
            ("chunk-b", "hydraulic sensor", "B"),
        ],
    )
    retriever = ReferenceBm25Retriever()

    unique_hits = retriever.search(engine, "pump", filters=_filters(), limit=10)
    repeated_hits = retriever.search(engine, "pump pump pump", filters=_filters(), limit=10)

    assert repeated_hits == unique_hits


def test_reference_bm25_returns_no_hits_for_empty_query_or_corpus(tmp_path) -> None:
    populated_engine = _sqlite_engine(tmp_path, "empty-query.db")
    _seed_search_rows(populated_engine, [("chunk-a", "hydraulic pump", "A")])
    empty_engine = _sqlite_engine(tmp_path, "empty-corpus.db")
    retriever = ReferenceBm25Retriever()

    assert retriever.search(populated_engine, "", filters=_filters(), limit=10) == []
    assert retriever.search(empty_engine, "pump", filters=_filters(), limit=10) == []


def test_reference_bm25_applies_explicit_model_before_limit(tmp_path) -> None:
    engine = _sqlite_engine(tmp_path, "filter-before-limit.db")
    _seed_search_rows(
        engine,
        [
            ("chunk-m", "检查 unrelated words filler", "M7040"),
            ("chunk-l", "检查 检查 检查 exact", "L3901"),
        ],
    )
    retriever = ReferenceBm25Retriever()

    unfiltered = retriever.search(engine, "检查", filters=_filters(), limit=1)
    filtered = retriever.search(engine, "检查", filters=_filters(model="M7040"), limit=1)

    assert [hit.chunk_id for hit in unfiltered] == ["chunk-l"]
    assert [hit.chunk_id for hit in filtered] == ["chunk-m"]


def test_reference_bm25_excludes_unavailable_documents(tmp_path) -> None:
    engine = _sqlite_engine(tmp_path, "availability.db")
    with engine.begin() as connection:
        connection.execute(
            insert(documents),
            [
                _document_row("doc-public"),
                _document_row("doc-queued", status=DocumentStatus.QUEUED.value),
                _document_row("doc-private", visibility="private"),
                _document_row("doc-deleted", deleted_at=datetime.now(UTC)),
            ],
        )
        connection.execute(
            insert(document_chunks),
            [
                {
                    "id": f"chunk-{suffix}",
                    "document_id": f"doc-{suffix}",
                    "chunk_type": ChunkType.TEXT.value,
                    "content": "pump",
                    "source_locator": {"type": "text"},
                }
                for suffix in ("public", "queued", "private", "deleted")
            ],
        )
        connection.execute(
            insert(chunk_search_index),
            [
                {
                    "id": f"idx-{suffix}",
                    "chunk_id": f"chunk-{suffix}",
                    "document_id": f"doc-{suffix}",
                    "chunk_type": ChunkType.TEXT.value,
                    "search_text": "pump",
                    "embedding_version": "v1",
                    "chunk_profile": "chunk-v1",
                    "embedding_dimension": 1024,
                }
                for suffix in ("public", "queued", "private", "deleted")
            ],
        )

    hits = ReferenceBm25Retriever().search(engine, "pump", filters=_filters(), limit=10)

    assert [hit.chunk_id for hit in hits] == ["chunk-public"]


def test_reference_bm25_stably_orders_equal_scores_by_chunk_id(tmp_path) -> None:
    engine = _sqlite_engine(tmp_path, "stable-order.db")
    _seed_search_rows(
        engine,
        [
            ("chunk-z", "hydraulic pump", "Z"),
            ("chunk-a", "hydraulic pump", "A"),
        ],
    )

    hits = ReferenceBm25Retriever().search(engine, "pump", filters=_filters(), limit=10)

    assert [hit.chunk_id for hit in hits] == ["chunk-a", "chunk-z"]
    assert [hit.rank for hit in hits] == [1, 2]


class _Rows:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows


class _RecordingConnection:
    def __init__(self, engine: _RecordingPostgresEngine) -> None:
        self.engine = engine

    def __enter__(self) -> _RecordingConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: object, params: dict[str, Any]) -> _Rows:
        self.engine.statement = str(statement)
        self.engine.params = params
        return _Rows([{"chunk_id": "chunk-m", "score": 4.25}])


class _RecordingPostgresEngine:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self) -> None:
        self.statement = ""
        self.params: dict[str, Any] = {}

    def connect(self) -> _RecordingConnection:
        return _RecordingConnection(self)


def test_postgres_bm25_uses_parameterized_pg_search_and_all_filters() -> None:
    engine = _RecordingPostgresEngine()
    query = "pump' OR 1=1 --"
    filters = build_retrieval_filters(
        request_filters={
            "brand": "Kubota",
            "model": "M7040",
            "document_type": "repair_manual",
            "language": "zh-CN",
            "document_version": "2024",
            "subsystem": " Hydraulic ",
        },
        viewer_user_id="owner-1",
    )

    hits = PostgresBm25Retriever().search(engine, query, filters=filters, limit=7)  # type: ignore[arg-type]

    sql = " ".join(engine.statement.split())
    assert "csi.search_text ||| :query" in sql
    assert "pdb.score(csi.id)" in sql
    assert "d.status = 'indexed'" in sql
    assert "d.deleted_at IS NULL" in sql
    assert "d.visibility = 'public'" in sql
    assert "d.owner_user_id = :viewer_user_id" in sql
    for field in ("brand", "model", "document_type", "language", "document_version"):
        assert f"d.{field} = :{field}" in sql
    assert "cel.entity_type = 'system'" in sql
    assert "cel.normalized_value = :subsystem" in sql
    assert "ORDER BY pdb.score(csi.id) DESC, csi.id ASC" in sql
    assert query not in sql
    assert engine.params == {
        "viewer_user_id": "owner-1",
        "brand": "Kubota",
        "model": "M7040",
        "document_type": "repair_manual",
        "language": "zh-CN",
        "document_version": "2024",
        "subsystem": "hydraulic",
        "query": query,
        "limit": 7,
    }
    assert [(hit.chunk_id, hit.rank, hit.score) for hit in hits] == [("chunk-m", 1, 4.25)]


def test_build_bm25_retriever_selects_backend_from_dialect(tmp_path) -> None:
    sqlite_engine = _sqlite_engine(tmp_path, "factory.db")
    postgres_engine = _RecordingPostgresEngine()

    assert isinstance(build_bm25_retriever(sqlite_engine), ReferenceBm25Retriever)
    assert isinstance(
        build_bm25_retriever(postgres_engine),  # type: ignore[arg-type]
        PostgresBm25Retriever,
    )
