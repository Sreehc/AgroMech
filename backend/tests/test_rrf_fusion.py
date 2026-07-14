import pytest

from agromech_api.rag.retrieval.fusion import RankedHit, rrf_fuse


def hit(chunk_id: str, rank: int, score: float) -> RankedHit:
    return RankedHit(chunk_id=chunk_id, rank=rank, score=score)


def test_rrf_fuses_ranks_without_using_raw_score_scale() -> None:
    fused, trace = rrf_fuse(
        {
            "dense": [hit("a", 1, 0.91), hit("b", 2, 0.90)],
            "bm25": [hit("b", 1, 1000.0), hit("a", 2, 1.0)],
        },
        rrf_k=60,
        weights={"dense": 1.0, "bm25": 1.0},
        limit=10,
    )

    assert [item.chunk_id for item in fused] == ["a", "b"]
    assert fused[0].rrf_score == fused[1].rrf_score
    assert fused[0].channel_ranks == {"dense": 1, "bm25": 2}
    assert trace["rrf_k"] == 60


def test_rrf_supports_one_channel_and_deduplicates_chunk() -> None:
    fused, _trace = rrf_fuse(
        {"dense": [hit("a", 1, 0.9), hit("a", 2, 0.8), hit("b", 3, 0.7)]},
        rrf_k=60,
        weights={"dense": 1.0},
        limit=10,
    )

    assert [item.chunk_id for item in fused] == ["a", "b"]
    assert fused[0].channel_ranks == {"dense": 1}


def test_rrf_ties_use_best_rank_then_chunk_id() -> None:
    fused, _trace = rrf_fuse(
        {"dense": [hit("b", 1, 0.9), hit("a", 1, 0.9)]},
        rrf_k=60,
        weights={"dense": 1.0},
        limit=10,
    )

    assert [item.chunk_id for item in fused] == ["a", "b"]


@pytest.mark.parametrize("channel_hits", [{}, {"dense": []}])
def test_rrf_returns_empty_results_without_candidates(
    channel_hits: dict[str, list[RankedHit]],
) -> None:
    fused, trace = rrf_fuse(
        channel_hits,
        rrf_k=60,
        weights={"dense": 1.0},
        limit=10,
    )

    assert fused == []
    assert trace["items"] == []
