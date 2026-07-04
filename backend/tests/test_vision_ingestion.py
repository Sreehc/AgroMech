import base64
import json
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request

import pytest
from sqlalchemy import create_engine, insert, select

from agromech_api.core.config import Settings, get_settings
from agromech_api.db.enums import AssetType, ChunkType, DocumentStatus, TaskType
from agromech_api.db.models import document_assets, document_chunks, documents, ingest_tasks, metadata
from agromech_api.ingestion import QueuedTask
from agromech_api.ingestion.vision import (
    BailianVisionReader,
    VisionModelError,
    build_visual_reader,
    process_visual_observations,
)
from agromech_worker.main import process_ingest_task, run_once


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
    source_locator: dict | None = None,
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
                source_locator=source_locator
                if source_locator is not None
                else {"type": "image", "source_file": "label.png"},
                ocr_text=ocr_text,
                visual_observation=visual_observation or {"ocr": {"status": "succeeded"}},
            )
        )


def bailian_settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "file_storage_backend": "local",
        "graph_backend": "local",
        "vector_backend": "local",
        "model_provider": "bailian",
        "embedding_provider": "local",
        "bailian_api_key": "key",
        "bailian_base_url": "https://bailian.example/compatible-mode/v1",
        "vision_model": "qwen3.7-plus",
        "vision_timeout_seconds": 12.0,
        "local_file_storage_path": "./.agromech-data/storage/files",
    }
    base.update(overrides)
    return Settings(**base)


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


def test_visual_observation_rejects_chunk_without_source_locator(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    source_path = tmp_path / "label.png"
    seed_document_with_asset(engine, source_path=source_path, source_locator={})

    result = process_visual_observations(
        engine,
        "doc-1",
        visual_reader=lambda _path, _ocr_text: {
            "description": "Dashboard warning label",
            "confidence": 0.82,
        },
    )

    assert result.success_count == 1
    assert result.chunk_count == 0
    with engine.connect() as connection:
        asset = connection.execute(select(document_assets)).mappings().one()
        chunks = connection.execute(select(document_chunks)).mappings().all()
    assert asset["visual_observation"]["vision"]["description"] == "Dashboard warning label"
    assert chunks == []


def test_bailian_vision_reader_sends_image_and_ocr_context(tmp_path) -> None:
    image_path = tmp_path / "label.png"
    image_path.write_bytes(PNG_BYTES)
    requests: list[dict] = []

    def transport(request: Request, timeout: float) -> dict:
        requests.append(
            {
                "url": request.full_url,
                "timeout": timeout,
                "headers": dict(request.header_items()),
                "payload": json.loads(request.data.decode("utf-8")),
            }
        )
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "description": "Dashboard label with oil warning",
                                "possible_models": ["M7040"],
                                "visible_parts": ["dashboard"],
                                "warning_lights": ["oil pressure"],
                                "part_numbers": ["HH-123"],
                                "confidence": 0.78,
                                "uncertainty": "model inferred from OCR",
                            }
                        )
                    }
                }
            ]
        }

    reader = BailianVisionReader(bailian_settings(), transport=transport)

    result = reader(image_path, "M7040 oil")

    assert result["description"] == "Dashboard label with oil warning"
    assert result["possible_models"] == ["M7040"]
    assert result["visible_parts"] == ["dashboard"]
    assert result["warning_lights"] == ["oil pressure"]
    assert result["part_numbers"] == ["HH-123"]
    assert result["confidence"] == 0.78
    assert result["uncertainty"] == "model inferred from OCR"
    request = requests[0]
    assert request["url"] == "https://bailian.example/compatible-mode/v1/chat/completions"
    assert request["timeout"] == 12.0
    assert request["headers"]["Authorization"] == "Bearer key"
    assert request["payload"]["model"] == "qwen3.7-plus"
    user_content = request["payload"]["messages"][1]["content"]
    assert user_content[0]["type"] == "text"
    assert "OCR text: M7040 oil" in user_content[0]["text"]
    assert user_content[1]["type"] == "image_url"
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_bailian_vision_reader_wraps_transport_errors(tmp_path) -> None:
    image_path = tmp_path / "label.png"
    image_path.write_bytes(PNG_BYTES)

    def transport(_request: Request, _timeout: float) -> dict:
        raise URLError("network down")

    reader = BailianVisionReader(bailian_settings(), transport=transport)

    with pytest.raises(VisionModelError, match="Vision request failed"):
        reader(image_path, None)


def test_build_visual_reader_selects_bailian() -> None:
    reader = build_visual_reader(bailian_settings(), transport=lambda _request, _timeout: {})

    assert isinstance(reader, BailianVisionReader)


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


def test_run_once_uses_configured_bailian_vision_reader_for_image_ingestion(tmp_path, monkeypatch) -> None:
    engine = create_test_engine(tmp_path)
    source_path = tmp_path / "label.png"
    source_path.write_bytes(PNG_BYTES)
    settings = bailian_settings(local_file_storage_path=str(tmp_path / "files"))
    get_settings.cache_clear()
    monkeypatch.setattr("agromech_worker.main.get_settings", lambda: settings)

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
                status=DocumentStatus.QUEUED.value,
                created_by_role="admin",
            )
        )
        connection.execute(
            insert(ingest_tasks).values(
                id="task-1",
                document_id="doc-1",
                task_type=TaskType.INGEST.value,
                status="queued",
                attempt_count=0,
                stage="queued",
            )
        )

    def transport(_request: Request, _timeout: float) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "description": "Visible hydraulic filter housing",
                                "possible_models": ["M7040"],
                                "visible_parts": ["hydraulic filter"],
                                "confidence": 0.8,
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(
        "agromech_worker.main.build_visual_reader",
        lambda _settings: BailianVisionReader(settings, transport=transport),
    )

    result = run_once(engine=engine)

    assert result == "succeeded"
    with engine.connect() as connection:
        chunk = connection.execute(select(document_chunks)).mappings().one()
        asset = connection.execute(select(document_assets)).mappings().one()
    assert chunk["chunk_type"] == ChunkType.IMAGE.value
    assert "Visible hydraulic filter housing" in chunk["content"]
    assert asset["visual_observation"]["vision"]["description"] == "Visible hydraulic filter housing"
