import pytest
from sqlalchemy import create_engine, event, insert, select, update

from agromech_api.core.config import Settings
from agromech_api.db.enums import ChunkType, DocumentStatus
from agromech_api.db.models import document_chunks, documents, metadata, retrieval_logs
from agromech_api.domain.entities import process_document_entities
from agromech_api.rag.retrieval.hybrid import (
    RetrievalTraceConflictError,
    hybrid_retrieve,
    hybrid_retrieve_with_trace,
)
from agromech_api.rag.retrieval.filters import build_retrieval_filters
from agromech_api.rag.retrieval.fusion import RankedHit
from agromech_api.rag.retrieval.rerank import RerankError
from agromech_api.rag.retrieval.indexing import SearchIndexer
from agromech_api.rag.traces import record_citation_trace


def create_test_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    return engine


def seed_retrieval_corpus(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(documents),
            [
                {
                    "id": "doc-m7040",
                    "visibility": "public",
                    "title": "M7040 Manual",
                    "original_file_name": "m7040.txt",
                    "file_hash": "hash-m7040",
                    "file_size_bytes": 100,
                    "mime_type": "text/plain",
                    "storage_uri": "file:///tmp/m7040.txt",
                    "brand": "Kubota",
                    "model": "M7040",
                    "document_type": "repair_manual",
                    "language": "zh-CN",
                    "document_version": "2024",
                    "status": DocumentStatus.INDEXED.value,
                    "created_by_role": "admin",
                },
                {
                    "id": "doc-l3901",
                    "visibility": "public",
                    "title": "L3901 Manual",
                    "original_file_name": "l3901.txt",
                    "file_hash": "hash-l3901",
                    "file_size_bytes": 100,
                    "mime_type": "text/plain",
                    "storage_uri": "file:///tmp/l3901.txt",
                    "brand": "Kubota",
                    "model": "L3901",
                    "document_type": "operator_manual",
                    "language": "en-US",
                    "document_version": "2023",
                    "status": DocumentStatus.INDEXED.value,
                    "created_by_role": "admin",
                },
                {
                    "id": "doc-image",
                    "visibility": "public",
                    "title": "Image Observation",
                    "original_file_name": "warning.png",
                    "file_hash": "hash-image",
                    "file_size_bytes": 100,
                    "mime_type": "image/png",
                    "storage_uri": "file:///tmp/warning.png",
                    "brand": "Kubota",
                    "model": None,
                    "document_type": "visual_observation",
                    "language": "zh-CN",
                    "document_version": "2024",
                    "status": DocumentStatus.INDEXED.value,
                    "created_by_role": "admin",
                },
            ],
        )
        connection.execute(
            insert(document_chunks),
            [
                {
                    "id": "chunk-m7040",
                    "document_id": "doc-m7040",
                    "chunk_type": ChunkType.TEXT.value,
                    "content": "Kubota M7040 hydraulic pump fault code E01 check pump pressure.",
                    "summary": "M7040 E01 hydraulic pump",
                    "source_locator": {"type": "text", "line_start": 1, "line_end": 1},
                },
                {
                    "id": "chunk-l3901",
                    "document_id": "doc-l3901",
                    "chunk_type": ChunkType.TEXT.value,
                    "content": "Kubota L3901 fault code E01 electrical sensor troubleshooting.",
                    "summary": "L3901 E01 electrical sensor",
                    "source_locator": {"type": "text", "line_start": 1, "line_end": 1},
                },
                {
                    "id": "chunk-image",
                    "document_id": "doc-image",
                    "chunk_type": ChunkType.IMAGE.value,
                    "content": "Visual description: dashboard hydraulic warning light with E01.",
                    "summary": "dashboard hydraulic warning light",
                    "source_locator": {"type": "image", "source_file": "warning.png"},
                    "metadata": {"detected_entities": {"possible_models": ["M7040"], "warning_lights": ["hydraulic"]}},
                },
            ],
        )
    for document_id in ["doc-m7040", "doc-l3901", "doc-image"]:
        process_document_entities(engine, document_id)
        SearchIndexer(engine).index_document(document_id)


class FixedBm25Retriever:
    def search(self, _engine, _query, *, filters, limit):
        _ = filters, limit
        return [
            RankedHit(chunk_id="chunk-m7040", rank=1, score=8.0),
            RankedHit(chunk_id="chunk-l3901", rank=2, score=3.0),
        ]


def test_hybrid_retrieval_uses_dense_bm25_rrf_and_no_structured_channel(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)

    result = hybrid_retrieve_with_trace(
        engine,
        "M7040 E01 hydraulic pump",
        filters=build_retrieval_filters(request_filters={}, viewer_user_id=None),
        bm25_retriever=FixedBm25Retriever(),
        trace_id="trace-rrf",
    )

    first = result["candidates"][0]
    assert first["chunk_id"] == "chunk-m7040"
    assert set(first["channels"]) == {"bm25", "dense"}
    assert first["score"] == first["rrf_score"]
    assert first["channel_ranks"] == {"bm25": 1, "dense": 1}
    assert "structured" not in first["channels"]


def test_duplicate_round_one_trace_uses_atomic_insert_and_preserves_first_row(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    hybrid_retrieve_with_trace(
        engine,
        "M7040 E01 hydraulic pump",
        trace_id="trace-atomic-round-one",
        logged_query="first question",
        retrieval_round=1,
    )
    with engine.connect() as connection:
        before = dict(
            connection.execute(
                select(retrieval_logs).where(
                    retrieval_logs.c.trace_id == "trace-atomic-round-one"
                )
            ).mappings().one()
        )

    retrieval_log_statements: list[str] = []

    def capture_retrieval_log_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "retrieval_logs" in statement:
            retrieval_log_statements.append(statement.strip().upper())

    event.listen(engine, "before_cursor_execute", capture_retrieval_log_sql)
    try:
        with pytest.raises(Exception) as caught:
            hybrid_retrieve_with_trace(
                engine,
                "L3901 electrical sensor troubleshooting",
                trace_id="trace-atomic-round-one",
                logged_query="second question",
                retrieval_round=1,
            )
    finally:
        event.remove(engine, "before_cursor_execute", capture_retrieval_log_sql)

    assert caught.type.__name__ == "RetrievalTraceConflictError"
    assert retrieval_log_statements[0].startswith("INSERT INTO RETRIEVAL_LOGS")
    with engine.connect() as connection:
        after = dict(
            connection.execute(
                select(retrieval_logs).where(
                    retrieval_logs.c.trace_id == "trace-atomic-round-one"
                )
            ).mappings().one()
        )
    for field in ("query", "filters", "query_rewrite", "fusion", "candidates", "rerank", "final_evidence", "channels"):
        assert after[field] == before[field]


@pytest.mark.parametrize("retrieval_round", [0, 3])
def test_retrieval_trace_rejects_rounds_outside_controller_limit(
    tmp_path,
    retrieval_round: int,
) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)

    with pytest.raises(Exception) as caught:
        hybrid_retrieve_with_trace(
            engine,
            "M7040 E01 hydraulic pump",
            trace_id=f"trace-invalid-round-{retrieval_round}",
            retrieval_round=retrieval_round,
        )

    assert caught.type.__name__ == "RetrievalTraceConflictError"


def test_retrieval_trace_rejects_supplemental_round_without_initial_row(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)

    with pytest.raises(Exception) as caught:
        hybrid_retrieve_with_trace(
            engine,
            "M7040 E01 hydraulic pump",
            trace_id="trace-missing-round-one",
            retrieval_round=2,
        )

    assert caught.type.__name__ == "RetrievalTraceConflictError"


@pytest.mark.parametrize(
    ("second_query", "second_filters"),
    [
        ("different original question", {}),
        ("original question", {"model": "L3901"}),
    ],
)
def test_supplemental_round_requires_matching_original_query_and_filters(
    tmp_path,
    second_query: str,
    second_filters: dict[str, str],
) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    initial_filters = build_retrieval_filters(
        request_filters={},
        viewer_user_id=None,
    )
    hybrid_retrieve_with_trace(
        engine,
        "M7040 E01 hydraulic pump",
        trace_id="trace-supplemental-identity",
        logged_query="original question",
        filters=initial_filters,
        retrieval_round=1,
    )
    before = retrieval_log_snapshot(engine, "trace-supplemental-identity")

    with pytest.raises(RetrievalTraceConflictError):
        hybrid_retrieve_with_trace(
            engine,
            "M7040 E01 hydraulic pump repair",
            trace_id="trace-supplemental-identity",
            logged_query=second_query,
            filters=build_retrieval_filters(
                request_filters=second_filters,
                viewer_user_id=None,
            ),
            retrieval_round=2,
        )

    assert retrieval_log_snapshot(engine, "trace-supplemental-identity") == before


def test_supplemental_round_rejects_completed_citation_trace(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    hybrid_retrieve_with_trace(
        engine,
        "M7040 E01 hydraulic pump",
        trace_id="trace-completed-citation",
        logged_query="original question",
        retrieval_round=1,
    )
    with engine.begin() as connection:
        row = connection.execute(
            select(retrieval_logs.c.channels).where(
                retrieval_logs.c.trace_id == "trace-completed-citation"
            )
        ).mappings().one()
        channels = dict(row["channels"])
        channels["citation"] = {
            "status": "ok",
            "count": 1,
            "chunk_ids": ["chunk-m7040"],
            "asset_ids": [],
        }
        connection.execute(
            update(retrieval_logs)
            .where(retrieval_logs.c.trace_id == "trace-completed-citation")
            .values(channels=channels)
        )
    before = retrieval_log_snapshot(engine, "trace-completed-citation")

    with pytest.raises(RetrievalTraceConflictError):
        hybrid_retrieve_with_trace(
            engine,
            "M7040 E01 hydraulic pump repair",
            trace_id="trace-completed-citation",
            logged_query="original question",
            retrieval_round=2,
        )

    assert retrieval_log_snapshot(engine, "trace-completed-citation") == before


def test_supplemental_round_does_not_overwrite_citation_written_before_cas_update(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    trace_id = "trace-citation-before-round-two-cas"
    hybrid_retrieve_with_trace(
        engine,
        "orchard sprayer calibration nozzle",
        trace_id=trace_id,
        logged_query="original question",
        retrieval_round=1,
    )
    before = retrieval_log_snapshot(engine, trace_id)
    citation_written = False

    def write_citation_before_round_two_update(_conn, _cursor, statement, _parameters, _context, _executemany):
        nonlocal citation_written
        if citation_written or not statement.lstrip().upper().startswith("UPDATE RETRIEVAL_LOGS"):
            return
        citation_written = True
        record_citation_trace(
            engine,
            trace_id,
            [{"chunk_id": "chunk-m7040", "document_id": "doc-m7040"}],
        )

    event.listen(engine, "before_cursor_execute", write_citation_before_round_two_update)
    try:
        with pytest.raises(RetrievalTraceConflictError):
            hybrid_retrieve_with_trace(
                engine,
                "M7040 E01 hydraulic pump repair",
                trace_id=trace_id,
                logged_query="original question",
                retrieval_round=2,
            )
    finally:
        event.remove(engine, "before_cursor_execute", write_citation_before_round_two_update)

    assert citation_written is True
    with engine.connect() as connection:
        citation_status = connection.execute(
            select(retrieval_logs.c.citation_status).where(retrieval_logs.c.trace_id == trace_id)
        ).scalar_one()
    assert citation_status == "completed"
    after = retrieval_log_snapshot(engine, trace_id)
    assert after == {
        **before,
        "channels": {
            **before["channels"],
            "citation": {
                "status": "ok",
                "count": 1,
                "chunk_ids": ["chunk-m7040"],
                "asset_ids": [],
            },
        },
    }


def test_record_citation_trace_leaves_historical_citation_channel_untouched(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    trace_id = "trace-historical-citation-channel"
    hybrid_retrieve_with_trace(
        engine,
        "M7040 E01 hydraulic pump",
        trace_id=trace_id,
        logged_query="original question",
        retrieval_round=1,
    )
    with engine.begin() as connection:
        row = connection.execute(
            select(retrieval_logs.c.channels).where(retrieval_logs.c.trace_id == trace_id)
        ).mappings().one()
        channels = {
            **dict(row["channels"]),
            "citation": {
                "status": "ok",
                "count": 1,
                "chunk_ids": ["chunk-m7040"],
                "asset_ids": [],
            },
        }
        connection.execute(
            update(retrieval_logs)
            .where(retrieval_logs.c.trace_id == trace_id)
            .values(channels=channels, citation_status="pending")
        )
    before = retrieval_log_snapshot(engine, trace_id)

    record_citation_trace(engine, trace_id, [])

    assert retrieval_log_snapshot(engine, trace_id) == before
    with engine.connect() as connection:
        assert connection.execute(
            select(retrieval_logs.c.citation_status).where(retrieval_logs.c.trace_id == trace_id)
        ).scalar_one() == "pending"


def test_record_citation_trace_raises_conflict_when_round_two_wins_cas(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    trace_id = "trace-citation-loses-to-round-two"
    hybrid_retrieve_with_trace(
        engine,
        "orchard sprayer calibration nozzle",
        trace_id=trace_id,
        logged_query="original question",
        retrieval_round=1,
    )
    round_two_snapshot: dict[str, object] | None = None
    round_two_completed = False

    def complete_round_two_before_citation_update(
        _conn,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        nonlocal round_two_completed, round_two_snapshot
        if round_two_completed or not statement.lstrip().upper().startswith("UPDATE RETRIEVAL_LOGS"):
            return
        round_two_completed = True
        hybrid_retrieve_with_trace(
            engine,
            "M7040 E01 hydraulic pump repair",
            trace_id=trace_id,
            logged_query="original question",
            retrieval_round=2,
        )
        round_two_snapshot = retrieval_log_snapshot(engine, trace_id)

    event.listen(engine, "before_cursor_execute", complete_round_two_before_citation_update)
    try:
        with pytest.raises(RetrievalTraceConflictError):
            record_citation_trace(
                engine,
                trace_id,
                [{"chunk_id": "chunk-m7040", "document_id": "doc-m7040"}],
            )
    finally:
        event.remove(engine, "before_cursor_execute", complete_round_two_before_citation_update)

    assert round_two_completed is True
    assert round_two_snapshot is not None
    assert retrieval_log_snapshot(engine, trace_id) == round_two_snapshot
    with engine.connect() as connection:
        row = connection.execute(
            select(retrieval_logs.c.retrieval_round, retrieval_logs.c.citation_status, retrieval_logs.c.channels)
            .where(retrieval_logs.c.trace_id == trace_id)
        ).mappings().one()
    assert row["retrieval_round"] == 2
    assert row["citation_status"] == "pending"
    assert "citation" not in row["channels"]


def test_supplemental_round_rejects_a_second_round_two_and_preserves_final_audit(
    tmp_path,
) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    hybrid_retrieve_with_trace(
        engine,
        "orchard sprayer calibration nozzle",
        trace_id="trace-one-supplement-only",
        logged_query="original question",
        retrieval_round=1,
    )
    hybrid_retrieve_with_trace(
        engine,
        "M7040 E01 hydraulic pump repair",
        trace_id="trace-one-supplement-only",
        logged_query="original question",
        retrieval_round=2,
    )
    before = retrieval_log_snapshot(engine, "trace-one-supplement-only")

    with pytest.raises(RetrievalTraceConflictError):
        hybrid_retrieve_with_trace(
            engine,
            "M7040 E01 hydraulic pump repair manual",
            trace_id="trace-one-supplement-only",
            logged_query="original question",
            retrieval_round=2,
        )

    assert retrieval_log_snapshot(engine, "trace-one-supplement-only") == before


def test_supplemental_round_maps_zero_row_atomic_update_to_conflict(
    tmp_path,
) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    hybrid_retrieve_with_trace(
        engine,
        "orchard sprayer calibration nozzle",
        trace_id="trace-lost-atomic-guard",
        logged_query="original question",
        retrieval_round=1,
    )
    before = retrieval_log_snapshot(engine, "trace-lost-atomic-guard")

    def force_guard_miss(_conn, _cursor, statement, parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("UPDATE RETRIEVAL_LOGS"):
            return f"{statement} AND 0 = 1", parameters
        return statement, parameters

    event.listen(engine, "before_cursor_execute", force_guard_miss, retval=True)
    try:
        with pytest.raises(RetrievalTraceConflictError):
            hybrid_retrieve_with_trace(
                engine,
                "M7040 E01 hydraulic pump repair",
                trace_id="trace-lost-atomic-guard",
                logged_query="original question",
                retrieval_round=2,
            )
    finally:
        event.remove(engine, "before_cursor_execute", force_guard_miss)

    assert retrieval_log_snapshot(engine, "trace-lost-atomic-guard") == before


def retrieval_log_snapshot(engine, trace_id: str) -> dict[str, object]:
    with engine.connect() as connection:
        row = connection.execute(
            select(retrieval_logs).where(retrieval_logs.c.trace_id == trace_id)
        ).mappings().one()
    return {
        field: row[field]
        for field in (
            "query",
            "filters",
            "query_rewrite",
            "fusion",
            "candidates",
            "rerank",
            "final_evidence",
            "channels",
        )
    }


def test_bm25_failure_degrades_to_dense_only(tmp_path) -> None:
    class FailingBm25Retriever:
        def search(self, *_args, **_kwargs):
            raise RuntimeError("bm25 unavailable")

    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    result = hybrid_retrieve_with_trace(
        engine,
        "dashboard hydraulic warning",
        filters=build_retrieval_filters(request_filters={}, viewer_user_id=None),
        bm25_retriever=FailingBm25Retriever(),
        trace_id="trace-bm25-degraded",
    )

    assert result["status"] == "ok"
    assert result["candidates"][0]["channels"] == ["dense"]
    assert result["candidates"][0]["channel_scores"]["dense"] > 0.25
    with engine.connect() as connection:
        log = connection.execute(select(retrieval_logs).where(retrieval_logs.c.trace_id == "trace-bm25-degraded")).mappings().one()
    assert {"channel": "bm25", "reason": "bm25_degraded"} in log["channels"]["degraded"]


def test_low_similarity_dense_only_query_returns_evidence_insufficient(tmp_path) -> None:
    class EmptyBm25Retriever:
        def search(self, *_args, **_kwargs):
            return []

    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)

    result = hybrid_retrieve_with_trace(
        engine,
        "orchard sprayer calibration nozzle",
        filters=build_retrieval_filters(request_filters={}, viewer_user_id=None),
        bm25_retriever=EmptyBm25Retriever(),
        trace_id="trace-low-similarity-dense-only",
    )

    assert result["status"] == "evidence_insufficient"
    assert result["final_evidence"] == []


def test_low_similarity_dense_hit_keeps_dense_channel_when_bm25_matches(
    tmp_path,
    monkeypatch,
) -> None:
    class MatchingBm25Retriever:
        def search(self, _engine, _query, *, filters, limit):
            _ = filters, limit
            return [RankedHit(chunk_id="chunk-m7040", rank=1, score=8.0)]

    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    monkeypatch.setattr(
        "agromech_api.rag.retrieval.hybrid.vector_search",
        lambda *_args, **_kwargs: [
            {
                "chunk_id": "chunk-image",
                "score": 0.9,
                "vector_ref": "pgvector://chunk_vector_embeddings/high-score",
                "embedding_id": "high-score",
            },
            {
                "chunk_id": "chunk-m7040",
                "score": 0.1,
                "vector_ref": "pgvector://chunk_vector_embeddings/low-score",
                "embedding_id": "low-score",
            },
            {
                "chunk_id": "chunk-l3901",
                "score": 0.08,
                "vector_ref": "pgvector://chunk_vector_embeddings/unrelated-low-score",
                "embedding_id": "unrelated-low-score",
            },
        ],
    )

    result = hybrid_retrieve_with_trace(
        engine,
        "M7040 E01 hydraulic pump",
        filters=build_retrieval_filters(request_filters={}, viewer_user_id=None),
        bm25_retriever=MatchingBm25Retriever(),
        trace_id="trace-bm25-survives-dense-filter",
    )

    assert result["status"] == "ok"
    assert result["candidates"][0]["chunk_id"] == "chunk-m7040"
    assert set(result["candidates"][0]["channels"]) == {"bm25", "dense"}
    assert result["candidates"][0]["channel_scores"]["dense"] == 0.1
    assert all(candidate["chunk_id"] != "chunk-l3901" for candidate in result["candidates"])


def test_dense_only_similarity_threshold_is_configurable_and_traced(tmp_path) -> None:
    class EmptyBm25Retriever:
        def search(self, *_args, **_kwargs):
            return []

    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    settings = Settings(_env_file=None, dense_only_min_similarity=0.75)

    result = hybrid_retrieve_with_trace(
        engine,
        "dashboard hydraulic warning",
        filters=build_retrieval_filters(request_filters={}, viewer_user_id=None),
        bm25_retriever=EmptyBm25Retriever(),
        trace_id="trace-configurable-dense-only-threshold",
        settings=settings,
    )

    assert result["status"] == "evidence_insufficient"
    with engine.connect() as connection:
        log = connection.execute(
            select(retrieval_logs).where(
                retrieval_logs.c.trace_id == "trace-configurable-dense-only-threshold"
            )
        ).mappings().one()
    assert log["model_config"]["dense_only_min_similarity"] == 0.75


def test_dense_failure_degrades_to_bm25_only(tmp_path) -> None:
    class FailingEmbeddingProvider:
        provider = "test"
        model = "failing"

        def embed(self, _query):
            raise RuntimeError("dense unavailable")

    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    result = hybrid_retrieve_with_trace(
        engine,
        "M7040 E01 hydraulic pump",
        filters=build_retrieval_filters(request_filters={}, viewer_user_id=None),
        embedding_provider=FailingEmbeddingProvider(),
        bm25_retriever=FixedBm25Retriever(),
        trace_id="trace-dense-degraded",
    )

    assert result["status"] == "ok"
    assert result["candidates"][0]["channels"] == ["bm25"]
    with engine.connect() as connection:
        log = connection.execute(select(retrieval_logs).where(retrieval_logs.c.trace_id == "trace-dense-degraded")).mappings().one()
    assert {"channel": "dense", "reason": "dense_degraded"} in log["channels"]["degraded"]


def test_both_retrieval_channels_failing_returns_evidence_insufficient(tmp_path) -> None:
    class FailingEmbeddingProvider:
        def embed(self, _query):
            raise RuntimeError("dense unavailable")

    class FailingBm25Retriever:
        def search(self, *_args, **_kwargs):
            raise RuntimeError("bm25 unavailable")

    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    result = hybrid_retrieve_with_trace(
        engine,
        "M7040 E01 hydraulic pump",
        filters=build_retrieval_filters(request_filters={}, viewer_user_id=None),
        embedding_provider=FailingEmbeddingProvider(),
        bm25_retriever=FailingBm25Retriever(),
        trace_id="trace-all-retrieval-degraded",
    )

    assert result["status"] == "evidence_insufficient"
    assert result["final_evidence"] == []


def test_bm25_receives_same_filters_instance_and_configured_top_k(tmp_path) -> None:
    class SpyBm25Retriever:
        filters = None
        limit = None

        def search(self, _engine, _query, *, filters, limit):
            self.filters = filters
            self.limit = limit
            return [RankedHit(chunk_id="chunk-m7040", rank=1, score=8.0)]

    class FailingEmbeddingProvider:
        def embed(self, _query):
            raise RuntimeError("dense unavailable")

    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    filters = build_retrieval_filters(request_filters={"model": "M7040"}, viewer_user_id=None)
    settings = Settings(
        _env_file=None,
        dense_top_k=8,
        bm25_top_k=7,
        fusion_top_k=10,
        rerank_top_k=10,
    )
    retriever = SpyBm25Retriever()

    hybrid_retrieve_with_trace(
        engine,
        "E01 repair",
        filters=filters,
        embedding_provider=FailingEmbeddingProvider(),
        bm25_retriever=retriever,
        settings=settings,
        trace_id="trace-bm25-filter-contract",
    )

    assert retriever.filters is filters
    assert retriever.limit == settings.bm25_top_k


def test_fusion_fail_closed_rejects_provider_hits_outside_explicit_filter(tmp_path) -> None:
    class FailingEmbeddingProvider:
        def embed(self, _query):
            raise RuntimeError("dense unavailable")

    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    filters = build_retrieval_filters(request_filters={"model": "M7040"}, viewer_user_id=None)

    result = hybrid_retrieve_with_trace(
        engine,
        "E01 repair",
        filters=filters,
        embedding_provider=FailingEmbeddingProvider(),
        bm25_retriever=FixedBm25Retriever(),
        trace_id="trace-model-filter-fail-closed",
    )

    assert {candidate["document_id"] for candidate in result["candidates"]} == {"doc-m7040"}


def test_model_applicability_loads_candidate_models_in_one_query(tmp_path) -> None:
    class FailingEmbeddingProvider:
        def embed(self, _query):
            raise RuntimeError("dense unavailable")

    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    applicability_selects = 0

    def count_applicability_selects(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        nonlocal applicability_selects
        normalized = statement.lstrip().lower()
        if normalized.startswith("select") and "chunk_entity_links" in normalized:
            applicability_selects += 1

    event.listen(engine, "before_cursor_execute", count_applicability_selects)
    try:
        result = hybrid_retrieve(
            engine,
            "M7040 E01 repair",
            embedding_provider=FailingEmbeddingProvider(),
            bm25_retriever=FixedBm25Retriever(),
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_applicability_selects)

    assert [candidate["chunk_id"] for candidate in result["candidates"]] == [
        "chunk-m7040",
        "chunk-l3901",
    ]
    assert result["candidates"][1]["not_applicable"] is True
    assert applicability_selects == 1


def test_hybrid_retrieval_marks_unrelated_model_candidates_not_applicable(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)

    result = hybrid_retrieve(engine, "M7040 E01 repair")

    first = result["candidates"][0]
    unrelated = next(candidate for candidate in result["candidates"] if candidate["chunk_id"] == "chunk-l3901")
    assert first["chunk_id"] == "chunk-m7040"
    assert unrelated["not_applicable"] is True
    assert unrelated["applicability_reason"] == "model_mismatch"
    assert unrelated["score"] == unrelated["rrf_score"]
    assert unrelated["rerank_score"] < first["rerank_score"]


def test_hybrid_retrieval_can_use_pgvector_candidates(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)

    result = hybrid_retrieve(
        engine,
        "dashboard hydraulic warning",
    )

    image_candidate = next(candidate for candidate in result["candidates"] if candidate["chunk_id"] == "chunk-image")
    assert "dense" in image_candidate["channels"]
    assert image_candidate["vector_ref"].startswith("pgvector://chunk_vector_embeddings/")


class FailingGraphSearchService:
    def expand(self, **_kwargs):
        raise RuntimeError("neo4j unavailable")


def test_hybrid_retrieval_ignores_graph_service_when_graph_is_disabled(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)

    result = hybrid_retrieve_with_trace(
        engine,
        "M7040 E01 hydraulic pump",
        trace_id="trace-graph-degraded",
    )

    assert result["status"] == "ok"
    with engine.connect() as connection:
        log = connection.execute(
            select(retrieval_logs).where(retrieval_logs.c.trace_id == "trace-graph-degraded")
        ).mappings().one()
    assert log["channels"]["degraded"] == []


class UnsourcedGraphSearchService:
    def expand(self, **_kwargs):
        return [
            {
                "entity_type": "fault_code",
                "entity_value": "E01",
                "hop_count": 1,
                "source_document_id": "doc-m7040",
                "source_chunk_id": None,
                "relationship_type": "co_occurs:model:fault_code",
                "confidence": 0.9,
                "channel": "graph",
                "final_answer_eligible": False,
            }
        ]


def test_hybrid_retrieval_ignores_graph_candidates_without_source_chunk(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)

    result = hybrid_retrieve(engine, "M7040 E01 hydraulic pump")

    assert all("graph" not in candidate["channels"] for candidate in result["candidates"])


class ReverseRerankProvider:
    def rerank(self, _query: str, documents: list[str]) -> list[float]:
        return [float(index) for index in range(len(documents), 0, -1)]


class FailingRerankProvider:
    def rerank(self, _query: str, _documents: list[str]) -> list[float]:
        raise RerankError("service timeout")


def test_hybrid_retrieval_uses_model_rerank_provider_and_records_trace(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)

    result = hybrid_retrieve_with_trace(
        engine,
        "M7040 E01 hydraulic pump",
        trace_id="trace-model-rerank",
        rerank_provider=ReverseRerankProvider(),
        rerank_top_k=3,
    )

    assert result["status"] == "ok"
    assert result["candidates"][0]["chunk_id"] == "chunk-m7040"
    assert result["candidates"][0]["rerank_score"] == 3.0

    with engine.connect() as connection:
        log = connection.execute(
            select(retrieval_logs).where(retrieval_logs.c.trace_id == "trace-model-rerank")
        ).mappings().one()
    assert log["rerank"]["strategy"] == "bailian_model_rerank"
    assert log["rerank"]["fallback"] is False
    assert log["channels"]["degraded"] == []


def test_hybrid_retrieval_falls_back_when_model_rerank_fails(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)

    result = hybrid_retrieve_with_trace(
        engine,
        "M7040 E01 hydraulic pump",
        trace_id="trace-rerank-degraded",
        rerank_provider=FailingRerankProvider(),
        rerank_top_k=3,
    )

    assert result["status"] == "ok"
    assert result["candidates"][0]["chunk_id"] == "chunk-m7040"

    with engine.connect() as connection:
        log = connection.execute(
            select(retrieval_logs).where(retrieval_logs.c.trace_id == "trace-rerank-degraded")
        ).mappings().one()
    assert {"channel": "rerank", "reason": "rerank_degraded"} in log["channels"]["degraded"]
    assert log["rerank"]["strategy"] == "deterministic_evidence_rerank"


def test_deterministic_rerank_fallback_prioritizes_model_fault_code_source_and_text_relevance(tmp_path) -> None:
    class TwoCandidateBm25Retriever:
        def search(self, _engine, _query, *, filters, limit):
            _ = filters, limit
            return [
                RankedHit(chunk_id="chunk-high", rank=1, score=8.0),
                RankedHit(chunk_id="chunk-low", rank=2, score=3.0),
            ]

    engine = create_test_engine(tmp_path)
    with engine.begin() as connection:
        connection.execute(
            insert(documents),
            [
                {
                    "id": "doc-high",
                    "visibility": "public",
                    "title": "M7040 Official Repair Manual",
                    "original_file_name": "m7040-official.txt",
                    "file_hash": "hash-high",
                    "file_size_bytes": 100,
                    "mime_type": "text/plain",
                    "storage_uri": "file:///tmp/m7040-official.txt",
                    "brand": "Kubota",
                    "model": "M7040",
                    "document_type": "repair_manual",
                    "language": "zh-CN",
                    "document_version": "2024",
                    "source": "manual",
                    "status": DocumentStatus.INDEXED.value,
                    "created_by_role": "admin",
                },
                {
                    "id": "doc-low",
                    "visibility": "public",
                    "title": "General Visual Note",
                    "original_file_name": "general-visual.txt",
                    "file_hash": "hash-low",
                    "file_size_bytes": 100,
                    "mime_type": "text/plain",
                    "storage_uri": "file:///tmp/general-visual.txt",
                    "brand": "Kubota",
                    "model": "M7040",
                    "document_type": "visual_observation",
                    "language": "zh-CN",
                    "document_version": "2024",
                    "source": "field",
                    "status": DocumentStatus.INDEXED.value,
                    "created_by_role": "admin",
                },
            ],
        )
        connection.execute(
            insert(document_chunks),
            [
                {
                    "id": "chunk-high",
                    "document_id": "doc-high",
                    "chunk_type": ChunkType.TEXT.value,
                    "content": "Kubota M7040 fault code E01 hydraulic pump pressure inspection steps.",
                    "summary": "M7040 E01 hydraulic pump",
                    "source_locator": {"type": "text", "line_start": 1, "line_end": 1},
                },
                {
                    "id": "chunk-low",
                    "document_id": "doc-low",
                    "chunk_type": ChunkType.IMAGE.value,
                    "content": "General tractor warning photo without model or fault code detail.",
                    "summary": "generic warning",
                    "source_locator": {"type": "image", "source_file": "general-warning.png"},
                },
            ],
        )

    for document_id in ["doc-high", "doc-low"]:
        process_document_entities(engine, document_id)
        SearchIndexer(engine).index_document(document_id)

    result = hybrid_retrieve_with_trace(
        engine,
        "M7040 E01 hydraulic pump",
        trace_id="trace-deterministic-fallback",
        bm25_retriever=TwoCandidateBm25Retriever(),
        rerank_provider=FailingRerankProvider(),
        rerank_top_k=5,
    )

    assert result["status"] == "ok"
    assert result["candidates"][0]["chunk_id"] == "chunk-high"
    assert result["candidates"][0]["rerank_score"] > result["candidates"][1]["rerank_score"]

    with engine.connect() as connection:
        log = connection.execute(
            select(retrieval_logs).where(retrieval_logs.c.trace_id == "trace-deterministic-fallback")
        ).mappings().one()
    first_item = log["rerank"]["items"][0]
    assert log["rerank"]["fallback"] is True
    assert first_item["chunk_id"] == "chunk-high"
    assert {
        "model_match",
        "fault_code_match",
        "source_credibility",
        "text_relevance",
    }.issubset(first_item["factors"])


def test_hybrid_retrieval_returns_evidence_insufficient_when_no_candidates(tmp_path) -> None:
    class EmptyEmbeddingProvider:
        def embed(self, _query):
            return [0.0] * 1024

    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)

    result = hybrid_retrieve(
        engine,
        "orchard sprayer calibration nozzle",
        embedding_provider=EmptyEmbeddingProvider(),
    )

    assert result == {
        "status": "evidence_insufficient",
        "candidates": [],
        "message": "No evidence found for the query",
    }


def seed_private_document(engine, *, owner_user_id: str) -> None:
    # 一份私有文档，仅归属者本人可检索。内容与公用 M7040 语料同主题，
    # 确保若可见性过滤失效，它必然会命中同一查询并暴露出来。
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id="doc-private",
                title="Private M7040 Notes",
                original_file_name="private-m7040.txt",
                file_hash="hash-private",
                file_size_bytes=100,
                mime_type="text/plain",
                storage_uri="file:///tmp/private-m7040.txt",
                brand="Kubota",
                model="M7040",
                document_type="repair_manual",
                language="zh-CN",
                status=DocumentStatus.INDEXED.value,
                created_by_role="user",
                owner_user_id=owner_user_id,
                visibility="private",
            )
        )
        connection.execute(
            insert(document_chunks).values(
                id="chunk-private",
                document_id="doc-private",
                chunk_type=ChunkType.TEXT.value,
                content="Kubota M7040 hydraulic pump fault code E01 private owner notes.",
                summary="M7040 E01 private notes",
                source_locator={"type": "text", "line_start": 1, "line_end": 1},
            )
        )
    process_document_entities(engine, "doc-private")
    SearchIndexer(engine).index_document("doc-private")


def candidate_document_ids(result: dict[str, object]) -> set[str]:
    return {candidate["document_id"] for candidate in result.get("candidates", [])}


def test_hybrid_retrieval_hides_private_document_from_anonymous_viewer(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    seed_private_document(engine, owner_user_id="owner-1")

    filters = build_retrieval_filters(request_filters={}, viewer_user_id=None)
    result = hybrid_retrieve_with_trace(engine, "M7040 E01 hydraulic pump", filters=filters)

    document_ids = candidate_document_ids(result)
    assert "doc-m7040" in document_ids
    assert "doc-private" not in document_ids


def test_hybrid_retrieval_hides_private_document_from_non_owner(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    seed_private_document(engine, owner_user_id="owner-1")

    filters = build_retrieval_filters(request_filters={}, viewer_user_id="intruder-2")
    result = hybrid_retrieve_with_trace(engine, "M7040 E01 hydraulic pump", filters=filters)

    document_ids = candidate_document_ids(result)
    assert "doc-m7040" in document_ids
    assert "doc-private" not in document_ids


def test_hybrid_retrieval_returns_private_document_to_its_owner(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    seed_private_document(engine, owner_user_id="owner-1")

    filters = build_retrieval_filters(request_filters={}, viewer_user_id="owner-1")
    result = hybrid_retrieve_with_trace(engine, "M7040 E01 hydraulic pump", filters=filters)

    document_ids = candidate_document_ids(result)
    assert "doc-private" in document_ids
    assert "doc-m7040" in document_ids
