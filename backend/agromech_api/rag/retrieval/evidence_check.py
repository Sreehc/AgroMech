from __future__ import annotations

from typing import Any


def check_evidence_sufficiency(
    *,
    question: str,
    final_evidence: list[dict[str, Any]],
    citations: list[dict[str, Any]],
    require_visual: bool = False,
) -> dict[str, Any]:
    missing: list[str] = []
    if not final_evidence:
        missing.append("evidence")
    if not citations:
        missing.append("citation")
    if require_visual:
        visual_evidence_ids = {
            (str(item["asset_id"]), str(item.get("document_id")))
            for item in final_evidence
            if item.get("asset_id")
        }
        visual_citation_ids = {
            (str(item["asset_id"]), str(item.get("document_id")))
            for item in citations
            if item.get("asset_id")
        }
        if not visual_evidence_ids:
            missing.append("visual_evidence")
        if not visual_evidence_ids.intersection(visual_citation_ids):
            missing.append("visual_citation")

    if missing:
        return {
            "status": "insufficient",
            "missing": missing,
            "reason": "required evidence or citation is missing",
            "confidence": 0.95,
        }

    return {
        "status": "sufficient",
        "missing": [],
        "reason": "final evidence has citation support",
        "confidence": 0.85,
    }
