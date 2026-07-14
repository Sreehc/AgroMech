from sqlalchemy import create_engine, insert, select

from agromech_api.db.enums import ChunkType, DocumentStatus
from agromech_api.db.models import document_chunks, documents, metadata, retrieval_logs
from agromech_api.domain.entities import process_document_entities
from agromech_api.rag.retrieval.hybrid import (
    hybrid_retrieve,
    hybrid_retrieve_with_trace,
)
from agromech_api.rag.retrieval.filters import build_retrieval_filters
from agromech_api.rag.retrieval.fusion import RankedHit
from agromech_api.rag.retrieval.rerank import RerankError
from agromech_api.rag.retrieval.indexing import SearchIndexer


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
    with engine.connect() as connection:
        log = connection.execute(select(retrieval_logs).where(retrieval_logs.c.trace_id == "trace-bm25-degraded")).mappings().one()
    assert {"channel": "bm25", "reason": "bm25_degraded"} in log["channels"]["degraded"]


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


def test_dense_and_bm25_share_explicit_model_filter_before_top_k(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    filters = build_retrieval_filters(request_filters={"model": "M7040"}, viewer_user_id=None)

    result = hybrid_retrieve_with_trace(
        engine,
        "E01 repair",
        filters=filters,
        bm25_retriever=FixedBm25Retriever(),
        trace_id="trace-model-filter",
    )

    assert {candidate["document_id"] for candidate in result["candidates"]} == {"doc-m7040"}


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
