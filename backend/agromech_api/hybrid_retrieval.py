from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Engine, select

from agromech_api.db.enums import ChunkType
from agromech_api.db.models import chunk_entity_links, document_chunks, retrieval_logs
from agromech_api.entity_extraction import normalize
from agromech_api.graph_rag import GraphRagService
from agromech_api.query_understanding import ParsedQuery, parse_query, structured_filter_chunks
from agromech_api.search_indexing import keyword_search, vector_search


CHANNEL_WEIGHTS = {
    "structured": 4.0,
    "keyword": 2.0,
    "vector": 1.5,
    "graph": 1.2,
    "vision": 1.0,
}
MIN_CANDIDATE_SCORE = 0.25


def hybrid_retrieve(engine: Engine, query: str, *, limit: int = 10) -> dict[str, object]:
    parsed = parse_query(query)
    ranked = rank_candidates(collect_candidates(engine, query, parsed, limit=limit), limit=limit)
    reranked, _trace = rerank_candidates(ranked, parsed, limit=limit)
    if not reranked:
        return evidence_insufficient()
    return {"status": "ok", "candidates": reranked}


def hybrid_retrieve_with_trace(
    engine: Engine,
    query: str,
    *,
    trace_id: str | None = None,
    limit: int = 10,
    degraded_channels: dict[str, str] | None = None,
) -> dict[str, object]:
    parsed = parse_query(query)
    trace_id = trace_id or str(uuid4())
    ranked = rank_candidates(collect_candidates(engine, query, parsed, limit=limit), limit=limit)
    reranked, rerank_trace = rerank_candidates(ranked, parsed, limit=limit)
    status = "ok" if reranked else "evidence_insufficient"
    final_evidence = evidence_payload(reranked)
    channels = channel_trace(ranked, degraded_channels or {})
    write_retrieval_log(
        engine,
        trace_id=trace_id,
        query=query,
        filters=parsed.filters,
        channels=channels,
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


def collect_candidates(engine: Engine, query: str, parsed: ParsedQuery, *, limit: int) -> list[dict[str, object]]:
    candidates: dict[str, dict[str, object]] = {}

    for result in keyword_search(engine, query, limit=limit * 2):
        add_candidate(engine, candidates, result["chunk_id"], "keyword", float(result["score"]))
    for result in vector_search(engine, query, limit=limit * 2):
        add_candidate(engine, candidates, result["chunk_id"], "vector", float(result["score"]))
    for result in structured_filter_chunks(engine, parsed):
        add_candidate(engine, candidates, result["chunk_id"], "structured", float(len(result["matched_filters"])))
    for result in graph_candidates(engine, parsed):
        add_candidate(engine, candidates, result["source_chunk_id"], "graph", float(result["confidence"]))

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
) -> tuple[list[dict[str, object]], dict[str, object]]:
    before_positions = {str(candidate["chunk_id"]): index for index, candidate in enumerate(candidates, start=1)}
    scored: list[dict[str, object]] = []
    for candidate in candidates:
        reranked = dict(candidate)
        reranked["rerank_score"] = rerank_score(candidate, parsed)
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
            }
        )
    return ranked, {"strategy": "deterministic_evidence_rerank", "items": trace_items}


def rerank_score(candidate: dict[str, object], parsed: ParsedQuery) -> float:
    score = float(candidate["score"])
    channels = set(candidate["channels"])
    score += 0.15 * len(channels)
    if "structured" in channels:
        score += 1.0
    if "graph" in channels:
        score += 0.5
    if "vision" in channels:
        score += 0.25
    if parsed.scope_uncertain:
        score -= 0.2
    if candidate.get("not_applicable"):
        score *= 0.1
    return score


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
    return {"used": used, "degraded": degraded}


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
                candidates=candidates,
                rerank=rerank,
                final_evidence=final_evidence,
            )
        )


def graph_candidates(engine: Engine, parsed: ParsedQuery) -> list[dict[str, object]]:
    service = GraphRagService(engine)
    results: list[dict[str, object]] = []
    for model in parsed.entities.get("model") or []:
        results.extend(service.expand(entity_type="model", value=model, max_hops=2))
    for fault_code in parsed.entities.get("fault_code") or []:
        results.extend(service.expand(entity_type="fault_code", value=fault_code, max_hops=1))
    return results


def add_candidate(
    engine: Engine,
    candidates: dict[str, dict[str, object]],
    chunk_id: str,
    channel: str,
    score: float,
) -> None:
    candidate = candidates.get(chunk_id)
    if candidate is None:
        candidate = chunk_payload(engine, chunk_id)
        candidates[chunk_id] = candidate
    add_channel(candidate, channel, score * CHANNEL_WEIGHTS[channel])


def add_channel(candidate: dict[str, object], channel: str, weighted_score: float) -> None:
    if channel not in candidate["channels"]:
        candidate["channels"].append(channel)
    candidate["score"] += weighted_score


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
        "score": 0.0,
        "not_applicable": False,
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
