from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import Engine, delete, insert, select

from agromech_api.db.models import chunk_entity_links, document_chunks, document_entity_extractions


BRANDS = [
    "John Deere",
    "Kubota",
    "久保田",
    "Yanmar",
    "洋马",
    "Case IH",
    "New Holland",
    "Claas",
    "Fendt",
]
SYSTEM_TERMS = [
    "hydraulic",
    "液压",
    "engine",
    "发动机",
    "electrical",
    "电气",
    "transmission",
    "变速箱",
    "brake",
    "制动",
    "fuel",
    "燃油",
    "cooling",
    "冷却",
]
COMPONENT_TERMS = [
    "pump",
    "液压泵",
    "filter",
    "滤芯",
    "valve",
    "阀",
    "belt",
    "皮带",
    "sensor",
    "传感器",
    "injector",
    "喷油器",
    "battery",
    "蓄电池",
    "alternator",
    "发电机",
]
MAINTENANCE_TERMS = [
    "change engine oil",
    "更换机油",
    "replace filter",
    "更换滤芯",
    "grease fittings",
    "润滑黄油嘴",
    "lubricate",
    "润滑",
    "check oil level",
    "检查油位",
]
FAULT_CODE_RE = re.compile(r"\b(?![ML]\d{3,4}\b)[A-Z]\d{2,4}(?![A-Z0-9])\b")
PART_NUMBER_RE = re.compile(r"\b[A-Z]{2,4}-\d{2,6}\b")
MODEL_RE = re.compile(r"\b(?:[A-Z]{0,2}[-\s]?\d{1,4}[A-Z]{0,3}|[0-9][A-Z][A-Z0-9]{0,4})\b")


@dataclass(frozen=True)
class ExtractedEntity:
    entity_type: str
    value: str
    normalized_value: str
    confidence: float
    source: str = "rule"


@dataclass(frozen=True)
class EntityExtractionResult:
    link_count: int
    low_confidence: bool


class EntityExtractor:
    def extract(self, text: str, *, metadata: dict | None = None) -> list[ExtractedEntity]:
        entities: list[ExtractedEntity] = []
        entities.extend(extract_from_terms("brand", text, BRANDS, confidence=0.9))
        entities.extend(extract_from_terms("system", text, SYSTEM_TERMS, confidence=0.75))
        entities.extend(extract_from_terms("component", text, COMPONENT_TERMS, confidence=0.72))
        entities.extend(extract_from_terms("maintenance_item", text, MAINTENANCE_TERMS, confidence=0.78))
        entities.extend(regex_entities("fault_code", text, FAULT_CODE_RE, confidence=0.86))
        entities.extend(regex_entities("part_number", text, PART_NUMBER_RE, confidence=0.84))
        entities.extend(model_entities(text))
        if metadata:
            entities.extend(metadata_entities(metadata))
        return dedupe_entities(entities)


def extract_from_terms(
    entity_type: str,
    text: str,
    terms: list[str],
    *,
    confidence: float,
) -> list[ExtractedEntity]:
    lower_text = text.lower()
    return [
        entity(entity_type, term, confidence)
        for term in terms
        if term_matches(lower_text, term)
    ]


def term_matches(lower_text: str, term: str) -> bool:
    lower_term = term.lower()
    if contains_cjk(lower_term):
        return lower_term in lower_text
    return bool(re.search(rf"\b{re.escape(lower_term)}\b", lower_text))


def regex_entities(
    entity_type: str,
    text: str,
    pattern: re.Pattern[str],
    *,
    confidence: float,
) -> list[ExtractedEntity]:
    return [entity(entity_type, match.group(0), confidence) for match in pattern.finditer(text)]


def model_entities(text: str) -> list[ExtractedEntity]:
    values = []
    for match in MODEL_RE.finditer(text):
        value = match.group(0)
        normalized = re.sub(r"[-\s]+", "", value.upper())
        if FAULT_CODE_RE.fullmatch(normalized) or PART_NUMBER_RE.fullmatch(value.upper()):
            continue
        if any(char.isdigit() for char in normalized) and any(char.isalpha() for char in normalized):
            values.append(entity("model", value, 0.8))
    return values


def metadata_entities(metadata: dict) -> list[ExtractedEntity]:
    detected = metadata.get("detected_entities") if isinstance(metadata, dict) else None
    if not isinstance(detected, dict):
        return []
    entities: list[ExtractedEntity] = []
    for value in detected.get("possible_models") or []:
        entities.append(entity("model", str(value), 0.7, source="vision"))
    for value in detected.get("visible_parts") or []:
        entities.append(entity("component", str(value), 0.65, source="vision"))
    for value in detected.get("warning_lights") or []:
        entities.append(entity("warning_light", str(value), 0.65, source="vision"))
    for value in detected.get("part_numbers") or []:
        entities.append(entity("part_number", str(value), 0.7, source="vision"))
    return entities


def entity(entity_type: str, value: str, confidence: float, *, source: str = "rule") -> ExtractedEntity:
    return ExtractedEntity(
        entity_type=entity_type,
        value=value,
        normalized_value=normalize(value),
        confidence=confidence,
        source=source,
    )


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).lower()


def contains_cjk(value: str) -> bool:
    return any("\u4e00" <= character <= "\u9fff" for character in value)


def dedupe_entities(entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
    best: dict[tuple[str, str], ExtractedEntity] = {}
    for item in entities:
        key = (item.entity_type, item.normalized_value)
        if key not in best or item.confidence > best[key].confidence:
            best[key] = item
    return list(best.values())


def process_document_entities(engine: Engine, document_id: str, *, extractor: EntityExtractor | None = None) -> EntityExtractionResult:
    active_extractor = extractor or EntityExtractor()
    with engine.connect() as connection:
        chunks = connection.execute(
            select(document_chunks).where(document_chunks.c.document_id == document_id)
        ).mappings().all()

    link_rows = []
    grouped: dict[str, set[str]] = {}
    confidence_values = []
    for chunk in chunks:
        entities = active_extractor.extract(chunk["content"] or "", metadata=chunk["metadata"])
        for item in entities:
            grouped.setdefault(item.entity_type, set()).add(item.value)
            confidence_values.append(item.confidence)
            link_rows.append(
                {
                    "id": str(uuid4()),
                    "chunk_id": chunk["id"],
                    "document_id": document_id,
                    "entity_type": item.entity_type,
                    "entity_value": item.value,
                    "normalized_value": item.normalized_value,
                    "confidence": item.confidence,
                    "source": item.source,
                }
            )

    confidence = min(confidence_values) if confidence_values else 0.0
    low_confidence = not link_rows or confidence < 0.55
    extracted_entities = {
        entity_type: sorted(values)
        for entity_type, values in sorted(grouped.items())
    }
    with engine.begin() as connection:
        connection.execute(delete(chunk_entity_links).where(chunk_entity_links.c.document_id == document_id))
        connection.execute(
            delete(document_entity_extractions).where(document_entity_extractions.c.document_id == document_id)
        )
        if link_rows:
            connection.execute(insert(chunk_entity_links), link_rows)
        connection.execute(
            insert(document_entity_extractions).values(
                id=str(uuid4()),
                document_id=document_id,
                extracted_entities=extracted_entities,
                confidence=confidence,
                low_confidence=low_confidence,
            )
        )
    return EntityExtractionResult(link_count=len(link_rows), low_confidence=low_confidence)


def filter_chunks_by_entity(engine: Engine, *, entity_type: str, value: str) -> list[dict[str, str]]:
    with engine.connect() as connection:
        rows = connection.execute(
            select(chunk_entity_links.c.chunk_id, chunk_entity_links.c.document_id)
            .where(chunk_entity_links.c.entity_type == entity_type)
            .where(chunk_entity_links.c.normalized_value == normalize(value))
            .order_by(chunk_entity_links.c.chunk_id)
        ).mappings().all()
    return [{"chunk_id": row["chunk_id"], "document_id": row["document_id"]} for row in rows]
