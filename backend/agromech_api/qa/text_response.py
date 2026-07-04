from __future__ import annotations


def uncertainty_payload(scope_uncertain: bool, citations: list[dict[str, object]]) -> dict[str, object]:
    reasons = []
    document_ids = {citation["document_id"] for citation in citations}
    if scope_uncertain:
        reasons.append("scope_uncertain")
    if len(document_ids) > 1:
        reasons.append("multiple_sources")
    return {"level": "medium" if reasons else "low", "reasons": reasons}


def evidence_is_safety_sensitive(citations: list[dict[str, object]]) -> bool:
    safety_terms = ("液压", "hydraulic", "电气", "发动机", "制动", "rotating", "旋转")
    text = " ".join(str(citation["evidence_snippet"]).lower() for citation in citations)
    return any(term in text for term in safety_terms)


def conclusion_from_citation(citation: dict[str, object]) -> str:
    return f"根据来源证据，相关资料片段为：{citation['evidence_snippet']}"


def applicability_section(filters: dict[str, object], citations: list[dict[str, object]]) -> str:
    model = filters.get("model")
    if model:
        return f"适用范围优先限定为 {model}，以引用资料为准。"
    document_titles = sorted({str(citation["document_title"]) for citation in citations})
    return f"适用范围需结合来源资料确认：{', '.join(document_titles)}。"


def possible_causes_section(citations: list[dict[str, object]]) -> list[str]:
    return [str(citation["evidence_snippet"]) for citation in citations[:3]]


def inspection_steps_section(citations: list[dict[str, object]]) -> list[str]:
    return [f"核对引用 {index} 的来源定位和原文内容。" for index, _citation in enumerate(citations[:3], start=1)]


def citation_section(citations: list[dict[str, object]]) -> list[str]:
    rendered = []
    for citation in citations:
        if citation.get("chunk_id"):
            rendered.append(f"{citation['document_title']} / {citation['chunk_id']}")
        elif citation.get("asset_id"):
            rendered.append(f"{citation['document_title']} / {citation['asset_id']} / p.{citation.get('page_number')}")
    return rendered


def compose_answer(sections: dict[str, object]) -> str:
    lines = [str(sections["conclusion"]), str(sections["applicability"])]
    safety_reminder = sections.get("safety_reminder") or []
    if safety_reminder:
        lines.extend(str(item) for item in safety_reminder)
    lines.append("以上结论仅基于当前检索到的来源证据。")
    return "\n".join(lines)

