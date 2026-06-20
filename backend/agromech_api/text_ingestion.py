from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4
from xml.etree import ElementTree
from zipfile import ZipFile

from sqlalchemy import Engine, delete, insert, select

from agromech_api.db.enums import ChunkType
from agromech_api.db.models import document_chunks, documents
from agromech_api.ingestion import IngestFailure


@dataclass(frozen=True)
class ParsedTextSegment:
    text: str
    source_locator: dict[str, object] | None = None
    page_number: int | None = None
    section_title: str | None = None


def parse_text_document(path: Path, mime_type: str) -> list[ParsedTextSegment]:
    extension = path.suffix.lower()
    if extension in {".txt"} or mime_type == "text/plain":
        return parse_plain_text(path)
    if extension in {".md", ".markdown"} or mime_type in {"text/markdown", "text/x-markdown"}:
        return parse_markdown(path)
    if extension == ".pdf" or mime_type == "application/pdf":
        return parse_pdf(path)
    if (
        extension == ".docx"
        or mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ):
        return parse_docx(path)
    raise IngestFailure(
        "unsupported_text_type",
        f"Unsupported text document type: {mime_type or extension}",
        stage="parse",
    )


def parse_plain_text(path: Path) -> list[ParsedTextSegment]:
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return []
    line_count = max(1, len(text.splitlines()))
    return [
        ParsedTextSegment(
            text=text,
            source_locator={"type": "text", "line_start": 1, "line_end": line_count},
        )
    ]


def parse_markdown(path: Path) -> list[ParsedTextSegment]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    segments: list[ParsedTextSegment] = []
    current_title: str | None = None
    current_start = 1
    current_lines: list[str] = []

    def flush(line_end: int) -> None:
        text = "\n".join(line for line in current_lines if line.strip()).strip()
        if not text:
            return
        locator = {
            "type": "markdown",
            "line_start": current_start,
            "line_end": line_end,
        }
        if current_title:
            locator["section_title"] = current_title
        segments.append(
            ParsedTextSegment(
                text=text,
                section_title=current_title,
                source_locator=locator,
            )
        )

    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            flush(index - 1)
            current_title = stripped.lstrip("#").strip() or None
            current_start = index
            current_lines = [line]
        else:
            current_lines.append(line)
    flush(len(lines))
    return segments


def parse_pdf(path: Path) -> list[ParsedTextSegment]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise IngestFailure("parser_unavailable", "pypdf is not installed", stage="parse") from exc

    reader = PdfReader(str(path))
    segments: list[ParsedTextSegment] = []
    for index, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            segments.append(
                ParsedTextSegment(
                    text=text,
                    page_number=index,
                    source_locator={"type": "pdf", "page": index},
                )
            )
    return segments


def parse_docx(path: Path) -> list[ParsedTextSegment]:
    try:
        with ZipFile(path) as archive:
            document_xml = archive.read("word/document.xml")
    except (KeyError, OSError) as exc:
        raise IngestFailure("docx_parse_failed", "DOCX document.xml could not be read", stage="parse") from exc

    root = ElementTree.fromstring(document_xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace)).strip()
        if text:
            paragraphs.append(text)

    if not paragraphs:
        return []
    return [
        ParsedTextSegment(
            text="\n".join(paragraphs),
            source_locator={
                "type": "docx",
                "paragraph_start": 1,
                "paragraph_end": len(paragraphs),
            },
        )
    ]


def replace_text_chunks(
    engine: Engine,
    document_id: str,
    segments: list[ParsedTextSegment],
) -> int:
    rows = []
    for segment in segments:
        text = segment.text.strip()
        if not text or not segment.source_locator:
            continue
        rows.append(
            {
                "id": str(uuid4()),
                "document_id": document_id,
                "chunk_type": ChunkType.TEXT.value,
                "content": text,
                "summary": text[:240],
                "page_number": segment.page_number,
                "section_title": segment.section_title,
                "source_locator": segment.source_locator,
            }
        )

    with engine.begin() as connection:
        connection.execute(
            delete(document_chunks).where(
                document_chunks.c.document_id == document_id,
                document_chunks.c.chunk_type == ChunkType.TEXT.value,
            )
        )
        if rows:
            connection.execute(insert(document_chunks), rows)
    return len(rows)


def process_text_document(engine: Engine, document_id: str) -> int:
    with engine.connect() as connection:
        document = connection.execute(
            select(documents).where(documents.c.id == document_id)
        ).mappings().one()

    path = local_file_path(document["storage_uri"])
    if not path.exists():
        raise IngestFailure("source_file_missing", "Source file is missing", stage="parse")

    segments = parse_text_document(path, document["mime_type"])
    has_referenceable_segment = any(segment.text.strip() and segment.source_locator for segment in segments)
    if not has_referenceable_segment:
        raise IngestFailure(
            "no_text_extracted",
            "No text with source locator extracted",
            stage="parse",
        )
    return replace_text_chunks(engine, document_id, segments)


def local_file_path(storage_uri: str) -> Path:
    parsed = urlparse(storage_uri)
    if parsed.scheme != "file":
        raise IngestFailure("unsupported_storage_uri", "Only local file storage is supported", stage="parse")
    return Path(unquote(parsed.path))
