from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import create_engine, insert, select

from agromech_api.db.enums import ChunkType, DocumentStatus, TaskType
from agromech_api.db.models import document_chunks, documents, metadata
from agromech_api.ingestion import QueuedTask
from agromech_api.table_ingestion import ParsedTableSegment, parse_table_document, replace_table_chunks
from agromech_worker.main import process_ingest_task


def create_test_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    return engine


def inline_cell(reference: str, value: str) -> str:
    return f'<c r="{reference}" t="inlineStr"><is><t>{value}</t></is></c>'


def write_minimal_xlsx(path: Path) -> None:
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        f'<row r="1">{inline_cell("A1", "Fault Code")}{inline_cell("B1", "Action")}</row>'
        f'<row r="2">{inline_cell("A2", "E01")}{inline_cell("B2", "Check hydraulic oil")}</row>'
        "</sheetData></worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Faults" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def test_parse_table_document_supports_csv_and_xlsx(tmp_path) -> None:
    csv_path = tmp_path / "faults.csv"
    xlsx_path = tmp_path / "faults.xlsx"
    csv_path.write_text("Fault Code,Action\nE01,Check hydraulic oil\n", encoding="utf-8")
    write_minimal_xlsx(xlsx_path)

    csv_segments = parse_table_document(csv_path, "text/csv")
    xlsx_segments = parse_table_document(
        xlsx_path,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    assert csv_segments[0].header == ["Fault Code", "Action"]
    assert csv_segments[0].rows == [["E01", "Check hydraulic oil"]]
    assert csv_segments[0].source_locator == {"type": "csv", "row_start": 1, "row_end": 2}
    assert xlsx_segments[0].worksheet_name == "Faults"
    assert xlsx_segments[0].header == ["Fault Code", "Action"]
    assert xlsx_segments[0].rows == [["E01", "Check hydraulic oil"]]
    assert xlsx_segments[0].source_locator == {
        "type": "xlsx",
        "worksheet_name": "Faults",
        "row_start": 1,
        "row_end": 2,
    }


def test_replace_table_chunks_saves_row_range_header_and_summary(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id="doc-1",
                title="Faults",
                original_file_name="faults.csv",
                file_hash="hash-doc-1",
                file_size_bytes=32,
                mime_type="text/csv",
                storage_uri="file:///tmp/faults.csv",
                status=DocumentStatus.PROCESSING.value,
                created_by_role="admin",
            )
        )

    replace_table_chunks(
        engine,
        "doc-1",
        [
            ParsedTableSegment(
                header=["Fault Code", "Action"],
                rows=[["E01", "Check hydraulic oil"]],
                worksheet_name="Faults",
                row_start=1,
                row_end=2,
                source_locator={
                    "type": "xlsx",
                    "worksheet_name": "Faults",
                    "row_start": 1,
                    "row_end": 2,
                },
            )
        ],
    )

    with engine.connect() as connection:
        chunk = connection.execute(select(document_chunks)).mappings().one()
    assert chunk["chunk_type"] == ChunkType.TABLE.value
    assert chunk["worksheet_name"] == "Faults"
    assert chunk["row_start"] == 1
    assert chunk["row_end"] == 2
    assert "Fault Code,Action" in chunk["content"]
    assert "E01,Check hydraulic oil" in chunk["content"]
    assert chunk["summary"] == "Faults rows 1-2: Fault Code, Action"


def test_worker_default_processor_parses_csv_document_and_saves_table_chunk(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    source_path = tmp_path / "faults.csv"
    source_path.write_text("Fault Code,Action\nE01,Check hydraulic oil\n", encoding="utf-8")
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id="doc-1",
                title="Faults",
                original_file_name="faults.csv",
                file_hash="hash-doc-1",
                file_size_bytes=source_path.stat().st_size,
                mime_type="text/csv",
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
        chunk = connection.execute(select(document_chunks)).mappings().one()
    assert chunk["chunk_type"] == ChunkType.TABLE.value
    assert chunk["source_locator"] == {"type": "csv", "row_start": 1, "row_end": 2}
