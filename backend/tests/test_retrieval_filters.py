from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, insert, select

from agromech_api.core.config import Settings
from agromech_api.db.enums import DocumentStatus
from agromech_api.db.models import documents, metadata
from agromech_api.rag.retrieval.filters import (
    build_retrieval_filters,
    document_filter_conditions,
)


def test_retrieval_settings_defaults_and_ordering() -> None:
    settings = Settings(_env_file=None)

    assert settings.bm25_top_k == 50
    assert settings.dense_top_k == 50
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
