from __future__ import annotations

from typing import Any


def check_evidence_sufficiency(
    *,
    question: str,
    final_evidence: list[dict[str, Any]],
    citations: list[dict[str, Any]],
) -> dict[str, Any]:
    missing: list[str] = []
    if not final_evidence:
        missing.append("evidence")
    if not citations:
        missing.append("citation")

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
