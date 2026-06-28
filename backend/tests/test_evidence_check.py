from agromech_api.evidence_check import check_evidence_sufficiency


def test_empty_evidence_is_insufficient() -> None:
    result = check_evidence_sufficiency(question="M7040 液压油多久换一次？", final_evidence=[], citations=[])

    assert result["status"] == "insufficient"
    assert "citation" in result["missing"]


def test_cited_evidence_is_sufficient() -> None:
    result = check_evidence_sufficiency(
        question="M7040 液压油多久换一次？",
        final_evidence=[{"chunk_id": "chunk-1", "document_id": "doc-1"}],
        citations=[{"chunk_id": "chunk-1", "document_id": "doc-1", "evidence_snippet": "change hydraulic oil"}],
    )

    assert result["status"] == "sufficient"
    assert result["missing"] == []
