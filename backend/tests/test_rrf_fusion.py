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


def test_rrf_duplicate_selection_uses_best_rank_regardless_of_input_order() -> None:
    hits = [hit("a", 4, 0.4), hit("a", 1, 0.9), hit("b", 2, 0.8)]

    forward = rrf_fuse(
        {"dense": hits},
        rrf_k=60,
        weights={"dense": 1.0},
        limit=1,
    )
    reversed_order = rrf_fuse(
        {"dense": list(reversed(hits))},
        rrf_k=60,
        weights={"dense": 1.0},
        limit=1,
    )

    assert forward == reversed_order
    fused, trace = forward
    assert fused[0].channel_ranks == {"dense": 1}
    assert fused[0].channel_scores == {"dense": 0.9}
    assert trace["channel_counts"] == {"dense": 3}
    assert [item["chunk_id"] for item in trace["items"]] == [
        item.chunk_id for item in fused
    ]


def test_rrf_duplicate_can_fill_missing_references_without_contributing_twice() -> None:
    fused, _trace = rrf_fuse(
        {
            "dense": [
                hit("a", 1, 0.9),
                RankedHit(
                    chunk_id="a",
                    rank=3,
                    score=0.7,
                    vector_ref="pgvector://chunks/embedding-a",
                    embedding_id="embedding-a",
                ),
            ]
        },
        rrf_k=60,
        weights={"dense": 1.0},
        limit=10,
    )

    assert fused[0].vector_ref == "pgvector://chunks/embedding-a"
    assert fused[0].embedding_id == "embedding-a"
    assert fused[0].channel_ranks == {"dense": 1}
    assert fused[0].channel_scores == {"dense": 0.9}
    assert fused[0].rrf_score == pytest.approx(1.0 / 61)


def test_rrf_equal_rank_duplicates_use_deterministic_secondary_order() -> None:
    hits = [
        RankedHit("a", 1, 0.9, vector_ref="vector-z"),
        RankedHit("a", 1, 0.9, vector_ref="vector-a"),
        RankedHit("a", 1, 0.8, embedding_id="embedding-z"),
        RankedHit("a", 1, 0.8, embedding_id="embedding-a"),
    ]

    forward = rrf_fuse(
        {"dense": hits},
        rrf_k=60,
        weights={"dense": 1.0},
        limit=10,
    )
    reversed_order = rrf_fuse(
        {"dense": list(reversed(hits))},
        rrf_k=60,
        weights={"dense": 1.0},
        limit=10,
    )

    assert forward == reversed_order
    fused, trace = forward
    assert fused[0].channel_scores == {"dense": 0.9}
    assert fused[0].vector_ref == "vector-a"
    assert fused[0].embedding_id == "embedding-a"
    assert trace["items"][0]["channel_scores"] == {"dense": 0.9}


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
