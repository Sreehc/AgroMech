from __future__ import annotations

from typing import Any


DOMAIN_SYNONYMS = {
    "液压泵": ["hydraulic pump"],
    "异响": ["abnormal noise"],
    "故障码": ["fault code"],
    "保养": ["maintenance"],
    "更换": ["replace", "change"],
}


def rewrite_query_for_evidence(
    *,
    question: str,
    filters: dict[str, str | None],
    missing: list[str],
) -> dict[str, Any]:
    additions: list[str] = []
    for term, synonyms in DOMAIN_SYNONYMS.items():
        if term in question:
            additions.extend(synonym for synonym in synonyms if synonym not in additions)

    query = " ".join([question, *additions]).strip()
    return {
        "query": query,
        "filters": dict(filters),
        "missing": list(missing),
        "reason": "expanded domain synonyms for missing evidence",
    }
