from __future__ import annotations

from sqlalchemy import Engine, select

from agromech_api.db.enums import ChunkType
from agromech_api.db.models import chunk_entity_links, document_chunks
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
    ranked = sorted(viable_candidates, key=lambda item: item["score"], reverse=True)[:limit]
    if not ranked:
        return {
            "status": "evidence_insufficient",
            "candidates": [],
            "message": "No evidence found for the query",
        }
    return {"status": "ok", "candidates": ranked}


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
