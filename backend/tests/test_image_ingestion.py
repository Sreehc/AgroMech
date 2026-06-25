import base64
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, select

from agromech_api.db.enums import AssetType, ChunkType, DocumentStatus, TaskType
from agromech_api.db.models import document_assets, document_chunks, documents, metadata
from agromech_api.image_ingestion import (
    ImageAssetCandidate,
    OcrUnavailable,
    replace_image_assets_and_chunks,
    process_image_document,
)
from agromech_api.ingestion import IngestFailure, QueuedTask
from agromech_worker.main import process_ingest_task


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/axw"
    "nJkAAAAASUVORK5CYII="
)


def create_test_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    return engine


def seed_document(
    engine,
    *,
    document_id: str = "doc-1",
    filename: str,
    mime_type: str,
    source_path: Path,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id=document_id,
                title=Path(filename).stem,
                original_file_name=filename,
                file_hash=f"hash-{document_id}",
                file_size_bytes=source_path.stat().st_size,
                mime_type=mime_type,
                storage_uri=f"file://{source_path}",
                status=DocumentStatus.PROCESSING.value,
                created_by_role="admin",
            )
        )


def write_minimal_pdf(path: Path, text: str) -> None:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream.encode('utf-8'))} >>\nstream\n{stream}\nendstream".encode("utf-8"),
    ]
    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(content))
        content.extend(f"{index} 0 obj\n".encode("ascii"))
        content.extend(obj)
        content.extend(b"\nendobj\n")
    xref_start = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    content.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(
        f"trailer << /Root 1 0 R /Size {len(objects) + 1} >>\n"
        f"startxref\n{xref_start}\n%%EOF\n".encode("ascii")
    )
    path.write_bytes(bytes(content))


def test_image_document_ocr_success_creates_asset_and_image_chunk(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    source_path = tmp_path / "label.png"
    source_path.write_bytes(PNG_BYTES)
    seed_document(engine, filename="label.png", mime_type="image/png", source_path=source_path)

    result = process_image_document(engine, "doc-1", ocr_reader=lambda _path: "Model M7040 warning label")

    assert result.asset_count == 1
    assert result.chunk_count == 1
    with engine.connect() as connection:
        asset = connection.execute(select(document_assets)).mappings().one()
        chunk = connection.execute(select(document_chunks)).mappings().one()
    assert asset["asset_type"] == AssetType.SOURCE_IMAGE.value
    assert asset["storage_uri"] == f"file://{source_path}"
    assert asset["page_number"] is None
    assert asset["source_locator"] == {"type": "image", "source_file": "label.png"}
    assert asset["ocr_text"] == "Model M7040 warning label"
    assert chunk["asset_id"] == asset["id"]
    assert chunk["chunk_type"] == ChunkType.IMAGE.value
    assert chunk["content"] == "Model M7040 warning label"
    assert chunk["source_locator"] == {"type": "image", "source_file": "label.png"}


def test_pdf_pages_are_rendered_to_page_assets_with_page_locator(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    source_path = tmp_path / "manual.pdf"
    write_minimal_pdf(source_path, "Hydraulic diagram")
    seed_document(engine, filename="manual.pdf", mime_type="application/pdf", source_path=source_path)

    result = process_image_document(
        engine,
        "doc-1",
        ocr_reader=lambda _path: "Hydraulic diagram OCR",
        asset_root=tmp_path / "files",
    )

    assert result.asset_count == 1
    assert result.chunk_count == 1
    with engine.connect() as connection:
        asset = connection.execute(select(document_assets)).mappings().one()
        chunk = connection.execute(select(document_chunks)).mappings().one()
    rendered_path = Path(asset["storage_uri"].replace("file://", ""))
    assert rendered_path.exists()
    assert rendered_path.suffix == ".png"
    assert rendered_path.is_relative_to(tmp_path / "files")
    assert asset["asset_type"] == AssetType.PAGE_IMAGE.value
    assert asset["page_number"] == 1
    assert asset["source_locator"] == {"type": "pdf_page", "source_file": "manual.pdf", "page": 1}
    assert chunk["page_number"] == 1


def test_ocr_failure_records_asset_failure_and_does_not_create_image_chunk(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    source_path = tmp_path / "label.png"
    source_path.write_bytes(PNG_BYTES)
    seed_document(engine, filename="label.png", mime_type="image/png", source_path=source_path)

    def fail_ocr(_path: Path) -> str:
        raise RuntimeError("OCR service unavailable")

    with pytest.raises(IngestFailure) as exc:
        process_image_document(engine, "doc-1", ocr_reader=fail_ocr)

    assert exc.value.code == "ocr_failed"
    assert exc.value.stage == "ocr"
    with engine.connect() as connection:
        asset = connection.execute(select(document_assets)).mappings().one()
        chunk_count = connection.execute(select(document_chunks)).mappings().all()
    assert asset["ocr_text"] is None
    assert asset["visual_observation"] == {
        "ocr": {
            "status": "failed",
            "error_code": "ocr_failed",
            "error_message": "OCR service unavailable",
        }
    }
    assert chunk_count == []


def test_ocr_unavailable_records_specific_error_code(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    source_path = tmp_path / "label.png"
    source_path.write_bytes(PNG_BYTES)
    seed_document(engine, filename="label.png", mime_type="image/png", source_path=source_path)

    def unavailable_ocr(_path: Path) -> str:
        raise OcrUnavailable("PaddleOCR is not installed")

    with pytest.raises(IngestFailure) as exc:
        process_image_document(engine, "doc-1", ocr_reader=unavailable_ocr)

    assert exc.value.code == "ocr_failed"
    assert exc.value.stage == "ocr"
    with engine.connect() as connection:
        asset = connection.execute(select(document_assets)).mappings().one()
        chunks = connection.execute(select(document_chunks)).mappings().all()
    assert asset["ocr_text"] is None
    assert asset["visual_observation"] == {
        "ocr": {
            "status": "failed",
            "error_code": "ocr_unavailable",
            "error_message": "PaddleOCR is not installed",
        }
    }
    assert chunks == []


def test_replace_image_assets_rejects_chunk_without_source_locator(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    source_path = tmp_path / "label.png"
    source_path.write_bytes(PNG_BYTES)
    seed_document(engine, filename="label.png", mime_type="image/png", source_path=source_path)

    result = replace_image_assets_and_chunks(
        engine,
        "doc-1",
        [
            ImageAssetCandidate(
                asset_type=AssetType.SOURCE_IMAGE.value,
                storage_uri=f"file://{source_path}",
                mime_type="image/png",
                source_locator={},
                ocr_text="M7040 serial plate",
            )
        ],
    )

    assert result.asset_count == 1
    assert result.chunk_count == 0
    with engine.connect() as connection:
        asset = connection.execute(select(document_assets)).mappings().one()
        chunks = connection.execute(select(document_chunks)).mappings().all()
    assert asset["source_locator"] == {}
    assert chunks == []


def test_worker_default_processor_routes_image_documents_to_image_ingestion(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    source_path = tmp_path / "label.png"
    source_path.write_bytes(PNG_BYTES)
    seed_document(engine, filename="label.png", mime_type="image/png", source_path=source_path)

    process_ingest_task(
        engine,
        QueuedTask(
            id="task-1",
            document_id="doc-1",
            task_type=TaskType.INGEST.value,
            attempt_count=0,
            stage="processing",
        ),
        ocr_reader=lambda _path: "Serial plate text",
    )

    with engine.connect() as connection:
        chunk = connection.execute(select(document_chunks)).mappings().one()
    assert chunk["chunk_type"] == ChunkType.IMAGE.value
    assert chunk["content"] == "Serial plate text"


def test_worker_pdf_text_ingestion_records_ocr_failure_without_blocking_text_chunk(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    source_path = tmp_path / "manual.pdf"
    write_minimal_pdf(source_path, "Engine oil interval")
    seed_document(engine, filename="manual.pdf", mime_type="application/pdf", source_path=source_path)

    def fail_ocr(_path: Path) -> str:
        raise RuntimeError("OCR runtime failed")

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
    )

    with engine.connect() as connection:
        chunks = connection.execute(select(document_chunks)).mappings().all()
        asset = connection.execute(select(document_assets)).mappings().one()
    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == ChunkType.TEXT.value
    assert chunks[0]["content"] == "Engine oil interval"
    assert asset["visual_observation"]["ocr"]["status"] == "failed"
    assert asset["visual_observation"]["ocr"]["error_code"] == "ocr_failed"


def test_worker_pdf_text_ingestion_records_ocr_unavailable_without_blocking_text_chunk(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    source_path = tmp_path / "manual.pdf"
    write_minimal_pdf(source_path, "Engine oil interval")
    seed_document(engine, filename="manual.pdf", mime_type="application/pdf", source_path=source_path)

    def unavailable_ocr(_path: Path) -> str:
        raise OcrUnavailable("PaddleOCR is not installed")

    process_ingest_task(
        engine,
        QueuedTask(
            id="task-1",
            document_id="doc-1",
            task_type=TaskType.INGEST.value,
            attempt_count=0,
            stage="processing",
        ),
        ocr_reader=unavailable_ocr,
    )

    with engine.connect() as connection:
        chunks = connection.execute(select(document_chunks)).mappings().all()
        asset = connection.execute(select(document_assets)).mappings().one()
    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == ChunkType.TEXT.value
    assert chunks[0]["content"] == "Engine oil interval"
    assert asset["visual_observation"]["ocr"]["status"] == "failed"
    assert asset["visual_observation"]["ocr"]["error_code"] == "ocr_unavailable"
