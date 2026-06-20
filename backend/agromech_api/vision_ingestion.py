from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine, insert, select, update

from agromech_api.db.enums import AssetType, ChunkType
from agromech_api.db.models import document_assets, document_chunks
from agromech_api.text_ingestion import local_file_path


VisualReader = Callable[[Path, str | None], dict[str, object]]


@dataclass(frozen=True)
class VisualIngestionResult:
    asset_count: int
    success_count: int
    failure_count: int
    chunk_count: int


def process_visual_observations(
    engine: Engine,
    document_id: str,
    *,
    visual_reader: VisualReader | None = None,
    confidence_threshold: float = 0.55,
) -> VisualIngestionResult:
    reader = visual_reader or default_visual_reader
    with engine.connect() as connection:
        assets = connection.execute(
            select(document_assets)
            .where(document_assets.c.document_id == document_id)
            .where(
                document_assets.c.asset_type.in_(
                    [
                        AssetType.PAGE_IMAGE.value,
                        AssetType.SOURCE_IMAGE.value,
                        AssetType.EXTRACTED_IMAGE.value,
                    ]
                )
            )
            .order_by(document_assets.c.created_at)
        ).mappings().all()

    success_count = 0
    failure_count = 0
    chunk_count = 0
    for asset in assets:
        path = local_file_path(asset["storage_uri"])
        existing_observation = dict(asset["visual_observation"] or {})
        existing_observation["ocr_text"] = asset["ocr_text"]

        try:
            raw_observation = reader(path, asset["ocr_text"])
        except Exception as exc:
            failure_count += 1
            existing_observation["vision"] = {
                "status": "failed",
                "service": "vision",
                "error_code": "vision_model_unavailable",
                "error_message": str(exc),
            }
            with engine.begin() as connection:
                connection.execute(
                    update(document_assets)
                    .where(document_assets.c.id == asset["id"])
                    .values(visual_observation=existing_observation)
                )
            continue

        vision_observation = normalize_visual_observation(raw_observation, confidence_threshold)
        existing_observation["vision"] = vision_observation
        success_count += 1
        chunk_content = image_chunk_content(asset["ocr_text"], vision_observation)
        with engine.begin() as connection:
            connection.execute(
                update(document_assets)
                .where(document_assets.c.id == asset["id"])
                .values(visual_observation=existing_observation)
            )
            if chunk_content.strip():
                upsert_image_chunk(connection, asset, chunk_content, vision_observation)
                chunk_count += 1

    return VisualIngestionResult(
        asset_count=len(assets),
        success_count=success_count,
        failure_count=failure_count,
        chunk_count=chunk_count,
    )


def default_visual_reader(path: Path, ocr_text: str | None) -> dict[str, object]:
    raise RuntimeError("Vision model is not configured")


def normalize_visual_observation(
    raw: dict[str, object],
    confidence_threshold: float,
) -> dict[str, object]:
    confidence = float(raw.get("confidence") or 0.0)
    low_confidence = bool(raw.get("low_confidence")) or confidence < confidence_threshold
    return {
        "status": "succeeded",
        "description": str(raw.get("description") or ""),
        "possible_models": list(raw.get("possible_models") or []),
        "visible_parts": list(raw.get("visible_parts") or []),
        "warning_lights": list(raw.get("warning_lights") or []),
        "part_numbers": list(raw.get("part_numbers") or []),
        "confidence": confidence,
        "low_confidence": low_confidence,
    }


def image_chunk_content(ocr_text: str | None, vision: dict[str, object]) -> str:
    parts = []
    if ocr_text:
        parts.append(f"OCR text:\n{ocr_text}")
    if vision["description"]:
        parts.append(f"Visual description:\n{vision['description']}")
    if vision["possible_models"]:
        parts.append("Possible models: " + ", ".join(vision["possible_models"]))
    if vision["visible_parts"]:
        parts.append("Visible parts: " + ", ".join(vision["visible_parts"]))
    if vision["warning_lights"]:
        parts.append("Warning lights: " + ", ".join(vision["warning_lights"]))
    if vision["part_numbers"]:
        parts.append("Part numbers: " + ", ".join(vision["part_numbers"]))
    return "\n\n".join(parts)


def upsert_image_chunk(connection, asset, content: str, vision: dict[str, object]) -> None:
    existing_chunk_id = connection.execute(
        select(document_chunks.c.id).where(document_chunks.c.asset_id == asset["id"])
    ).scalar_one_or_none()
    metadata = {
        "detected_entities": {
            "possible_models": vision["possible_models"],
            "visible_parts": vision["visible_parts"],
            "warning_lights": vision["warning_lights"],
            "part_numbers": vision["part_numbers"],
        },
        "visual_confidence": vision["confidence"],
        "low_confidence": vision["low_confidence"],
    }
    values = {
        "document_id": asset["document_id"],
        "asset_id": asset["id"],
        "chunk_type": ChunkType.IMAGE.value,
        "content": content,
        "summary": content[:240],
        "page_number": asset["page_number"],
        "source_locator": asset["source_locator"],
        "metadata": metadata,
    }
    if existing_chunk_id:
        connection.execute(
            update(document_chunks)
            .where(document_chunks.c.id == existing_chunk_id)
            .values(**values)
        )
    else:
        connection.execute(
            insert(document_chunks).values(id=str(uuid4()), **values)
        )
