import base64
from pathlib import Path

from sqlalchemy import create_engine, insert, select

from agromech_api.db.enums import AssetType, ChunkType, DocumentStatus, TaskType
from agromech_api.db.models import document_assets, document_chunks, documents, metadata
from agromech_api.ingestion import QueuedTask
from agromech_api.vision_ingestion import process_visual_observations
from agromech_worker.main import process_ingest_task


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/axw"
    "nJkAAAAASUVORK5CYII="
)


def create_test_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    return engine


def seed_document_with_asset(
    engine,
    *,
    source_path: Path,
    ocr_text: str | None = "Oil pressure warning",
    visual_observation: dict | None = None,
) -> None:
    source_path.write_bytes(PNG_BYTES)
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id="doc-1",
                title="Label",
                original_file_name="label.png",
                file_hash="hash-doc-1",
                file_size_bytes=source_path.stat().st_size,
                mime_type="image/png",
                storage_uri=f"file://{source_path}",
                status=DocumentStatus.PROCESSING.value,
                created_by_role="admin",
            )
        )
        connection.execute(
            insert(document_assets).values(
                id="asset-1",
                document_id="doc-1",
                asset_type=AssetType.SOURCE_IMAGE.value,
                storage_uri=f"file://{source_path}",
                mime_type="image/png",
                source_locator={"type": "image", "source_file": "label.png"},
                ocr_text=ocr_text,
                visual_observation=visual_observation or {"ocr": {"status": "succeeded"}},
            )
        )


def test_visual_observation_updates_asset_and_image_chunk(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    source_path = tmp_path / "label.png"
    seed_document_with_asset(engine, source_path=source_path)

    result = process_visual_observations(
        engine,
        "doc-1",
        visual_reader=lambda _path, _ocr_text: {
            "description": "Dashboard warning label on hydraulic panel",
            "possible_models": ["M7040"],
            "visible_parts": ["hydraulic panel"],
            "warning_lights": ["oil pressure"],
            "part_numbers": ["HH-123"],
            "confidence": 0.82,
        },
        confidence_threshold=0.55,
    )

    assert result.success_count == 1
    assert result.chunk_count == 1
    with engine.connect() as connection:
        asset = connection.execute(select(document_assets)).mappings().one()
        chunk = connection.execute(select(document_chunks)).mappings().one()
    assert asset["visual_observation"]["ocr_text"] == "Oil pressure warning"
    assert asset["visual_observation"]["vision"] == {
        "status": "succeeded",
        "description": "Dashboard warning label on hydraulic panel",
        "possible_models": ["M7040"],
        "visible_parts": ["hydraulic panel"],
        "warning_lights": ["oil pressure"],
        "part_numbers": ["HH-123"],
        "confidence": 0.82,
        "low_confidence": False,
    }
    assert chunk["chunk_type"] == ChunkType.IMAGE.value
    assert "Oil pressure warning" in chunk["content"]
    assert "M7040" in chunk["content"]
    assert chunk["metadata"]["detected_entities"]["possible_models"] == ["M7040"]


def test_visual_model_unavailable_records_service_error(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    source_path = tmp_path / "label.png"
    seed_document_with_asset(engine, source_path=source_path)

    def unavailable(_path: Path, _ocr_text: str | None) -> dict:
        raise RuntimeError("Vision model unavailable")

    result = process_visual_observations(engine, "doc-1", visual_reader=unavailable)

    assert result.failure_count == 1
    with engine.connect() as connection:
        asset = connection.execute(select(document_assets)).mappings().one()
    assert asset["visual_observation"]["vision"] == {
        "status": "failed",
        "service": "vision",
        "error_code": "vision_model_unavailable",
        "error_message": "Vision model unavailable",
    }


def test_worker_image_ingestion_continues_when_ocr_fails_but_vision_succeeds(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    source_path = tmp_path / "label.png"
    source_path.write_bytes(PNG_BYTES)
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id="doc-1",
                title="Label",
                original_file_name="label.png",
                file_hash="hash-doc-1",
                file_size_bytes=source_path.stat().st_size,
                mime_type="image/png",
                storage_uri=f"file://{source_path}",
                status=DocumentStatus.PROCESSING.value,
                created_by_role="admin",
            )
        )

    def fail_ocr(_path: Path) -> str:
        raise RuntimeError("OCR offline")

    process_ingest_task(
        engine,
        QueuedTask(
            id="task-1",
            document_id="doc-1",
            task_type=TaskType.INGEST.value,
            attempt_count=0,
            stage="processing",
        ),
        ocr_reader=fail_ocr,
        visual_reader=lambda _path, _ocr_text: {
            "description": "Visible John Deere hydraulic filter",
            "possible_models": ["6M"],
            "visible_parts": ["hydraulic filter"],
            "confidence": 0.74,
        },
    )

    with engine.connect() as connection:
        asset = connection.execute(select(document_assets)).mappings().one()
        chunk = connection.execute(select(document_chunks)).mappings().one()
    assert asset["visual_observation"]["ocr"]["status"] == "failed"
    assert asset["visual_observation"]["vision"]["status"] == "succeeded"
    assert chunk["chunk_type"] == ChunkType.IMAGE.value
    assert "hydraulic filter" in chunk["content"]
