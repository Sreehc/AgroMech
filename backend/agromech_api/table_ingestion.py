from __future__ import annotations

import csv
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree
from zipfile import ZipFile

from sqlalchemy import Engine, delete, insert, select

from agromech_api.db.enums import ChunkType
from agromech_api.db.models import document_chunks, documents
from agromech_api.ingestion import IngestFailure
from agromech_api.text_ingestion import local_file_path


@dataclass(frozen=True)
class ParsedTableSegment:
    header: list[str]
    rows: list[list[str]]
    row_start: int
    row_end: int
    source_locator: dict[str, object] | None = None
    worksheet_name: str | None = None


def is_table_document(filename: str, mime_type: str) -> bool:
    extension = Path(filename).suffix.lower()
    return extension in {".csv", ".xlsx"} or mime_type in {
        "text/csv",
        "application/csv",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }


def parse_table_document(path: Path, mime_type: str) -> list[ParsedTableSegment]:
    extension = path.suffix.lower()
    if extension == ".csv" or mime_type in {"text/csv", "application/csv"}:
        return parse_csv(path)
    if (
        extension == ".xlsx"
        or mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ):
        return parse_xlsx(path)
    raise IngestFailure(
        "unsupported_table_type",
        f"Unsupported table document type: {mime_type or extension}",
        stage="parse",
    )


def parse_csv(path: Path) -> list[ParsedTableSegment]:
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        rows = [[cell.strip() for cell in row] for row in csv.reader(handle)]
    rows = [row for row in rows if any(cell for cell in row)]
    if not rows:
        return []
    header = rows[0]
    data_rows = rows[1:]
    row_end = len(rows)
    return [
        ParsedTableSegment(
            header=header,
            rows=data_rows,
            row_start=1,
            row_end=row_end,
            source_locator={"type": "csv", "row_start": 1, "row_end": row_end},
        )
    ]


def parse_xlsx(path: Path) -> list[ParsedTableSegment]:
    try:
        with ZipFile(path) as archive:
            shared_strings = read_shared_strings(archive)
            sheets = read_workbook_sheets(archive)
            segments = []
            for worksheet_name, worksheet_path in sheets:
                xml = archive.read(worksheet_path)
                rows = read_worksheet_rows(xml, shared_strings)
                rows = [row for row in rows if any(cell for cell in row)]
                if not rows:
                    continue
                row_end = len(rows)
                segments.append(
                    ParsedTableSegment(
                        header=rows[0],
                        rows=rows[1:],
                        worksheet_name=worksheet_name,
                        row_start=1,
                        row_end=row_end,
                        source_locator={
                            "type": "xlsx",
                            "worksheet_name": worksheet_name,
                            "row_start": 1,
                            "row_end": row_end,
                        },
                    )
                )
    except (KeyError, OSError, ElementTree.ParseError) as exc:
        raise IngestFailure("xlsx_parse_failed", "XLSX workbook could not be read", stage="parse") from exc
    return segments


def read_shared_strings(archive: ZipFile) -> list[str]:
    try:
        xml = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    root = ElementTree.fromstring(xml)
    return [
        "".join(text_node.text or "" for text_node in item.findall(".//x:t", namespace))
        for item in root.findall(".//x:si", namespace)
    ]


def read_workbook_sheets(archive: ZipFile) -> list[tuple[str, str]]:
    main_ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rel_ns = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    relationship_targets = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in relationships.findall(".//r:Relationship", rel_ns)
    }

    sheets: list[tuple[str, str]] = []
    for sheet in workbook.findall(".//x:sheet", main_ns):
        relationship_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        target = relationship_targets[relationship_id]
        worksheet_path = target if target.startswith("xl/") else f"xl/{target}"
        sheets.append((sheet.attrib["name"], worksheet_path))
    return sheets


def read_worksheet_rows(xml: bytes, shared_strings: list[str]) -> list[list[str]]:
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    root = ElementTree.fromstring(xml)
    rows: list[list[str]] = []
    for row in root.findall(".//x:row", namespace):
        rows.append([cell_value(cell, shared_strings, namespace) for cell in row.findall("x:c", namespace)])
    return rows


def cell_value(cell, shared_strings: list[str], namespace: dict[str, str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//x:t", namespace)).strip()

    value = cell.find("x:v", namespace)
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        index = int(value.text)
        return shared_strings[index].strip() if index < len(shared_strings) else ""
    return value.text.strip()


def replace_table_chunks(
    engine: Engine,
    document_id: str,
    segments: list[ParsedTableSegment],
) -> int:
    rows = []
    for segment in segments:
        content = table_content(segment)
        if not content.strip() or not segment.source_locator:
            continue
        rows.append(
            {
                "id": str(uuid4()),
                "document_id": document_id,
                "chunk_type": ChunkType.TABLE.value,
                "content": content,
                "summary": table_summary(segment),
                "worksheet_name": segment.worksheet_name,
                "row_start": segment.row_start,
                "row_end": segment.row_end,
                "source_locator": segment.source_locator,
            }
        )

    with engine.begin() as connection:
        connection.execute(
            delete(document_chunks).where(
                document_chunks.c.document_id == document_id,
                document_chunks.c.chunk_type == ChunkType.TABLE.value,
            )
        )
        if rows:
            connection.execute(insert(document_chunks), rows)
    return len(rows)


def table_content(segment: ParsedTableSegment) -> str:
    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    if segment.header:
        writer.writerow(segment.header)
    writer.writerows(segment.rows)
    return buffer.getvalue().strip()


def table_summary(segment: ParsedTableSegment) -> str:
    header = ", ".join(cell for cell in segment.header if cell)
    prefix = f"{segment.worksheet_name} " if segment.worksheet_name else ""
    return f"{prefix}rows {segment.row_start}-{segment.row_end}: {header}".strip()


def process_table_document(engine: Engine, document_id: str) -> int:
    with engine.connect() as connection:
        document = connection.execute(
            select(documents).where(documents.c.id == document_id)
        ).mappings().one()

    path = local_file_path(document["storage_uri"])
    if not path.exists():
        raise IngestFailure("source_file_missing", "Source file is missing", stage="parse")

    segments = parse_table_document(path, document["mime_type"])
    has_referenceable_segment = any(table_content(segment).strip() and segment.source_locator for segment in segments)
    if not has_referenceable_segment:
        raise IngestFailure(
            "no_table_extracted",
            "No table with source locator extracted",
            stage="parse",
        )
    return replace_table_chunks(engine, document_id, segments)
