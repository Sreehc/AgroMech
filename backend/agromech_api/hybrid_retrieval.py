from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Engine, select

from agromech_api.config import get_settings
from agromech_api.db.enums import ChunkType
from agromech_api.db.models import chunk_entity_links, document_chunks, retrieval_logs
from agromech_api.entity_extraction import normalize
from agromech_api.graph_rag import GraphRagService
from agromech_api.query_understanding import ParsedQuery, parse_query, structured_filter_chunks
from agromech_api.rerank import RerankError
from agromech_api.search_indexing import keyword_search, vector_search


CHANNEL_WEIGHTS = {
    "structured": 4.0,
    "keyword": 2.0,
    "vector": 1.5,
    "graph": 1.2,
    "vision": 1.0,
}
MIN_CANDIDATE_SCORE = 0.25


def hybrid_retrieve(
    engine: Engine,
    query: str,
    *,
    limit: int = 10,
    vector_store=None,
    vector_collection: str | None = None,
    embedding_provider=None,
    graph_service=None,
    rerank_provider=None,
    rerank_top_k: int | None = None,
) -> dict[str, object]:
    parsed = parse_query(query, engine=engine)
    degraded_channels: dict[str, str] = {}
    candidate_limit = max(limit, rerank_top_k or limit)
    ranked = rank_candidates(
        collect_candidates(
            engine,
            query,
            parsed,
            limit=candidate_limit,
            vector_store=vector_store,
            vector_collection=vector_collection,
            embedding_provider=embedding_provider,
            graph_service=graph_service,
            degraded_channels=degraded_channels,
        ),
        limit=candidate_limit,
    )
    reranked, _trace = rerank_candidates(
        ranked,
        parsed,
        limit=limit,
        query=query,
        rerank_provider=rerank_provider,
        degraded_channels=degraded_channels,
        rerank_top_k=rerank_top_k,
    )
    if not reranked:
        return evidence_insufficient()
    return {"status": "ok", "candidates": reranked}


def hybrid_retrieve_with_trace(
    engine: Engine,
    query: str,
    *,
    trace_id: str | None = None,
    limit: int = 10,
    logged_query: str | None = None,
    filters: dict[str, object] | None = None,
    degraded_channels: dict[str, str] | None = None,
    vector_store=None,
    vector_collection: str | None = None,
    embedding_provider=None,
    graph_service=None,
    rerank_provider=None,
    rerank_top_k: int | None = None,
    settings=None,
) -> dict[str, object]:
    settings = settings or get_settings()
    parsed = parse_query(query, engine=engine)
    trace_id = trace_id or str(uuid4())
    degraded_channels = dict(degraded_channels or {})
    candidate_limit = max(limit, rerank_top_k or limit)
    ranked = rank_candidates(
        collect_candidates(
            engine,
            query,
            parsed,
            limit=candidate_limit,
            vector_store=vector_store,
            vector_collection=vector_collection,
            embedding_provider=embedding_provider,
            graph_service=graph_service,
            degraded_channels=degraded_channels,
        ),
        limit=candidate_limit,
    )
    reranked, rerank_trace = rerank_candidates(
        ranked,
        parsed,
        limit=limit,
        query=query,
        rerank_provider=rerank_provider,
        degraded_channels=degraded_channels,
        rerank_top_k=rerank_top_k,
    )
    status = "ok" if reranked else "evidence_insufficient"
    final_evidence = evidence_payload(reranked)
    channels = channel_trace(ranked, degraded_channels)
    write_retrieval_log(
        engine,
        trace_id=trace_id,
        query=logged_query or query,
        filters=dict(filters or parsed.filters),
        channels=channels,
        model_config=trace_model_config(settings),
        candidates=[trace_candidate(candidate) for candidate in ranked],
        rerank=rerank_trace,
        final_evidence=final_evidence,
    )
    if not reranked:
        return {**evidence_insufficient(), "trace_id": trace_id}
    return {
        "status": status,
        "trace_id": trace_id,
        "candidates": reranked,
        "final_evidence": final_evidence,
    }


def collect_candidates(
    engine: Engine,
    query: str,
    parsed: ParsedQuery,
    *,
    limit: int,
    vector_store=None,
    vector_collection: str | None = None,
    embedding_provider=None,
    graph_service=None,
    degraded_channels: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    candidates: dict[str, dict[str, object]] = {}

    for result in keyword_search(engine, query, limit=limit * 2):
        add_candidate(engine, candidates, result["chunk_id"], "keyword", float(result["score"]))
    for result in vector_search(
        engine,
        query,
        limit=limit * 2,
        vector_store=vector_store,
        collection=vector_collection,
        embedding_provider=embedding_provider,
    ):
        add_candidate(
            engine,
            candidates,
            result["chunk_id"],
            "vector",
            float(result["score"]),
            vector_ref=result.get("vector_ref"),
        )
    for result in structured_filter_chunks(engine, parsed):
        add_candidate(engine, candidates, result["chunk_id"], "structured", float(len(result["matched_filters"])))
    for result in graph_candidates(engine, parsed, graph_service=graph_service, degraded_channels=degraded_channels):
        source_chunk_id = result.get("source_chunk_id")
        if source_chunk_id:
            add_candidate(engine, candidates, str(source_chunk_id), "graph", float(result["confidence"]))

    for candidate in candidates.values():
        if candidate["chunk_type"] == ChunkType.IMAGE.value:
            add_channel(candidate, "vision", CHANNEL_WEIGHTS["vision"])
        apply_model_applicability(engine, candidate, parsed)

    viable_candidates = [
        candidate
        for candidate in candidates.values()
        if candidate["score"] >= MIN_CANDIDATE_SCORE or any(channel in candidate["channels"] for channel in ["keyword", "structured", "graph"])
    ]
    return viable_candidates


def rank_candidates(candidates: list[dict[str, object]], *, limit: int) -> list[dict[str, object]]:
    return sorted(candidates, key=lambda item: item["score"], reverse=True)[:limit]


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

    ranked = sorted(scored, key=lambda item: item["rerank_score"], reverse=True)[:limit]
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

    ranked = sorted(reranked_items, key=lambda item: item["rerank_score"], reverse=True)[:limit]
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
        "structured_bonus": 1.0 if "structured" in channels else 0.0,
        "graph_bonus": 0.5 if "graph" in channels else 0.0,
        "vision_bonus": 0.25 if "vision" in channels else 0.0,
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


def channel_trace(candidates: list[dict[str, object]], degraded_channels: dict[str, str]) -> dict[str, object]:
    used = sorted({channel for candidate in candidates for channel in candidate["channels"]})
    degraded = [
        {"channel": channel, "reason": reason}
        for channel, reason in sorted(degraded_channels.items(), key=lambda item: item[0])
    ]
    return {
        "used": used,
        "degraded": degraded,
        "embedding_version": get_settings().embedding_version,
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
        "vector_backend": settings.vector_backend,
        "vector_collection": settings.zvec_collection if settings.vector_backend == "zvec" else settings.milvus_collection,
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
        "score": round(float(candidate["score"]), 6),
        "rerank_score": round(float(candidate.get("rerank_score", candidate["score"])), 6),
        "not_applicable": bool(candidate.get("not_applicable", False)),
        "applicability_reason": candidate.get("applicability_reason"),
        "vector_ref": candidate.get("vector_ref"),
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
            "score": round(float(candidate.get("rerank_score", candidate["score"])), 6),
            "not_applicable": bool(candidate.get("not_applicable", False)),
            "applicability_reason": candidate.get("applicability_reason"),
            "vector_ref": candidate.get("vector_ref"),
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
    candidates: list[dict[str, object]],
    rerank: dict[str, object],
    final_evidence: list[dict[str, object]],
) -> None:
    with engine.begin() as connection:
        connection.execute(
            retrieval_logs.insert().values(
                id=str(uuid4()),
                trace_id=trace_id,
                query=query,
                filters=filters,
                channels=channels,
                model_config=model_config,
                candidates=candidates,
                rerank=rerank,
                final_evidence=final_evidence,
            )
        )


def graph_candidates(
    engine: Engine,
    parsed: ParsedQuery,
    *,
    graph_service=None,
    degraded_channels: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    service = graph_service or GraphRagService(engine)
    results: list[dict[str, object]] = []
    try:
        for model in parsed.entities.get("model") or []:
            results.extend(service.expand(entity_type="model", value=model, max_hops=2))
        for fault_code in parsed.entities.get("fault_code") or []:
            results.extend(service.expand(entity_type="fault_code", value=fault_code, max_hops=1))
    except Exception:  # noqa: BLE001 - graph search is an optional retrieval channel.
        if degraded_channels is not None:
            degraded_channels["graph"] = "graph_degraded"
        return []
    return [result for result in results if result.get("source_chunk_id")]


def add_candidate(
    engine: Engine,
    candidates: dict[str, dict[str, object]],
    chunk_id: str,
    channel: str,
    score: float,
    vector_ref: object | None = None,
) -> None:
    candidate = candidates.get(chunk_id)
    if candidate is None:
        candidate = chunk_payload(engine, chunk_id)
        candidates[chunk_id] = candidate
    add_channel(candidate, channel, score * CHANNEL_WEIGHTS[channel])
    if vector_ref:
        candidate["vector_ref"] = vector_ref


def add_channel(candidate: dict[str, object], channel: str, weighted_score: float) -> None:
    channel_scores = candidate.setdefault("channel_scores", {})
    existing_score = float(channel_scores.get(channel, 0.0))
    if weighted_score <= existing_score:
        return
    channel_scores[channel] = weighted_score
    if channel not in candidate["channels"]:
        candidate["channels"].append(channel)
    candidate["score"] = sum(float(score) for score in channel_scores.values())


def chunk_payload(engine: Engine, chunk_id: str) -> dict[str, object]:
    with engine.connect() as connection:
        chunk = connection.execute(
            select(document_chunks).where(document_chunks.c.id == chunk_id)
        ).mappings().one()
    return {
        "chunk_id": chunk["id"],
        "document_id": chunk["document_id"],
        "chunk_type": chunk["chunk_type"],
        "content": chunk["content"],
        "source_locator": chunk["source_locator"],
        "channels": [],
        "channel_scores": {},
        "score": 0.0,
        "not_applicable": False,
        "vector_ref": None,
    }


def apply_model_applicability(engine: Engine, candidate: dict[str, object], parsed: ParsedQuery) -> None:
    explicit_model = parsed.filters.get("model")
    if not explicit_model:
        if parsed.scope_uncertain:
            candidate["scope_uncertain"] = True
        return

    candidate_models = chunk_entity_values(engine, candidate["chunk_id"], "model")
    if candidate_models and normalize(str(explicit_model)) not in candidate_models:
        candidate["not_applicable"] = True
        candidate["applicability_reason"] = "model_mismatch"
        candidate["score"] *= 0.1


def chunk_entity_values(engine: Engine, chunk_id: str, entity_type: str) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            select(chunk_entity_links.c.normalized_value)
            .where(chunk_entity_links.c.chunk_id == chunk_id)
            .where(chunk_entity_links.c.entity_type == entity_type)
        ).scalars().all()
    return set(rows)
