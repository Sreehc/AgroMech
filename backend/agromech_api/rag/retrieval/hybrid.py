from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from sqlalchemy import Engine, select, update
from sqlalchemy.exc import IntegrityError

from agromech_api.core.config import get_settings
from agromech_api.db.enums import ChunkType
from agromech_api.db.models import chunk_entity_links, document_chunks, documents, retrieval_logs
from agromech_api.domain.entities import normalize
from agromech_api.rag.retrieval.bm25 import Bm25Retriever, build_bm25_retriever
from agromech_api.rag.retrieval.filters import (
    RetrievalFilters,
    build_retrieval_filters,
    chunk_filter_conditions,
    document_filter_conditions,
)
from agromech_api.rag.retrieval.fusion import FusedHit, RankedHit, rrf_fuse
from agromech_api.rag.retrieval.indexing import vector_search
from agromech_api.rag.retrieval.query_understanding import ParsedQuery, parse_query
from agromech_api.rag.retrieval.rerank import RerankError


# Citation remains intentionally short, while evaluation needs a deeper rank list.
EVALUATION_RERANK_TRACE_LIMIT = 20


class RetrievalTraceConflictError(RuntimeError):
    """Raised when a retrieval round cannot safely mutate the requested trace."""


class DenseRetrievalAgent:
    channel = "dense"

    def run(
        self,
        engine: Engine,
        query: str,
        *,
        filters: RetrievalFilters,
        limit: int,
        embedding_provider=None,
    ) -> list[RankedHit]:
        results = vector_search(
            engine,
            query,
            filters=filters,
            limit=limit,
            embedding_provider=embedding_provider,
        )
        return [
            RankedHit(
                chunk_id=str(result["chunk_id"]),
                rank=rank,
                score=float(result["score"]),
                vector_ref=str(result["vector_ref"]) if result.get("vector_ref") else None,
                embedding_id=str(result["embedding_id"]) if result.get("embedding_id") else None,
            )
            for rank, result in enumerate(results, start=1)
        ]


class Bm25RetrievalAgent:
    channel = "bm25"

    def run(
        self,
        engine: Engine,
        query: str,
        *,
        filters: RetrievalFilters,
        limit: int,
        retriever: Bm25Retriever,
    ) -> list[RankedHit]:
        return retriever.search(engine, query, filters=filters, limit=limit)


class RerankAgent:
    def run(
        self,
        candidates: list[dict[str, object]],
        parsed: ParsedQuery,
        *,
        limit: int,
        query: str | None = None,
        rerank_provider=None,
        degraded_channels: dict[str, str] | None = None,
        rerank_top_k: int | None = None,
    ) -> tuple[list[dict[str, object]], dict[str, object]]:
        return rerank_candidates(
            candidates,
            parsed,
            limit=limit,
            query=query,
            rerank_provider=rerank_provider,
            degraded_channels=degraded_channels,
            rerank_top_k=rerank_top_k,
        )


def hybrid_retrieve(
    engine: Engine,
    query: str,
    *,
    limit: int = 10,
    filters: RetrievalFilters | None = None,
    query_rewrite: dict[str, object] | None = None,
    degraded_channels: dict[str, str] | None = None,
    embedding_provider=None,
    bm25_retriever: Bm25Retriever | None = None,
    rerank_provider=None,
    rerank_top_k: int | None = None,
    settings=None,
) -> dict[str, object]:
    _ = query_rewrite
    settings = settings or get_settings()
    filters = filters or build_retrieval_filters(request_filters={}, viewer_user_id=None)
    degraded_channels = dict(degraded_channels or {})
    _, reranked, _, _ = retrieve_candidates(
        engine,
        query,
        filters=filters,
        final_limit=limit,
        embedding_provider=embedding_provider,
        bm25_retriever=bm25_retriever,
        rerank_provider=rerank_provider,
        rerank_top_k=rerank_top_k,
        degraded_channels=degraded_channels,
        settings=settings,
    )
    if not reranked:
        return evidence_insufficient()
    return {"status": "ok", "candidates": reranked}


def hybrid_retrieve_with_trace(
    engine: Engine,
    query: str,
    *,
    trace_id: str | None = None,
    limit: int | None = None,
    logged_query: str | None = None,
    filters: RetrievalFilters | None = None,
    query_rewrite: dict[str, object] | None = None,
    retrieval_round: int = 1,
    degraded_channels: dict[str, str] | None = None,
    embedding_provider=None,
    bm25_retriever: Bm25Retriever | None = None,
    rerank_provider=None,
    rerank_top_k: int | None = None,
    settings=None,
) -> dict[str, object]:
    started = time.perf_counter()
    validate_retrieval_round(retrieval_round)
    settings = settings or get_settings()
    filters = filters or build_retrieval_filters(request_filters={}, viewer_user_id=None)
    query_rewrite = dict(query_rewrite or {})
    final_limit = limit or settings.final_evidence_limit
    trace_rerank_limit = max(final_limit, EVALUATION_RERANK_TRACE_LIMIT)
    trace_id = trace_id or str(uuid4())
    degraded_channels = dict(degraded_channels or {})
    candidates, reranked, fusion_trace, rerank_trace = retrieve_candidates(
        engine,
        query,
        filters=filters,
        final_limit=trace_rerank_limit,
        embedding_provider=embedding_provider,
        bm25_retriever=bm25_retriever,
        rerank_provider=rerank_provider,
        rerank_top_k=max(rerank_top_k or settings.rerank_top_k, trace_rerank_limit),
        degraded_channels=degraded_channels,
        settings=settings,
    )
    status = "ok" if reranked else "evidence_insufficient"
    final_candidates = reranked[:final_limit]
    final_evidence = evidence_payload(final_candidates)
    channels = channel_trace(candidates, degraded_channels, settings=settings)
    fusion_trace["retrieval_duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
    write_retrieval_log(
        engine,
        trace_id=trace_id,
        query=logged_query or query,
        filters=filters.as_trace(),
        channels=channels,
        model_config=trace_model_config(settings),
        query_rewrite=query_rewrite,
        retrieval_round=retrieval_round,
        fusion=fusion_trace,
        candidates=[trace_candidate(candidate) for candidate in candidates],
        rerank=rerank_trace,
        final_evidence=final_evidence,
    )
    if not reranked:
        return {**evidence_insufficient(), "trace_id": trace_id, "final_evidence": []}
    return {
        "status": status,
        "trace_id": trace_id,
        "candidates": final_candidates,
        "final_evidence": final_evidence,
    }


def retrieve_candidates(
    engine: Engine,
    query: str,
    *,
    filters: RetrievalFilters,
    final_limit: int,
    embedding_provider=None,
    bm25_retriever: Bm25Retriever | None = None,
    rerank_provider=None,
    rerank_top_k: int | None = None,
    degraded_channels: dict[str, str],
    settings=None,
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    dict[str, object],
    dict[str, object],
]:
    settings = settings or get_settings()
    parsed = parse_query(query, engine=engine)
    channel_hits, channel_status = collect_ranked_hits(
        engine,
        query,
        filters=filters,
        dense_top_k=settings.dense_top_k,
        bm25_top_k=settings.bm25_top_k,
        embedding_provider=embedding_provider,
        bm25_retriever=bm25_retriever or build_bm25_retriever(engine),
    )
    for channel, status in channel_status.items():
        if status.endswith("_degraded"):
            degraded_channels[channel] = status
    channel_hits = filter_low_similarity_dense_only_hits(
        channel_hits,
        min_similarity=settings.dense_only_min_similarity,
    )
    fused, fusion_trace = rrf_fuse(
        channel_hits,
        rrf_k=settings.rrf_k,
        weights={
            "dense": settings.rrf_dense_weight,
            "bm25": settings.rrf_bm25_weight,
        },
        limit=settings.fusion_top_k,
    )
    candidates = hydrate_fused_candidates(engine, fused)
    candidates = enforce_retrieval_filters(engine, candidates, filters=filters)
    apply_model_applicability(engine, candidates, parsed)
    reranked, rerank_trace = RerankAgent().run(
        candidates,
        parsed,
        limit=final_limit,
        query=query,
        rerank_provider=rerank_provider,
        degraded_channels=degraded_channels,
        rerank_top_k=rerank_top_k or settings.rerank_top_k,
    )
    return candidates, reranked, fusion_trace, rerank_trace


def filter_low_similarity_dense_only_hits(
    channel_hits: dict[str, list[RankedHit]],
    *,
    min_similarity: float,
) -> dict[str, list[RankedHit]]:
    bm25_chunk_ids = {hit.chunk_id for hit in channel_hits.get("bm25", [])}
    eligible_dense = [
        hit
        for hit in channel_hits.get("dense", [])
        if hit.score >= min_similarity or hit.chunk_id in bm25_chunk_ids
    ]
    return {
        **channel_hits,
        "dense": [
            RankedHit(
                chunk_id=hit.chunk_id,
                rank=rank,
                score=hit.score,
                vector_ref=hit.vector_ref,
                embedding_id=hit.embedding_id,
            )
            for rank, hit in enumerate(eligible_dense, start=1)
        ],
    }


def collect_ranked_hits(
    engine: Engine,
    query: str,
    *,
    filters: RetrievalFilters,
    dense_top_k: int,
    bm25_top_k: int,
    embedding_provider=None,
    bm25_retriever: Bm25Retriever,
) -> tuple[dict[str, list[RankedHit]], dict[str, str]]:
    jobs = {
        "dense": (
            DenseRetrievalAgent().run,
            {
                "engine": engine,
                "query": query,
                "filters": filters,
                "limit": dense_top_k,
                "embedding_provider": embedding_provider,
            },
        ),
        "bm25": (
            Bm25RetrievalAgent().run,
            {
                "engine": engine,
                "query": query,
                "filters": filters,
                "limit": bm25_top_k,
                "retriever": bm25_retriever,
            },
        ),
    }
    channel_hits: dict[str, list[RankedHit]] = {"dense": [], "bm25": []}
    channel_status: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
        futures = {
            channel: executor.submit(run, **kwargs)
            for channel, (run, kwargs) in jobs.items()
        }
        for channel, future in futures.items():
            try:
                channel_hits[channel] = future.result()
                channel_status[channel] = "ok" if channel_hits[channel] else "empty"
            except Exception:  # noqa: BLE001 - one channel must degrade independently.
                channel_status[channel] = f"{channel}_degraded"
    return channel_hits, channel_status


def hydrate_fused_candidates(
    engine: Engine,
    fused: list[FusedHit],
) -> list[dict[str, object]]:
    if not fused:
        return []
    chunk_ids = [hit.chunk_id for hit in fused]
    with engine.connect() as connection:
        rows = connection.execute(
            select(
                document_chunks.c.id,
                document_chunks.c.document_id,
                document_chunks.c.chunk_type,
                document_chunks.c.content,
                document_chunks.c.source_locator,
            ).where(document_chunks.c.id.in_(chunk_ids))
        ).mappings().all()
    rows_by_id = {str(row["id"]): row for row in rows}
    candidates = []
    for hit in fused:
        row = rows_by_id.get(hit.chunk_id)
        if row is None:
            continue
        candidates.append(
            {
                "chunk_id": hit.chunk_id,
                "document_id": row["document_id"],
                "chunk_type": row["chunk_type"],
                "content": row["content"],
                "source_locator": row["source_locator"],
                "channels": sorted(hit.channel_ranks),
                "channel_ranks": dict(hit.channel_ranks),
                "channel_scores": dict(hit.channel_scores),
                "rrf_score": hit.rrf_score,
                "score": hit.rrf_score,
                "vector_ref": hit.vector_ref,
                "embedding_id": hit.embedding_id,
                "not_applicable": False,
            }
        )
    return candidates


def enforce_retrieval_filters(
    engine: Engine,
    candidates: list[dict[str, object]],
    *,
    filters: RetrievalFilters,
) -> list[dict[str, object]]:
    chunk_ids = [str(candidate["chunk_id"]) for candidate in candidates]
    if not chunk_ids:
        return []
    statement = (
        select(document_chunks.c.id)
        .select_from(
            document_chunks.join(
                documents,
                document_chunks.c.document_id == documents.c.id,
            )
        )
        .where(document_chunks.c.id.in_(chunk_ids))
        .where(*document_filter_conditions(filters))
        .where(*chunk_filter_conditions(document_chunks.c.id, filters))
    )
    with engine.connect() as connection:
        allowed = set(connection.execute(statement).scalars().all())
    return [candidate for candidate in candidates if candidate["chunk_id"] in allowed]


def rerank_candidates(
    candidates: list[dict[str, object]],
    parsed: ParsedQuery,
    *,
    limit: int,
    query: str | None = None,
    rerank_provider=None,
    degraded_channels: dict[str, str] | None = None,
    rerank_top_k: int | None = None,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    if rerank_provider is not None:
        try:
            return model_rerank_candidates(
                candidates,
                limit=limit,
                query=query or "",
                rerank_provider=rerank_provider,
                rerank_top_k=rerank_top_k,
            )
        except Exception:  # noqa: BLE001 - rerank degradation must not fail retrieval.
            if degraded_channels is not None:
                degraded_channels["rerank"] = "rerank_degraded"
    before_positions = {str(candidate["chunk_id"]): index for index, candidate in enumerate(candidates, start=1)}
    scored: list[dict[str, object]] = []
    for candidate in candidates:
        reranked = dict(candidate)
        reranked["rerank_score"] = rerank_score(candidate, parsed)
        reranked["rerank_factors"] = rerank_factors(candidate, parsed)
        scored.append(reranked)

    ranked = sorted(
        scored,
        key=lambda item: (-float(item["rerank_score"]), str(item["chunk_id"])),
    )[:limit]
    after_positions = {str(candidate["chunk_id"]): index for index, candidate in enumerate(ranked, start=1)}
    trace_items = []
    for candidate in ranked:
        chunk_id = str(candidate["chunk_id"])
        trace_items.append(
            {
                "chunk_id": chunk_id,
                "before_rank": before_positions[chunk_id],
                "after_rank": after_positions[chunk_id],
                "before_score": round(float(candidate["score"]), 6),
                "after_score": round(float(candidate["rerank_score"]), 6),
                "channels": list(candidate["channels"]),
                "factors": candidate["rerank_factors"],
            }
        )
    return ranked, {"strategy": "deterministic_evidence_rerank", "fallback": True, "items": trace_items}


def model_rerank_candidates(
    candidates: list[dict[str, object]],
    *,
    limit: int,
    query: str,
    rerank_provider,
    rerank_top_k: int | None = None,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    top_k = max(limit, rerank_top_k or len(candidates))
    rerank_input = candidates[:top_k]
    documents = [str(candidate["content"]) for candidate in rerank_input]
    scores = rerank_provider.rerank(query, documents)
    if len(scores) != len(rerank_input):
        raise RerankError(
            f"Rerank service returned {len(scores)} scores for {len(rerank_input)} candidates"
        )

    reranked_items: list[dict[str, object]] = []
    for candidate, rerank_score_value in zip(rerank_input, scores, strict=True):
        reranked = dict(candidate)
        reranked["rerank_score"] = float(rerank_score_value)
        reranked_items.append(reranked)

    ranked = sorted(
        reranked_items,
        key=lambda item: (-float(item["rerank_score"]), str(item["chunk_id"])),
    )[:limit]
    before_positions = {str(candidate["chunk_id"]): index for index, candidate in enumerate(candidates, start=1)}
    after_positions = {str(candidate["chunk_id"]): index for index, candidate in enumerate(ranked, start=1)}
    trace_items = []
    for candidate in ranked:
        chunk_id = str(candidate["chunk_id"])
        trace_items.append(
            {
                "chunk_id": chunk_id,
                "before_rank": before_positions[chunk_id],
                "after_rank": after_positions[chunk_id],
                "before_score": round(float(candidate["score"]), 6),
                "after_score": round(float(candidate["rerank_score"]), 6),
                "channels": list(candidate["channels"]),
            }
        )
    return ranked, {"strategy": "bailian_model_rerank", "fallback": False, "items": trace_items}


def rerank_score(candidate: dict[str, object], parsed: ParsedQuery) -> float:
    factors = rerank_factors(candidate, parsed)
    return sum(float(value) for value in factors.values())


def rerank_factors(candidate: dict[str, object], parsed: ParsedQuery) -> dict[str, float]:
    score = float(candidate["score"])
    channels = set(candidate["channels"])
    content = str(candidate.get("content") or "").lower()

    factors = {
        "base_score": score,
        "model_match": model_match_score(candidate, parsed),
        "fault_code_match": fault_code_match_score(content, parsed),
        "source_credibility": source_credibility_score(candidate),
        "text_relevance": text_relevance_score(content, parsed),
        "channel_diversity": 0.15 * len(channels),
        "scope_uncertainty_penalty": -0.2 if parsed.scope_uncertain else 0.0,
        "applicability_penalty": -(score * 0.9) if candidate.get("not_applicable") else 0.0,
    }
    return {key: round(value, 6) for key, value in factors.items()}


def model_match_score(candidate: dict[str, object], parsed: ParsedQuery) -> float:
    explicit_model = parsed.filters.get("model")
    if not explicit_model:
        return 0.0
    if candidate.get("not_applicable"):
        return -1.5
    content = normalize(str(candidate.get("content") or ""))
    normalized_model = normalize(str(explicit_model))
    return 1.5 if normalized_model in content else 0.75


def fault_code_match_score(content: str, parsed: ParsedQuery) -> float:
    fault_codes = [str(code).lower() for code in parsed.entities.get("fault_code") or []]
    if not fault_codes:
        return 0.0
    matches = sum(1 for code in fault_codes if code in content)
    return round(1.2 * matches, 6)


def source_credibility_score(candidate: dict[str, object]) -> float:
    locator = candidate.get("source_locator") or {}
    chunk_type = candidate.get("chunk_type")
    if isinstance(locator, dict):
        locator_type = str(locator.get("type") or "")
        if locator_type in {"pdf", "text", "markdown", "docx", "csv", "xlsx", "pdf_table"}:
            return 1.0
        if locator_type in {"pdf_page", "image"}:
            return 0.4
    if chunk_type == ChunkType.TABLE.value:
        return 1.0
    if chunk_type == ChunkType.TEXT.value:
        return 0.8
    if chunk_type == ChunkType.IMAGE.value:
        return 0.3
    return 0.0


def text_relevance_score(content: str, parsed: ParsedQuery) -> float:
    query_terms = [
        token.lower()
        for token in parsed.original_query.split()
        if len(token.strip()) >= 2
    ]
    if not query_terms:
        return 0.0
    overlaps = sum(1 for term in query_terms if term in content)
    return round(0.3 * overlaps, 6)


def evidence_insufficient() -> dict[str, object]:
    return {
        "status": "evidence_insufficient",
        "candidates": [],
        "message": "No evidence found for the query",
    }


def channel_trace(
    candidates: list[dict[str, object]],
    degraded_channels: dict[str, str],
    *,
    settings,
) -> dict[str, object]:
    used = sorted({channel for candidate in candidates for channel in candidate["channels"]})
    degraded = [
        {"channel": channel, "reason": reason}
        for channel, reason in sorted(degraded_channels.items(), key=lambda item: item[0])
    ]
    return {
        "used": used,
        "degraded": degraded,
        "embedding_version": settings.embedding_version,
    }


def trace_model_config(settings) -> dict[str, object]:
    return {
        "model_provider": settings.model_provider,
        "llm_model": settings.llm_model,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "embedding_dimension": settings.embedding_dimension,
        "embedding_version": settings.embedding_version,
        "chunk_profile": settings.chunk_profile,
        "vector_backend": "pgvector",
        "vector_collection": None,
        "bm25_backend": "pg_search",
        "bm25_top_k": settings.bm25_top_k,
        "dense_top_k": settings.dense_top_k,
        "dense_only_min_similarity": settings.dense_only_min_similarity,
        "rrf_k": settings.rrf_k,
        "rrf_dense_weight": settings.rrf_dense_weight,
        "rrf_bm25_weight": settings.rrf_bm25_weight,
        "fusion_top_k": settings.fusion_top_k,
        "query_rewrite_model": settings.query_rewrite_model if settings.query_rewrite_enabled else None,
        "graph_backend": settings.graph_backend,
        "rerank_enabled": settings.rerank_enabled,
        "rerank_model": settings.rerank_model if settings.rerank_enabled else None,
        "rerank_top_k": settings.rerank_top_k,
        "final_evidence_limit": settings.final_evidence_limit,
    }


def trace_candidate(candidate: dict[str, object]) -> dict[str, object]:
    return {
        "chunk_id": candidate["chunk_id"],
        "document_id": candidate["document_id"],
        "chunk_type": candidate["chunk_type"],
        "content": candidate["content"],
        "source_locator": candidate["source_locator"],
        "channels": list(candidate["channels"]),
        "channel_ranks": dict(candidate["channel_ranks"]),
        "channel_scores": dict(candidate["channel_scores"]),
        "rrf_score": candidate["rrf_score"],
        "score": round(float(candidate["score"]), 6),
        "rerank_score": round(float(candidate.get("rerank_score", candidate["score"])), 6),
        "not_applicable": bool(candidate.get("not_applicable", False)),
        "applicability_reason": candidate.get("applicability_reason"),
        "vector_ref": candidate.get("vector_ref"),
        "embedding_id": candidate.get("embedding_id"),
    }


def evidence_payload(candidates: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "chunk_id": candidate["chunk_id"],
            "document_id": candidate["document_id"],
            "chunk_type": candidate["chunk_type"],
            "content": candidate["content"],
            "source_locator": candidate["source_locator"],
            "channels": list(candidate["channels"]),
            "channel_ranks": dict(candidate["channel_ranks"]),
            "channel_scores": dict(candidate["channel_scores"]),
            "rrf_score": candidate["rrf_score"],
            "score": round(float(candidate.get("rerank_score", candidate["score"])), 6),
            "not_applicable": bool(candidate.get("not_applicable", False)),
            "applicability_reason": candidate.get("applicability_reason"),
            "vector_ref": candidate.get("vector_ref"),
            "embedding_id": candidate.get("embedding_id"),
        }
        for candidate in candidates
    ]


def write_retrieval_log(
    engine: Engine,
    *,
    trace_id: str,
    query: str,
    filters: dict[str, object],
    channels: dict[str, object],
    model_config: dict[str, object],
    query_rewrite: dict[str, object],
    retrieval_round: int,
    fusion: dict[str, object],
    candidates: list[dict[str, object]],
    rerank: dict[str, object],
    final_evidence: list[dict[str, object]],
) -> None:
    validate_retrieval_round(retrieval_round)
    rewrite_attempt = {**query_rewrite, "retrieval_round": retrieval_round}
    fusion_attempt = {**fusion, "retrieval_round": retrieval_round}
    if retrieval_round == 1:
        rewrite_payload = append_trace_attempt(None, rewrite_attempt)
        fusion_payload = append_trace_attempt(None, fusion_attempt)
        fusion_payload["retrieval_duration_ms"] = round(
            float(fusion_attempt.get("retrieval_duration_ms", 0.0)),
            3,
        )
        try:
            with engine.begin() as connection:
                connection.execute(
                    retrieval_logs.insert().values(
                        id=str(uuid4()),
                        trace_id=trace_id,
                        query=query,
                        filters=filters,
                        channels=channels,
                        model_config=model_config,
                        query_rewrite=rewrite_payload,
                        fusion=fusion_payload,
                        retrieval_round=1,
                        citation_status="pending",
                        candidates=candidates,
                        rerank=rerank,
                        final_evidence=final_evidence,
                    )
                )
        except IntegrityError as exc:
            raise RetrievalTraceConflictError("retrieval trace already exists") from exc
        return

    with engine.begin() as connection:
        existing = connection.execute(
            select(retrieval_logs)
            .where(retrieval_logs.c.trace_id == trace_id)
            .with_for_update()
        ).mappings().one_or_none()
        if not supplemental_trace_is_appendable(
            existing,
            query=query,
            filters=filters,
        ):
            raise RetrievalTraceConflictError("retrieval trace cannot accept a supplemental round")
        rewrite_payload = append_trace_attempt(
            existing["query_rewrite"],
            rewrite_attempt,
        )
        fusion_payload = append_trace_attempt(
            existing["fusion"],
            fusion_attempt,
        )
        fusion_payload["retrieval_duration_ms"] = round(
            sum(
                float(item.get("retrieval_duration_ms", 0.0))
                for item in fusion_payload["attempts"]
            ),
            3,
        )
        result = connection.execute(
            update(retrieval_logs)
            .where(retrieval_logs.c.id == existing["id"])
            .where(retrieval_logs.c.retrieval_round == 1)
            .where(retrieval_logs.c.citation_status == "pending")
            .values(
                channels=channels,
                model_config=model_config,
                query_rewrite=rewrite_payload,
                fusion=fusion_payload,
                retrieval_round=2,
                candidates=candidates,
                rerank=rerank,
                final_evidence=final_evidence,
            )
        )
        if result.rowcount != 1:
            raise RetrievalTraceConflictError("supplemental retrieval trace update lost its round guard")


def validate_retrieval_round(retrieval_round: int) -> None:
    if retrieval_round not in {1, 2}:
        raise RetrievalTraceConflictError("retrieval round must be 1 or 2")


def supplemental_trace_is_appendable(
    existing,
    *,
    query: str,
    filters: dict[str, object],
) -> bool:
    if existing is None:
        return False
    rewrite = dict(existing["query_rewrite"] or {})
    fusion = dict(existing["fusion"] or {})
    rewrite_attempts = list(rewrite.get("attempts") or [])
    fusion_attempts = list(fusion.get("attempts") or [])
    rewrite_final = dict(rewrite.get("final") or {})
    fusion_final = dict(fusion.get("final") or {})
    channels = dict(existing["channels"] or {})
    return (
        existing["query"] == query
        and dict(existing["filters"] or {}) == filters
        and "citation" not in channels
        and len(rewrite_attempts) == 1
        and len(fusion_attempts) == 1
        and rewrite_final.get("retrieval_round") == 1
        and fusion_final.get("retrieval_round") == 1
    )


def append_trace_attempt(
    existing: dict[str, object] | None,
    current: dict[str, object],
) -> dict[str, object]:
    previous = dict(existing or {})
    attempts = list(previous.get("attempts") or [])
    attempts.append(dict(current))
    return {"attempts": attempts, "final": dict(current)}


def graph_candidates(
    engine: Engine,
    parsed: ParsedQuery,
    *,
    graph_service=None,
    degraded_channels: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    return []


def apply_model_applicability(
    engine: Engine,
    candidates: list[dict[str, object]],
    parsed: ParsedQuery,
) -> None:
    explicit_model = parsed.filters.get("model")
    if not explicit_model:
        if parsed.scope_uncertain:
            for candidate in candidates:
                candidate["scope_uncertain"] = True
        return

    models_by_chunk = chunk_entity_values_by_id(
        engine,
        [str(candidate["chunk_id"]) for candidate in candidates],
        "model",
    )
    normalized_model = normalize(str(explicit_model))
    for candidate in candidates:
        candidate_models = models_by_chunk.get(str(candidate["chunk_id"]), set())
        if candidate_models and normalized_model not in candidate_models:
            candidate["not_applicable"] = True
            candidate["applicability_reason"] = "model_mismatch"


def chunk_entity_values_by_id(
    engine: Engine,
    chunk_ids: list[str],
    entity_type: str,
) -> dict[str, set[str]]:
    if not chunk_ids:
        return {}
    with engine.connect() as connection:
        rows = connection.execute(
            select(
                chunk_entity_links.c.chunk_id,
                chunk_entity_links.c.normalized_value,
            )
            .where(chunk_entity_links.c.chunk_id.in_(chunk_ids))
            .where(chunk_entity_links.c.entity_type == entity_type)
        ).all()
    values_by_chunk: dict[str, set[str]] = {}
    for chunk_id, normalized_value in rows:
        values_by_chunk.setdefault(str(chunk_id), set()).add(str(normalized_value))
    return values_by_chunk
