from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import Engine, select

from agromech_api.db.models import chunk_entity_links, documents
from agromech_api.domain.entities import BRANDS, COMPONENT_TERMS, SYSTEM_TERMS, EntityExtractor, normalize
from agromech_api.domain.model_aliases import resolve_models


MODEL_ALIAS_RE = re.compile(r"\b([A-Z]{0,2})[-\s]?(\d{3,4})([A-Z]{0,3})\b", re.IGNORECASE)
FAULT_CODE_RE = re.compile(r"\b[A-Z]\d{2,4}\b", re.IGNORECASE)
PART_NUMBER_RE = re.compile(r"\b[A-Z]{2,4}-\d{2,6}\b", re.IGNORECASE)
LANGUAGE_RE = re.compile(r"\b[a-z]{2}(?:-[A-Z]{2})\b")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
DOCUMENT_TYPE_TERMS = {
    "manual",
    "repair_manual",
    "operator_manual",
    "parts_catalog",
    "maintenance_manual",
    "service_manual",
}


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


def parse_query(query: str, *, engine: Engine | None = None) -> ParsedQuery:
    extractor = EntityExtractor()
    entities = extractor.extract(query)
    grouped: dict[str, list[str]] = {}
    for item in entities:
        grouped.setdefault(item.entity_type, [])
        if item.value not in grouped[item.entity_type]:
            grouped[item.entity_type].append(item.value)

    models = normalized_models(query, engine=engine)
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
    elif systems := grouped.get("system"):
        filters["subsystem"] = normalize_system(systems[0])
    if components := grouped.get("component"):
        filters["component"] = components[0]
    if part_numbers := grouped.get("part_number"):
        filters["part_number"] = part_numbers[0]
    if document_type := first_document_type(query):
        filters["document_type"] = document_type
    if language := first_language(query):
        filters["language"] = language
    if document_version := first_document_version(query):
        filters["document_version"] = document_version

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
    entity_filters: list[tuple[str, str, str]] = []
    if model := parsed.filters.get("model"):
        entity_filters.append(("model", "model", str(model)))
    if subsystem := parsed.filters.get("subsystem"):
        entity_filters.append(("subsystem", "system", str(subsystem)))
    if component := parsed.filters.get("component"):
        entity_filters.append(("component", "component", str(component)))
    if fault_codes := parsed.entities.get("fault_code"):
        entity_filters.append(("fault_code", "fault_code", fault_codes[0]))
    if part_numbers := parsed.entities.get("part_number"):
        entity_filters.append(("part_number", "part_number", part_numbers[0]))
    metadata_filters = [
        (key, str(parsed.filters[key]))
        for key in ["brand", "document_type", "language", "document_version"]
        if parsed.filters.get(key)
    ]

    if not entity_filters and not metadata_filters:
        return []

    with engine.connect() as connection:
        rows = connection.execute(select(chunk_entity_links)).mappings().all()
        document_rows = connection.execute(select(documents)).mappings().all()

    chunk_matches: dict[str, dict[str, object]] = {}
    for row in rows:
        for filter_name, entity_type, value in entity_filters:
            if row["entity_type"] == entity_type and row["normalized_value"] == normalize(value):
                item = chunk_matches.setdefault(
                    row["chunk_id"],
                    {
                        "chunk_id": row["chunk_id"],
                        "document_id": row["document_id"],
                        "matched_filters": [],
                    },
                )
                if filter_name not in item["matched_filters"]:
                    item["matched_filters"].append(filter_name)

    if metadata_filters:
        documents_by_id = {row["id"]: row for row in document_rows}
        chunk_ids = set(chunk_matches) if entity_filters else {
            row["chunk_id"] for row in rows
        }
        if not entity_filters:
            # Metadata-only structured filters need chunks as candidates; entity
            # links provide chunk/document membership without adding entity hits.
            for row in rows:
                chunk_matches.setdefault(
                    row["chunk_id"],
                    {
                        "chunk_id": row["chunk_id"],
                        "document_id": row["document_id"],
                        "matched_filters": [],
                    },
                )
        for chunk_id in list(chunk_ids):
            item = chunk_matches.get(chunk_id)
            if item is None:
                continue
            document = documents_by_id.get(item["document_id"])
            if document is None or not metadata_matches(document, metadata_filters):
                chunk_matches.pop(chunk_id, None)
                continue
            for key, _value in metadata_filters:
                if key not in item["matched_filters"]:
                    item["matched_filters"].append(key)

    required = required_filter_names(entity_filters, metadata_filters, parsed)
    results = [
        result
        for result in chunk_matches.values()
        if required.issubset(set(result["matched_filters"]))
    ]

    for result in results:
        result["matched_filters"] = sort_matched_filters(result["matched_filters"])
        if parsed.scope_uncertain:
            result["scope_uncertain"] = True
    return sorted(results, key=lambda item: item["chunk_id"])


def required_filter_names(
    entity_filters: list[tuple[str, str, str]],
    metadata_filters: list[tuple[str, str]],
    parsed: ParsedQuery,
) -> set[str]:
    required = {key for key, _value in metadata_filters}
    if parsed.filters.get("model"):
        required.add("model")
    if parsed.filters.get("subsystem"):
        required.add("subsystem")
    if parsed.filters.get("component"):
        required.add("component")
    if parsed.entities.get("fault_code"):
        required.add("fault_code")
    if parsed.entities.get("part_number"):
        required.add("part_number")
    if not required:
        required = {key for key, _entity_type, _value in entity_filters}
    return required


def sort_matched_filters(values: list[str]) -> list[str]:
    order = [
        "brand",
        "model",
        "document_type",
        "language",
        "document_version",
        "subsystem",
        "component",
        "fault_code",
        "part_number",
    ]
    return sorted(values, key=lambda value: order.index(value) if value in order else len(order))


def metadata_matches(document, filters: list[tuple[str, str]]) -> bool:
    for key, value in filters:
        actual = document[key]
        if actual is None or normalize(str(actual)) != normalize(value):
            return False
    return True


def normalized_models(query: str, *, engine: Engine | None = None) -> list[str]:
    values = []
    for match in MODEL_ALIAS_RE.finditer(query):
        raw_value = match.group(0).upper()
        if PART_NUMBER_RE.fullmatch(raw_value) or is_language_fragment_model(raw_value, query, match):
            continue
        prefix, number, suffix = match.groups()
        value = f"{prefix}{number}{suffix}".upper()
        if value in {"E01", "E02"}:
            continue
        if value not in values:
            values.append(value)
    # When an engine is available, resolve through the manual alias table so
    # human-curated aliases map to a canonical model. With an empty alias table
    # (the common case) this returns the same rule-normalized values.
    if engine is not None and values:
        return resolve_models(engine, values)
    return values


def is_language_fragment_model(raw_value: str, query: str, match: re.Match[str]) -> bool:
    # Avoid treating the "CN 2024" portion of "zh-CN 2024" as a tractor model.
    prefix, number, _suffix = match.groups()
    if prefix.upper() in {"CN", "US", "GB"} and YEAR_RE.fullmatch(number):
        return True
    return any(raw_value in language_context.upper() for language_context in LANGUAGE_RE.findall(query))


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


def first_document_type(query: str) -> str | None:
    lower_query = query.lower()
    for value in sorted(DOCUMENT_TYPE_TERMS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(value)}\b", lower_query):
            return value
    return None


def first_language(query: str) -> str | None:
    match = LANGUAGE_RE.search(query)
    return match.group(0) if match else None


def first_document_version(query: str) -> str | None:
    match = YEAR_RE.search(query)
    return match.group(0) if match else None


def normalize_system(value: str) -> str:
    aliases = {
        "液压": "hydraulic",
        "发动机": "engine",
        "电气": "electrical",
        "制动": "brake",
        "燃油": "fuel",
        "冷却": "cooling",
        "变速箱": "transmission",
    }
    return aliases.get(value, value)


def infer_intent(query: str) -> str:
    lower_query = query.lower()
    if any(term in lower_query for term in ["配件", "part", "part number", "适用", "applies to", "fit"]):
        return "part_lookup"
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
