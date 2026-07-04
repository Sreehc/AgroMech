from __future__ import annotations

from sqlalchemy import Engine, or_, select, update

from agromech_api.db.enums import ChunkType
from agromech_api.db.models import document_chunks, documents, retrieval_logs


def build_citations(engine: Engine, evidence_items: list[dict[str, object]]) -> list[dict[str, object]]:
    applicable_items = [item for item in evidence_items if not item.get("not_applicable")]
    document_ids = {str(item["document_id"]) for item in applicable_items}
    with engine.connect() as connection:
        rows = connection.execute(select(documents).where(documents.c.id.in_(document_ids))).mappings().all()
    titles = {row["id"]: row["title"] for row in rows}
    citations = []
    for item in applicable_items:
        evidence_snippet = build_evidence_window(engine, item)
        citations.append(
            {
                "document_id": item["document_id"],
                "document_title": titles.get(str(item["document_id"]), "Unknown document"),
                "chunk_id": item["chunk_id"],
                "source_locator": item["source_locator"],
                "evidence_snippet": evidence_snippet,
                "evidence_type": item["chunk_type"],
                "accessible": True,
            }
        )
    return citations


def build_visual_citations(engine: Engine, evidence_items: list[dict[str, object]]) -> list[dict[str, object]]:
    document_ids = {str(item["document_id"]) for item in evidence_items}
    with engine.connect() as connection:
        rows = connection.execute(select(documents).where(documents.c.id.in_(document_ids))).mappings().all()
    titles = {row["id"]: row["title"] for row in rows}
    citations = []
    for item in evidence_items:
        citations.append(
            {
                "document_id": item["document_id"],
                "document_title": titles.get(str(item["document_id"]), "Unknown document"),
                "asset_id": item["asset_id"],
                "page_number": item["page_number"],
                "source_locator": item["source_locator"],
                "evidence_snippet": item.get("ocr_text") or item.get("visual_observation") or "",
                "evidence_type": item.get("evidence_type", "visual_page"),
                "accessible": True,
            }
        )
    return citations


def build_evidence_window(engine: Engine, evidence_item: dict[str, object]) -> str:
    chunk_type = str(evidence_item["chunk_type"])
    if chunk_type == ChunkType.TABLE.value:
        return table_evidence_window(evidence_item)
    if chunk_type == ChunkType.TEXT.value:
        return text_evidence_window(engine, evidence_item)
    return clipped_text(str(evidence_item["content"]))


def table_evidence_window(evidence_item: dict[str, object]) -> str:
    content = str(evidence_item["content"])
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return clipped_text(content)
    header = lines[0]
    data_lines = lines[1:3]
    return clipped_text("\n".join([header, *data_lines]))


def text_evidence_window(engine: Engine, evidence_item: dict[str, object]) -> str:
    source_locator = evidence_item.get("source_locator") or {}
    if not isinstance(source_locator, dict):
        return clipped_text(str(evidence_item["content"]))
    locator_type = source_locator.get("type")
    if locator_type not in {"text", "markdown", "docx"}:
        return clipped_text(str(evidence_item["content"]))

    chunk_id = str(evidence_item["chunk_id"])
    line_start = source_locator.get("line_start")
    line_end = source_locator.get("line_end")
    if not isinstance(line_start, int) or not isinstance(line_end, int):
        return clipped_text(str(evidence_item["content"]))

    with engine.connect() as connection:
        current_chunk = connection.execute(
            select(document_chunks).where(document_chunks.c.id == chunk_id)
        ).mappings().one_or_none()
        if current_chunk is None:
            return clipped_text(str(evidence_item["content"]))

        neighbor_rows = connection.execute(
            select(document_chunks)
            .where(document_chunks.c.document_id == current_chunk["document_id"])
            .where(document_chunks.c.chunk_type == ChunkType.TEXT.value)
            .where(
                or_(
                    document_chunks.c.id == chunk_id,
                    document_chunks.c.source_locator["line_end"].as_integer() == line_start - 1,
                    document_chunks.c.source_locator["line_start"].as_integer() == line_end + 1,
                )
            )
            .order_by(document_chunks.c.source_locator["line_start"].as_integer())
        ).mappings().all()

    if not neighbor_rows:
        return clipped_text(str(evidence_item["content"]))
    return clipped_text("\n".join(str(row["content"]) for row in neighbor_rows if row["content"]))


def clipped_text(content: str, *, limit: int = 360) -> str:
    return content[:limit]


def trim_retrieval_final_evidence(
    engine: Engine,
    *,
    trace_id: str,
    final_evidence: list[dict[str, object]],
) -> None:
    with engine.begin() as connection:
        connection.execute(
            update(retrieval_logs)
            .where(retrieval_logs.c.trace_id == trace_id)
            .values(final_evidence=final_evidence)
        )
