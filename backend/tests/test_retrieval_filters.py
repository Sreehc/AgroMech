from datetime import UTC, datetime
from dataclasses import FrozenInstanceError

import pytest
from sqlalchemy import create_engine, insert, select

from agromech_api.core.config import Settings
from agromech_api.db.enums import ChunkType, DocumentStatus
from agromech_api.db.models import chunk_entity_links, document_chunks, documents, metadata, users
from agromech_api.rag.retrieval.filters import (
    RetrievalFilters,
    build_retrieval_filters,
    chunk_filter_conditions,
    document_filter_conditions,
)


def test_retrieval_settings_defaults_and_ordering() -> None:
    settings = Settings(_env_file=None)

    assert settings.bm25_top_k == 50
    assert settings.dense_top_k == 50
    assert settings.dense_only_min_similarity == 0.25
    assert settings.rrf_k == 60
    assert settings.rrf_dense_weight == 1.0
    assert settings.rrf_bm25_weight == 1.0
    assert settings.fusion_top_k == 30
    assert settings.query_rewrite_model == "qwen3.6-flash"
    assert settings.query_rewrite_timeout_seconds == 10.0
    assert settings.optional_retrieval_channel_list == ["dense", "bm25", "vision", "rerank"]

    with pytest.raises(ValueError, match="FINAL_EVIDENCE_LIMIT must be <= FUSION_TOP_K"):
        Settings(_env_file=None, final_evidence_limit=31, fusion_top_k=30, rerank_top_k=40)

    with pytest.raises(ValueError, match="FINAL_EVIDENCE_LIMIT must be <= RERANK_TOP_K"):
        Settings(_env_file=None, final_evidence_limit=6, rerank_top_k=5)

    with pytest.raises(ValueError, match="RERANK_TOP_K must be <= FUSION_TOP_K"):
        Settings(_env_file=None, rerank_top_k=31, fusion_top_k=30)

    with pytest.raises(ValueError, match="RRF weights must not both be zero"):
        Settings(_env_file=None, rrf_dense_weight=0, rrf_bm25_weight=0)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_query_rewrite_timeout_must_be_finite(value: float) -> None:
    with pytest.raises(ValueError, match="QUERY_REWRITE_TIMEOUT_SECONDS must be finite"):
        Settings(_env_file=None, query_rewrite_timeout_seconds=value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), -0.1, 1.1])
def test_dense_only_min_similarity_must_be_a_probability(value: float) -> None:
    with pytest.raises(ValueError, match="DENSE_ONLY_MIN_SIMILARITY must be between 0 and 1"):
        Settings(_env_file=None, dense_only_min_similarity=value)


@pytest.mark.parametrize("field_name", ["rrf_dense_weight", "rrf_bm25_weight"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_rrf_weights_must_be_finite(field_name: str, value: float) -> None:
    with pytest.raises(ValueError, match="RRF weights must be finite"):
        Settings(_env_file=None, **{field_name: value})


def test_retrieval_filters_are_frozen() -> None:
    filters = RetrievalFilters(viewer_user_id=None)

    with pytest.raises(FrozenInstanceError):
        setattr(filters, "brand", "Kubota")


def test_document_filters_enforce_owner_visibility_and_indexed_status(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'visibility.db'}")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(users),
            [
                {
                    "id": "owner-1",
                    "username": "owner-1",
                    "password_hash": "hash",
                    "role": "user",
                },
                {
                    "id": "owner-2",
                    "username": "owner-2",
                    "password_hash": "hash",
                    "role": "user",
                },
            ],
        )
        connection.execute(
            insert(documents),
            [
                _document_row("public-indexed"),
                _document_row("private-owned", visibility="private", owner_user_id="owner-1"),
                _document_row("private-other", visibility="private", owner_user_id="owner-2"),
                _document_row("public-queued", status=DocumentStatus.QUEUED.value),
            ],
        )

    def visible_document_ids(viewer_user_id: str | None) -> set[str]:
        filters = build_retrieval_filters(request_filters={}, viewer_user_id=viewer_user_id)
        with engine.connect() as connection:
            return set(
                connection.execute(
                    select(documents.c.id).where(*document_filter_conditions(filters))
                ).scalars()
            )

    assert visible_document_ids(None) == {"public-indexed"}
    assert visible_document_ids("other-user") == {"public-indexed"}
    assert visible_document_ids("owner-1") == {"public-indexed", "private-owned"}


def test_chunk_filters_match_only_requested_subsystem(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'subsystem.db'}")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(documents), [_document_row("subsystem-document")])
        connection.execute(
            insert(document_chunks),
            [
                {
                    "id": "hydraulic-chunk",
                    "document_id": "subsystem-document",
                    "chunk_type": ChunkType.TEXT.value,
                    "content": "Hydraulic system troubleshooting",
                    "source_locator": {"type": "text"},
                },
                {
                    "id": "component-chunk",
                    "document_id": "subsystem-document",
                    "chunk_type": ChunkType.TEXT.value,
                    "content": "Hydraulic component troubleshooting",
                    "source_locator": {"type": "text"},
                },
            ],
        )
        connection.execute(
            insert(chunk_entity_links),
            [
                {
                    "id": "hydraulic-system-link",
                    "chunk_id": "hydraulic-chunk",
                    "document_id": "subsystem-document",
                    "entity_type": "system",
                    "entity_value": "Hydraulic",
                    "normalized_value": "hydraulic",
                    "confidence": 1.0,
                    "source": "rule",
                },
                {
                    "id": "hydraulic-component-link",
                    "chunk_id": "component-chunk",
                    "document_id": "subsystem-document",
                    "entity_type": "component",
                    "entity_value": "Hydraulic",
                    "normalized_value": "hydraulic",
                    "confidence": 1.0,
                    "source": "rule",
                },
            ],
        )

    def matching_chunk_ids(subsystem: str) -> list[str]:
        filters = build_retrieval_filters(
            request_filters={"subsystem": subsystem},
            viewer_user_id=None,
        )
        with engine.connect() as connection:
            return connection.execute(
                select(document_chunks.c.id).where(
                    *chunk_filter_conditions(document_chunks.c.id, filters)
                )
            ).scalars().all()

    assert matching_chunk_ids(" Hydraulic ") == ["hydraulic-chunk"]
    assert matching_chunk_ids("transmission") == []


def test_explicit_filters_are_applied_before_retrieval(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'filters.db'}")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(documents),
            [
                {
                    "id": "public-m7040",
                    "title": "M7040 Manual",
                    "original_file_name": "m7040.txt",
                    "file_hash": "hash-1",
                    "file_size_bytes": 1,
                    "mime_type": "text/plain",
                    "storage_uri": "file:///m7040.txt",
                    "brand": "Kubota",
                    "model": "M7040",
                    "document_type": "repair_manual",
                    "language": "zh-CN",
                    "status": DocumentStatus.INDEXED.value,
                    "visibility": "public",
                    "created_by_role": "admin",
                    "deleted_at": None,
                },
                {
                    "id": "public-l3901",
                    "title": "L3901 Manual",
                    "original_file_name": "l3901.txt",
                    "file_hash": "hash-2",
                    "file_size_bytes": 1,
                    "mime_type": "text/plain",
                    "storage_uri": "file:///l3901.txt",
                    "brand": "Kubota",
                    "model": "L3901",
                    "document_type": "repair_manual",
                    "language": "zh-CN",
                    "status": DocumentStatus.INDEXED.value,
                    "visibility": "public",
                    "created_by_role": "admin",
                    "deleted_at": None,
                },
                {
                    "id": "deleted-m7040",
                    "title": "Deleted M7040 Manual",
                    "original_file_name": "deleted-m7040.txt",
                    "file_hash": "hash-3",
                    "file_size_bytes": 1,
                    "mime_type": "text/plain",
                    "storage_uri": "file:///deleted-m7040.txt",
                    "brand": "Kubota",
                    "model": "M7040",
                    "document_type": "repair_manual",
                    "language": "zh-CN",
                    "status": DocumentStatus.INDEXED.value,
                    "visibility": "public",
                    "created_by_role": "admin",
                    "deleted_at": datetime.now(UTC),
                },
            ],
        )

    filters = build_retrieval_filters(
        request_filters={"brand": "Kubota", "model": "M7040", "language": "zh-CN"},
        viewer_user_id=None,
    )
    with engine.connect() as connection:
        ids = connection.execute(
            select(documents.c.id).where(*document_filter_conditions(filters))
        ).scalars().all()

    assert ids == ["public-m7040"]


def _document_row(
    document_id: str,
    *,
    status: str = DocumentStatus.INDEXED.value,
    visibility: str = "public",
    owner_user_id: str | None = None,
) -> dict[str, object]:
    return {
        "id": document_id,
        "title": document_id,
        "original_file_name": f"{document_id}.txt",
        "file_hash": f"hash-{document_id}",
        "file_size_bytes": 1,
        "mime_type": "text/plain",
        "storage_uri": f"file:///{document_id}.txt",
        "status": status,
        "visibility": visibility,
        "owner_user_id": owner_user_id,
        "created_by_role": "user",
        "deleted_at": None,
    }
