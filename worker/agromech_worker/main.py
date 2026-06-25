from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy import select

from agromech_api.config import get_settings
from agromech_api.database import get_engine
from agromech_api.db.models import documents
from agromech_api.entity_extraction import process_document_entities
from agromech_api.graph_rag import GraphRagService, GraphSyncError
from agromech_api.image_ingestion import is_image_document, is_pdf_document, process_image_document
from agromech_api.ingestion import IngestFailure, IngestTaskRunner, QueuedTask
from agromech_api.ocr_ingestion import process_ocr_document
from agromech_api.search_indexing import SearchIndexer
from agromech_api.table_ingestion import is_table_document, process_table_document
from agromech_api.text_ingestion import process_text_document
from agromech_api.vision_ingestion import build_visual_reader, process_visual_observations


LOGGER = logging.getLogger("agromech.worker")


def health_status() -> dict[str, str]:
    return {"status": "ok", "service": "worker"}


def process_ingest_task(
    engine: Engine,
    task: QueuedTask,
    *,
    ocr_reader=None,
    visual_reader=None,
    indexer: SearchIndexer | None = None,
    graph_service=None,
    ocr_document_processor=None,
) -> None:
    settings = get_settings()
    with engine.connect() as connection:
        document = connection.execute(
            select(documents.c.original_file_name, documents.c.mime_type).where(
                documents.c.id == task.document_id
            )
        ).mappings().one()

    # Cloud text-only OCR mode: recognize all text via the PaddleOCR cloud API and
    # build text chunks from it, replacing local pypdf/OCR text extraction. Image
    # documents and the visual understanding path are left to the legacy branches.
    cloud_text_mode = settings.ocr_text_mode == "cloud_text"
    if cloud_text_mode and is_pdf_document(document["original_file_name"], document["mime_type"]):
        ocr_processor = ocr_document_processor or process_ocr_document
        ocr_result = ocr_processor(engine, task.document_id)
        chunk_count = ocr_result.text_chunk_count
        chunk_kind = "ocr_text"
    elif is_table_document(document["original_file_name"], document["mime_type"]):
        chunk_count = process_table_document(engine, task.document_id)
        chunk_kind = "table"
    elif is_image_document(document["original_file_name"], document["mime_type"]):
        image_result = process_image_document(
            engine,
            task.document_id,
            ocr_reader=ocr_reader,
            fail_on_all_ocr_failure=False,
            asset_root=Path(settings.local_file_storage_path) if settings.file_storage_backend == "local" else None,
        )
        visual_result = process_visual_observations(
            engine,
            task.document_id,
            visual_reader=visual_reader,
            confidence_threshold=settings.vision_confidence_threshold,
        )
        chunk_count = image_result.chunk_count + visual_result.chunk_count
        if chunk_count == 0:
            raise IngestFailure(
                "image_understanding_failed",
                "OCR and visual observation produced no searchable image content",
                stage="vision",
            )
        chunk_kind = "image"
    elif is_pdf_document(document["original_file_name"], document["mime_type"]):
        chunk_count = 0
        try:
            chunk_count += process_table_document(engine, task.document_id)
        except IngestFailure as exc:
            if exc.code != "no_table_extracted":
                raise
        try:
            chunk_count += process_text_document(engine, task.document_id)
        except IngestFailure as exc:
            if exc.code != "no_text_extracted":
                raise
        result = process_image_document(
            engine,
            task.document_id,
            ocr_reader=ocr_reader,
            fail_on_all_ocr_failure=False,
            asset_root=Path(settings.local_file_storage_path) if settings.file_storage_backend == "local" else None,
        )
        visual_result = process_visual_observations(
            engine,
            task.document_id,
            visual_reader=visual_reader,
            confidence_threshold=settings.vision_confidence_threshold,
        )
        chunk_count += result.chunk_count + visual_result.chunk_count
        if chunk_count == 0:
            raise IngestFailure(
                "image_understanding_failed",
                "PDF text extraction, OCR, and visual observation produced no searchable content",
                stage="vision",
            )
        chunk_kind = "pdf"
    else:
        chunk_count = process_text_document(engine, task.document_id)
        chunk_kind = "text"

    entity_result = process_document_entities(engine, task.document_id)
    try:
        graph_result = (graph_service or GraphRagService(engine)).sync_document(task.document_id)
    except GraphSyncError as exc:
        raise IngestFailure("graph_sync_failed", str(exc), stage="graph") from exc
    index_result = (indexer or SearchIndexer(engine)).index_document(task.document_id)
    LOGGER.info(
        "Processed ingest task: task_id=%s document_id=%s task_type=%s chunk_kind=%s chunks=%s entity_links=%s graph_edges=%s indexed_chunks=%s",
        task.id,
        task.document_id,
        task.task_type,
        chunk_kind,
        chunk_count,
        entity_result.link_count,
        graph_result.edge_count,
        index_result.chunk_count,
    )


def run_once(*, engine: Engine | None = None, processor=None) -> str:
    active_engine = engine or get_engine()
    if processor is None:
        # Production path: build the indexer from the configured embedding
        # provider (Bailian when selected) so real ingestion uses real vectors.
        # Direct process_ingest_task callers keep the deterministic default.
        from agromech_api.embedding import build_embedding_provider
        from agromech_api.graph_rag import build_graph_service
        from agromech_api.zvec_store import build_vector_store

        settings = get_settings()
        indexer = SearchIndexer(
            active_engine,
            embedding_provider=build_embedding_provider(settings),
            vector_store=build_vector_store(settings),
            collection=settings.zvec_collection if settings.vector_backend == "zvec" else None,
        )
        graph_service = build_graph_service(active_engine, settings)
        visual_reader = build_visual_reader(settings)
        active_processor = lambda task: process_ingest_task(
            active_engine,
            task,
            indexer=indexer,
            graph_service=graph_service,
            visual_reader=visual_reader,
        )
    else:
        active_processor = processor
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
