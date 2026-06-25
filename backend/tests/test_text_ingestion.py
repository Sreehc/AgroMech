from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from sqlalchemy import create_engine, insert, select

from agromech_api.db.enums import ChunkType, DocumentStatus, TaskType
from agromech_api.db.models import document_chunks, documents, metadata
from agromech_api.ingestion import IngestFailure, QueuedTask
from agromech_api.text_ingestion import (
    ParsedTextSegment,
    parse_text_document,
    process_text_document,
    replace_text_chunks,
)
from agromech_worker.main import process_ingest_task


def create_test_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    return engine


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


def write_pdf_with_blank_and_text_page(path: Path) -> None:
    import fitz

    document = fitz.open()
    document.new_page()
    text_page = document.new_page()
    text_page.insert_text((72, 72), "Hydraulic pressure warning")
    document.save(path)
    document.close()


def write_minimal_docx(path: Path, paragraphs: list[str]) -> None:
    body = "".join(f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>" for paragraph in paragraphs)
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "")
        archive.writestr("word/document.xml", document_xml)


def test_parse_text_document_supports_p0_text_formats(tmp_path) -> None:
    txt_path = tmp_path / "manual.txt"
    md_path = tmp_path / "manual.md"
    pdf_path = tmp_path / "manual.pdf"
    docx_path = tmp_path / "manual.docx"
    txt_path.write_text("Check oil level\nTighten bolts", encoding="utf-8")
    md_path.write_text("# Service\nChange filter\n\n## Safety\nStop engine", encoding="utf-8")
    write_minimal_pdf(pdf_path, "Hydraulic pressure warning")
    write_minimal_docx(docx_path, ["Maintenance interval", "Every 100 hours"])

    parsed = {
        "txt": parse_text_document(txt_path, "text/plain"),
        "md": parse_text_document(md_path, "text/markdown"),
        "pdf": parse_text_document(pdf_path, "application/pdf"),
        "docx": parse_text_document(
            docx_path,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    }

    assert parsed["txt"][0].text == "Check oil level\nTighten bolts"
    assert parsed["txt"][0].source_locator == {"type": "text", "line_start": 1, "line_end": 2}
    assert parsed["md"][0].section_title == "Service"
    assert parsed["md"][0].source_locator["type"] == "markdown"
    assert "Hydraulic pressure warning" in parsed["pdf"][0].text
    assert parsed["pdf"][0].page_number == 1
    assert parsed["pdf"][0].source_locator == {"type": "pdf", "page": 1}
    assert parsed["docx"][0].text == "Maintenance interval\nEvery 100 hours"
    assert parsed["docx"][0].source_locator == {
        "type": "docx",
        "paragraph_start": 1,
        "paragraph_end": 2,
    }


def test_parse_pdf_skips_blank_pages_and_preserves_original_page_number(tmp_path) -> None:
    pdf_path = tmp_path / "blank-first.pdf"
    write_pdf_with_blank_and_text_page(pdf_path)

    parsed = parse_text_document(pdf_path, "application/pdf")

    assert len(parsed) == 1
    assert "Hydraulic pressure warning" in parsed[0].text
    assert parsed[0].page_number == 2
    assert parsed[0].source_locator == {"type": "pdf", "page": 2}


def test_replace_text_chunks_saves_only_chunks_with_source_locator(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id="doc-1",
                title="Manual",
                original_file_name="manual.txt",
                file_hash="hash-doc-1",
                file_size_bytes=10,
                mime_type="text/plain",
                storage_uri="file:///tmp/manual.txt",
                status=DocumentStatus.PROCESSING.value,
                created_by_role="admin",
            )
        )

    replace_text_chunks(
        engine,
        "doc-1",
        [
            ParsedTextSegment(
                text="Keep hands away from belts.",
                source_locator={"type": "text", "line_start": 1, "line_end": 1},
            ),
            ParsedTextSegment(text="No locator"),
        ],
    )

    with engine.connect() as connection:
        chunks = connection.execute(select(document_chunks)).mappings().all()
    assert len(chunks) == 1
    assert chunks[0]["document_id"] == "doc-1"
    assert chunks[0]["chunk_type"] == ChunkType.TEXT.value
    assert chunks[0]["content"] == "Keep hands away from belts."
    assert chunks[0]["source_locator"] == {"type": "text", "line_start": 1, "line_end": 1}


def test_replace_text_chunks_rejects_empty_source_locator(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id="doc-1",
                title="Manual",
                original_file_name="manual.txt",
                file_hash="hash-doc-1",
                file_size_bytes=10,
                mime_type="text/plain",
                storage_uri="file:///tmp/manual.txt",
                status=DocumentStatus.PROCESSING.value,
                created_by_role="admin",
            )
        )

    count = replace_text_chunks(
        engine,
        "doc-1",
        [ParsedTextSegment(text="Has text but no usable locator", source_locator={})],
    )

    assert count == 0
    with engine.connect() as connection:
        chunks = connection.execute(select(document_chunks)).mappings().all()
    assert chunks == []


def test_worker_default_processor_parses_text_document_and_saves_chunks(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    source_path = tmp_path / "manual.txt"
    source_path.write_text("Grease fittings every 50 hours.", encoding="utf-8")
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id="doc-1",
                title="Manual",
                original_file_name="manual.txt",
                file_hash="hash-doc-1",
                file_size_bytes=source_path.stat().st_size,
                mime_type="text/plain",
                storage_uri=f"file://{source_path}",
                status=DocumentStatus.PROCESSING.value,
                created_by_role="admin",
            )
        )

    process_ingest_task(
        engine,
        QueuedTask(
            id="task-1",
            document_id="doc-1",
            task_type=TaskType.INGEST.value,
            attempt_count=0,
            stage="processing",
        ),
    )

    with engine.connect() as connection:
        content = connection.execute(select(document_chunks.c.content)).scalar_one()
    assert content == "Grease fittings every 50 hours."


def test_empty_parse_failure_does_not_delete_existing_text_chunks(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    source_path = tmp_path / "empty.txt"
    source_path.write_text("", encoding="utf-8")
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id="doc-1",
                title="Manual",
                original_file_name="manual.txt",
                file_hash="hash-doc-1",
                file_size_bytes=0,
                mime_type="text/plain",
                storage_uri=f"file://{source_path}",
                status=DocumentStatus.REPROCESSING.value,
                created_by_role="admin",
            )
        )
        connection.execute(
            insert(document_chunks).values(
                id="chunk-1",
                document_id="doc-1",
                chunk_type=ChunkType.TEXT.value,
                content="Existing searchable content",
                source_locator={"type": "text", "line_start": 1, "line_end": 1},
            )
        )

    with pytest.raises(IngestFailure) as exc:
        process_text_document(engine, "doc-1")

    assert exc.value.code == "no_text_extracted"
    with engine.connect() as connection:
        content = connection.execute(select(document_chunks.c.content)).scalar_one()
    assert content == "Existing searchable content"
