from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RankedHit:
    chunk_id: str
    rank: int
    score: float
    vector_ref: str | None = None
    embedding_id: str | None = None


@dataclass
class FusedHit:
    chunk_id: str
    rrf_score: float = 0.0
    channel_ranks: dict[str, int] = field(default_factory=dict)
    channel_scores: dict[str, float] = field(default_factory=dict)
    vector_ref: str | None = None
    embedding_id: str | None = None


def rrf_fuse(
    channel_hits: dict[str, list[RankedHit]],
    *,
    rrf_k: int,
    weights: dict[str, float],
    limit: int,
) -> tuple[list[FusedHit], dict[str, object]]:
    fused: dict[str, FusedHit] = {}
    for channel, hits in channel_hits.items():
        hits_by_chunk: dict[str, list[RankedHit]] = {}
        for hit in hits:
            hits_by_chunk.setdefault(hit.chunk_id, []).append(hit)
        for chunk_id, duplicate_hits in hits_by_chunk.items():
            ranked_hits = sorted(duplicate_hits, key=lambda hit: hit.rank)
            best_hit = ranked_hits[0]
            item = fused.setdefault(chunk_id, FusedHit(chunk_id=chunk_id))
            item.channel_ranks[channel] = best_hit.rank
            item.channel_scores[channel] = best_hit.score
            item.rrf_score += weights.get(channel, 0.0) / (rrf_k + best_hit.rank)
            item.vector_ref = item.vector_ref or next(
                (hit.vector_ref for hit in ranked_hits if hit.vector_ref), None
            )
            item.embedding_id = item.embedding_id or next(
                (hit.embedding_id for hit in ranked_hits if hit.embedding_id), None
            )
    ranked = sorted(
        fused.values(),
        key=lambda item: (-item.rrf_score, min(item.channel_ranks.values()), item.chunk_id),
    )[:limit]
    trace = {
        "rrf_k": rrf_k,
        "weights": dict(weights),
        "channel_counts": {channel: len(hits) for channel, hits in channel_hits.items()},
        "items": [
            {
                "chunk_id": item.chunk_id,
                "channel_ranks": dict(item.channel_ranks),
                "channel_scores": dict(item.channel_scores),
                "rrf_score": round(item.rrf_score, 8),
            }
            for item in ranked
        ],
    }
    return ranked, trace
