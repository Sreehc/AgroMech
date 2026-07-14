from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import exists, or_, select

from agromech_api.db.enums import DocumentStatus
from agromech_api.db.models import chunk_entity_links, documents
from agromech_api.domain.entities import normalize


@dataclass(frozen=True)
class RetrievalFilters:
    viewer_user_id: str | None
    brand: str | None = None
    model: str | None = None
    document_type: str | None = None
    language: str | None = None
    document_version: str | None = None
    subsystem: str | None = None

    def as_trace(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "brand": self.brand,
                "model": self.model,
                "document_type": self.document_type,
                "language": self.language,
                "document_version": self.document_version,
                "subsystem": self.subsystem,
            }.items()
            if value is not None
        }


def build_retrieval_filters(
    *, request_filters: dict[str, str | None], viewer_user_id: str | None
) -> RetrievalFilters:
    values = {
        key: normalized_filter(request_filters.get(key))
        for key in RetrievalFilters.__dataclass_fields__
        if key != "viewer_user_id"
    }
    if values["subsystem"] is not None:
        values["subsystem"] = normalize(values["subsystem"])
    return RetrievalFilters(viewer_user_id=viewer_user_id, **values)


def normalized_filter(value: str | None) -> str | None:
    return value.strip() if value and value.strip() else None


def document_filter_conditions(filters: RetrievalFilters) -> list[object]:
    visibility = documents.c.visibility == "public"
    if filters.viewer_user_id is not None:
        visibility = or_(visibility, documents.c.owner_user_id == filters.viewer_user_id)
    conditions: list[object] = [
        documents.c.status == DocumentStatus.INDEXED.value,
        documents.c.deleted_at.is_(None),
        visibility,
    ]
    for field in ("brand", "model", "document_type", "language", "document_version"):
        value = getattr(filters, field)
        if value is not None:
            conditions.append(getattr(documents.c, field) == value)
    return conditions


def chunk_filter_conditions(chunk_id_column, filters: RetrievalFilters) -> list[object]:
    if filters.subsystem is None:
        return []
    return [
        exists(
            select(chunk_entity_links.c.id).where(
                chunk_entity_links.c.chunk_id == chunk_id_column,
                chunk_entity_links.c.entity_type == "system",
                chunk_entity_links.c.normalized_value == normalize(filters.subsystem),
            )
        )
    ]
