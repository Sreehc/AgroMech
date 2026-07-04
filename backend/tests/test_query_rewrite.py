from agromech_api.rag.retrieval.query_rewrite import rewrite_query_for_evidence


def test_query_rewrite_expands_domain_synonyms_and_preserves_filters() -> None:
    result = rewrite_query_for_evidence(
        question="液压泵异响怎么检查？",
        filters={"model": "M7040"},
        missing=["part"],
    )

    assert "液压泵异响怎么检查？" in result["query"]
    assert "hydraulic pump" in result["query"]
    assert "abnormal noise" in result["query"]
    assert result["filters"] == {"model": "M7040"}
    assert result["reason"] == "expanded domain synonyms for missing evidence"
