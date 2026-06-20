from __future__ import annotations

import logging

from sqlalchemy import Engine
from sqlalchemy import select

from agromech_api.database import get_engine
from agromech_api.db.models import documents
from agromech_api.image_ingestion import is_image_document, is_pdf_document, process_image_document
from agromech_api.ingestion import IngestFailure, IngestTaskRunner, QueuedTask
from agromech_api.table_ingestion import is_table_document, process_table_document
from agromech_api.text_ingestion import process_text_document


LOGGER = logging.getLogger("agromech.worker")


def health_status() -> dict[str, str]:
    return {"status": "ok", "service": "worker"}


def process_ingest_task(engine: Engine, task: QueuedTask, *, ocr_reader=None) -> None:
    with engine.connect() as connection:
        document = connection.execute(
            select(documents.c.original_file_name, documents.c.mime_type).where(
                documents.c.id == task.document_id
            )
        ).mappings().one()

    if is_table_document(document["original_file_name"], document["mime_type"]):
        chunk_count = process_table_document(engine, task.document_id)
        chunk_kind = "table"
    elif is_image_document(document["original_file_name"], document["mime_type"]):
        result = process_image_document(engine, task.document_id, ocr_reader=ocr_reader)
        chunk_count = result.chunk_count
        chunk_kind = "image"
    elif is_pdf_document(document["original_file_name"], document["mime_type"]):
        chunk_count = 0
        try:
            chunk_count += process_text_document(engine, task.document_id)
        except IngestFailure as exc:
            if exc.code != "no_text_extracted":
                raise
        result = process_image_document(
            engine,
            task.document_id,
            ocr_reader=ocr_reader,
            fail_on_all_ocr_failure=chunk_count == 0,
        )
        chunk_count += result.chunk_count
        chunk_kind = "pdf"
    else:
        chunk_count = process_text_document(engine, task.document_id)
        chunk_kind = "text"

    LOGGER.info(
        "Processed ingest task: task_id=%s document_id=%s task_type=%s chunk_kind=%s chunks=%s",
        task.id,
        task.document_id,
        task.task_type,
        chunk_kind,
        chunk_count,
    )


def run_once(*, engine: Engine | None = None, processor=None) -> str:
    active_engine = engine or get_engine()
    active_processor = processor or (lambda task: process_ingest_task(active_engine, task))
    runner = IngestTaskRunner(active_engine)
    result = runner.run_next(active_processor)
    LOGGER.info("AgroMech worker run_once result: %s", result)
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    LOGGER.info("AgroMech worker health: %s", health_status())
    try:
        run_once()
    except Exception:
        LOGGER.exception("AgroMech worker run_once failed")


if __name__ == "__main__":
    main()
