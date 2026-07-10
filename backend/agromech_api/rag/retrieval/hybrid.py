from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from sqlalchemy import Engine, or_, select

from agromech_api.core.config import get_settings
from agromech_api.db.enums import ChunkType
from agromech_api.db.models import chunk_entity_links, document_chunks, documents, retrieval_logs
from agromech_api.domain.entities import normalize
from agromech_api.rag.retrieval.indexing import keyword_search, vector_search
from agromech_api.rag.retrieval.query_understanding import ParsedQuery, parse_query, structured_filter_chunks
from agromech_api.rag.retrieval.rerank import RerankError


CHANNEL_WEIGHTS = {
    "structured": 4.0,
    "keyword": 2.0,
    "vector": 1.5,
    "vision": 1.0,
}
MIN_CANDIDATE_SCORE = 0.25


# 未登录访客（viewer_user_id 为 None）只能检索公用文档。这是一条 fail-closed
# 安全线：任何未能证明可见的候选都会被剔除。
def visible_document_ids(
    engine: Engine,
    document_ids: set[str],
    *,
    viewer_user_id: str | None,
) -> set[str]:
    """返回 document_ids 中对该访问者可见的子集。

    可见性规则与文档 REST 层保持一致：公用文档对所有人可见；私有文档仅归属者
    本人可见。登录用户传入其 user_id；匿名访客传 None，此时只返回公用文档。
    """
    if not document_ids:
        return set()
    conditions = [documents.c.visibility == "public"]
    if viewer_user_id is not None:
        conditions.append(documents.c.owner_user_id == viewer_user_id)
    with engine.connect() as connection:
        rows = connection.execute(
            select(documents.c.id)
            .where(
                documents.c.id.in_(document_ids),
                or_(*conditions),
            )
        ).scalars().all()
    return set(rows)


class KeywordRetrievalAgent:
    channel = "keyword"

    def run(self, engine: Engine, query: str, parsed: ParsedQuery, *, limit: int, **_kwargs) -> dict[str, object]:
        _ = parsed
        return {
            "channel": self.channel,
            "status": "ok",
            "results": [
                {
                    "chunk_id": result["chunk_id"],
                    "score": float(result["score"]),
                }
                for result in keyword_search(engine, query, limit=limit * 2)
            ],
        }


class VectorRetrievalAgent:
    channel = "vector"

    def run(
        self,
        engine: Engine,
        query: str,
        parsed: ParsedQuery,
        *,
        limit: int,
        embedding_provider=None,
        degraded_channels: dict[str, str] | None = None,
        viewer_user_id: str | None = None,
        **_kwargs,
    ) -> dict[str, object]:
        _ = parsed
        try:
            results = [
                {
                    "chunk_id": result["chunk_id"],
                    "score": float(result["score"]),
                    "vector_ref": result.get("vector_ref"),
                    "embedding_id": result.get("embedding_id"),
                }
                for result in vector_search(
                    engine,
                    query,
                    limit=limit * 2,
                    embedding_provider=embedding_provider,
                    viewer_user_id=viewer_user_id,
                )
            ]
        except Exception:  # noqa: BLE001 - vector degradation must not fail retrieval.
            if degraded_channels is not None:
                degraded_channels[self.channel] = "vector_degraded"
            return {"channel": self.channel, "status": "degraded", "results": []}
        return {"channel": self.channel, "status": "ok", "results": results}


class StructuredRetrievalAgent:
    channel = "structured"

    def run(self, engine: Engine, query: str, parsed: ParsedQuery, *, limit: int, **_kwargs) -> dict[str, object]:
        _ = query, limit
        return {
            "channel": self.channel,
            "status": "ok",
            "results": [
                {
                    "chunk_id": result["chunk_id"],
                    "score": float(len(result["matched_filters"])),
                }
                for result in structured_filter_chunks(engine, parsed)
            ],
        }


class EvidenceMergeAgent:
    def run(
        self,
        engine: Engine,
        channel_results: list[dict[str, object]],
        parsed: ParsedQuery,
    ) -> list[dict[str, object]]:
        candidates: dict[str, dict[str, object]] = {}
        for channel_result in channel_results:
            channel = str(channel_result["channel"])
            for result in channel_result.get("results", []):
                add_candidate(
                    engine,
                    candidates,
                    str(result["chunk_id"]),
                    channel,
                    float(result["score"]),
                    vector_ref=result.get("vector_ref") if isinstance(result, dict) else None,
                    embedding_id=result.get("embedding_id") if isinstance(result, dict) else None,
                )
        for candidate in candidates.values():
            apply_model_applicability(engine, candidate, parsed)
        return list(candidates.values())


class VisualRetrievalAgent:
    channel = "vision"

    def run(self, candidates: list[dict[str, object]]) -> list[dict[str, object]]:
        updated: list[dict[str, object]] = []
        for candidate in candidates:
            candidate = dict(candidate)
            if candidate["chunk_type"] == ChunkType.IMAGE.value:
                add_channel(candidate, self.channel, CHANNEL_WEIGHTS[self.channel])
            updated.append(candidate)
        return updated


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
    embedding_provider=None,
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
            embedding_provider=embedding_provider,
            degraded_channels=degraded_channels,
        ),
        limit=candidate_limit,
    )
    reranked, _trace = RerankAgent().run(
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
    embedding_provider=None,
    rerank_provider=None,
    rerank_top_k: int | None = None,
    viewer_user_id: str | None = None,
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
            embedding_provider=embedding_provider,
            degraded_channels=degraded_channels,
            viewer_user_id=viewer_user_id,
        ),
        limit=candidate_limit,
    )
    reranked, rerank_trace = RerankAgent().run(
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
    embedding_provider=None,
    degraded_channels: dict[str, str] | None = None,
    viewer_user_id: str | None = None,
) -> list[dict[str, object]]:
    channel_results = collect_channel_results(
        engine,
        query,
        parsed,
        limit=limit,
        embedding_provider=embedding_provider,
        degraded_channels=degraded_channels,
        viewer_user_id=viewer_user_id,
    )
    candidates = EvidenceMergeAgent().run(engine, channel_results, parsed)
    candidates = VisualRetrievalAgent().run(candidates)
    candidates = enforce_visibility(engine, candidates, viewer_user_id=viewer_user_id)
    return viable_candidates(candidates)


def enforce_visibility(
    engine: Engine,
    candidates: list[dict[str, object]],
    *,
    viewer_user_id: str | None,
) -> list[dict[str, object]]:
    """剔除访问者无权看到的候选（fail-closed 安全线）。

    在候选合并后、排序前统一过滤：公用文档对所有人可见，私有文档仅归属者本人
    可见，匿名访客只保留公用文档。任何 document_id 无法证明可见的候选都被丢弃。
    """
    document_ids = {
        str(candidate["document_id"])
        for candidate in candidates
        if candidate.get("document_id")
    }
    allowed = visible_document_ids(engine, document_ids, viewer_user_id=viewer_user_id)
    return [
        candidate
        for candidate in candidates
        if candidate.get("document_id") and str(candidate["document_id"]) in allowed
    ]


def collect_channel_results(
    engine: Engine,
    query: str,
    parsed: ParsedQuery,
    *,
    limit: int,
    embedding_provider=None,
    degraded_channels: dict[str, str] | None = None,
    viewer_user_id: str | None = None,
) -> list[dict[str, object]]:
    agents = [
        (
            KeywordRetrievalAgent(),
            {
                "engine": engine,
                "query": query,
                "parsed": parsed,
                "limit": limit,
            },
        ),
        (
            VectorRetrievalAgent(),
            {
                "engine": engine,
                "query": query,
                "parsed": parsed,
                "limit": limit,
                "embedding_provider": embedding_provider,
                "degraded_channels": degraded_channels,
                "viewer_user_id": viewer_user_id,
            },
        ),
        (
            StructuredRetrievalAgent(),
            {
                "engine": engine,
                "query": query,
                "parsed": parsed,
                "limit": limit,
            },
        ),
    ]
    with ThreadPoolExecutor(max_workers=len(agents)) as executor:
        futures = [
            executor.submit(agent.run, **kwargs)
            for agent, kwargs in agents
        ]
        return [future.result() for future in futures]


def viable_candidates(candidates: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        candidate
        for candidate in candidates
        if candidate["score"] >= MIN_CANDIDATE_SCORE
        or any(channel in candidate["channels"] for channel in ["keyword", "structured"])
    ]


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
        "vector_backend": "pgvector",
        "vector_collection": None,
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
    return []


def add_candidate(
    engine: Engine,
    candidates: dict[str, dict[str, object]],
    chunk_id: str,
    channel: str,
    score: float,
    vector_ref: object | None = None,
    embedding_id: object | None = None,
) -> None:
    candidate = candidates.get(chunk_id)
    if candidate is None:
        candidate = chunk_payload(engine, chunk_id)
        candidates[chunk_id] = candidate
    add_channel(candidate, channel, score * CHANNEL_WEIGHTS[channel])
    if vector_ref:
        candidate["vector_ref"] = vector_ref
    if embedding_id:
        candidate["embedding_id"] = embedding_id


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
        "embedding_id": None,
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
