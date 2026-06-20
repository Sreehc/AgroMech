from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import Engine, select

from agromech_api.db.models import chunk_entity_links
from agromech_api.entity_extraction import BRANDS, COMPONENT_TERMS, SYSTEM_TERMS, EntityExtractor, normalize


MODEL_ALIAS_RE = re.compile(r"\b([A-Z]{0,2})[-\s]?(\d{3,4})([A-Z]{0,3})\b", re.IGNORECASE)
FAULT_CODE_RE = re.compile(r"\b[A-Z]\d{2,4}\b", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedQuery:
    original_query: str
    intent: str
    filters: dict[str, object]
    entities: dict[str, list[str]]
    safety_sensitive: bool
    scope_uncertain: bool
    needs_clarification: list[str]
    multi_model: bool


def parse_query(query: str) -> ParsedQuery:
    extractor = EntityExtractor()
    entities = extractor.extract(query)
    grouped: dict[str, list[str]] = {}
    for item in entities:
        grouped.setdefault(item.entity_type, [])
        if item.value not in grouped[item.entity_type]:
            grouped[item.entity_type].append(item.value)

    models = normalized_models(query)
    if models:
        grouped["model"] = models
    fault_codes = [code.upper() for code in FAULT_CODE_RE.findall(query) if code.upper() not in models]
    if fault_codes:
        grouped["fault_code"] = list(dict.fromkeys(fault_codes))

    filters: dict[str, object] = {}
    brand = first_matching_term(query, BRANDS)
    if brand:
        filters["brand"] = brand
    if len(models) == 1:
        filters["model"] = models[0]
    elif len(models) > 1:
        filters["models"] = models
    subsystem = normalized_subsystem(query)
    if subsystem:
        filters["subsystem"] = subsystem

    multi_model = len(models) > 1
    has_fault_code = bool(grouped.get("fault_code"))
    scope_uncertain = (has_fault_code and not models) or multi_model
    needs_clarification = []
    if has_fault_code and not models:
        needs_clarification.append("model")
    if multi_model:
        needs_clarification.append("separate_models")

    return ParsedQuery(
        original_query=query,
        intent=infer_intent(query),
        filters=filters,
        entities=grouped,
        safety_sensitive=contains_safety_sensitive_terms(query),
        scope_uncertain=scope_uncertain,
        needs_clarification=needs_clarification,
        multi_model=multi_model,
    )


def structured_filter_chunks(engine: Engine, parsed: ParsedQuery) -> list[dict[str, object]]:
    required_filters: list[tuple[str, str]] = []
    if model := parsed.filters.get("model"):
        required_filters.append(("model", str(model)))
    if fault_codes := parsed.entities.get("fault_code"):
        required_filters.append(("fault_code", fault_codes[0]))

    if not required_filters:
        return []

    with engine.connect() as connection:
        rows = connection.execute(select(chunk_entity_links)).mappings().all()

    chunk_matches: dict[str, dict[str, object]] = {}
    for row in rows:
        for entity_type, value in required_filters:
            if row["entity_type"] == entity_type and row["normalized_value"] == normalize(value):
                item = chunk_matches.setdefault(
                    row["chunk_id"],
                    {
                        "chunk_id": row["chunk_id"],
                        "document_id": row["document_id"],
                        "matched_filters": [],
                    },
                )
                if entity_type not in item["matched_filters"]:
                    item["matched_filters"].append(entity_type)

    if parsed.filters.get("model"):
        required = {"model"}
        if parsed.entities.get("fault_code"):
            required.add("fault_code")
        results = [
            result
            for result in chunk_matches.values()
            if required.issubset(set(result["matched_filters"]))
        ]
    else:
        results = list(chunk_matches.values())

    for result in results:
        if parsed.scope_uncertain:
            result["scope_uncertain"] = True
    return sorted(results, key=lambda item: item["chunk_id"])


def normalized_models(query: str) -> list[str]:
    values = []
    for prefix, number, suffix in MODEL_ALIAS_RE.findall(query):
        value = f"{prefix}{number}{suffix}".upper()
        if value in {"E01", "E02"}:
            continue
        if value not in values:
            values.append(value)
    return values


def first_matching_term(query: str, terms: list[str]) -> str | None:
    lower_query = query.lower()
    for term in terms:
        if re.search(rf"\b{re.escape(term.lower())}\b", lower_query):
            return term
    return None


def normalized_subsystem(query: str) -> str | None:
    lower_query = query.lower()
    aliases = {
        "液压": "hydraulic",
        "发动机": "engine",
        "电气": "electrical",
        "制动": "brake",
    }
    for alias, value in aliases.items():
        if alias in query:
            return value
    for term in SYSTEM_TERMS:
        if re.search(rf"\b{re.escape(term.lower())}\b", lower_query):
            return term
    return None


def infer_intent(query: str) -> str:
    lower_query = query.lower()
    if any(term in lower_query for term in ["怎么修", "repair", "fix", "处理"]):
        return "repair"
    if any(term in lower_query for term in ["保养", "maintenance", "interval"]):
        return "maintenance"
    if any(term in lower_query for term in ["什么意思", "meaning", "含义"]):
        return "diagnose"
    return "lookup"


def contains_safety_sensitive_terms(query: str) -> bool:
    lower_query = query.lower()
    return any(term in lower_query for term in ["hydraulic", "液压", "engine", "发动机", "brake", "制动", "electrical", "电气", "旋转"])
