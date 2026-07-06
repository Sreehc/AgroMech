from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy import select

from agromech_api.core.config import get_settings
from agromech_api.core.database import get_engine
from agromech_api.db.models import documents
from agromech_api.domain.entities import process_document_entities
from agromech_api.ingestion.image import is_image_document, is_pdf_document, process_image_document
from agromech_api.ingestion.metadata import backfill_document_metadata, build_metadata_extractor
from agromech_api.ingestion.ocr import process_ocr_document
from agromech_api.ingestion.runner import IngestFailure, IngestTaskRunner, QueuedTask
from agromech_api.ingestion.table import is_table_document, process_table_document
from agromech_api.ingestion.text import process_text_document
from agromech_api.ingestion.vision import build_visual_reader, process_visual_observations
from agromech_api.rag.retrieval.indexing import SearchIndexer, VisualPageIndexer


LOGGER = logging.getLogger("agromech.worker")
SKIP_METADATA_EXTRACTION = object()


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
    metadata_extractor=SKIP_METADATA_EXTRACTION,
    asset_root: Path | None = None,
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
        ocr_result = ocr_processor(
            engine,
            task.document_id,
            persist_visual=True,
            asset_root=asset_root,
        )
        # Run the visual-understanding pass over the page + region assets the OCR
        # step just persisted so figures/tables get searchable image chunks too.
        visual_result = process_visual_observations(
            engine,
            task.document_id,
            visual_reader=visual_reader,
            confidence_threshold=settings.vision_confidence_threshold,
        )
        chunk_count = (
            ocr_result.text_chunk_count
            + ocr_result.table_chunk_count
            + visual_result.chunk_count
        )
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
            asset_root=asset_root,
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
            asset_root=asset_root,
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

    if metadata_extractor is SKIP_METADATA_EXTRACTION:
        metadata_result = None
    else:
        metadata_result = backfill_document_metadata(
            engine,
            task.document_id,
            extractor=metadata_extractor,
        )
    entity_result = process_document_entities(engine, task.document_id)
    # Graph RAG is intentionally disabled in the current main ingest path.
    # Keep graph modules available for future work, but do not sync graph edges here.
    active_indexer = indexer or SearchIndexer(engine)
    index_result = active_indexer.index_document(task.document_id)
    visual_index_result = None
    if any(asset["asset_type"] == "page_image" for asset in _document_page_assets(engine, task.document_id)):
        visual_indexer = VisualPageIndexer(engine)
        visual_index_result = visual_indexer.index_document(task.document_id)
    LOGGER.info(
        "Processed ingest task: task_id=%s document_id=%s task_type=%s chunk_kind=%s chunks=%s metadata_fields=%s entity_links=%s indexed_chunks=%s visual_indexed=%s",
        task.id,
        task.document_id,
        task.task_type,
        chunk_kind,
        chunk_count,
        sorted(metadata_result.updated_fields) if metadata_result is not None else [],
        entity_result.link_count,
        index_result.chunk_count,
        visual_index_result.chunk_count if visual_index_result is not None else 0,
    )


def _document_page_assets(engine: Engine, document_id: str):
    from agromech_api.db.models import document_assets

    with engine.connect() as connection:
        return connection.execute(
            select(document_assets.c.asset_type).where(document_assets.c.document_id == document_id)
        ).mappings().all()


def run_once(*, engine: Engine | None = None, processor=None) -> str:
    active_engine = engine or get_engine()
    if processor is None:
        # Production path: build the indexer from the configured embedding
        # provider (Bailian when selected) so real ingestion uses real vectors.
        # Direct process_ingest_task callers keep the deterministic default.
        from agromech_api.integrations.embeddings.text import build_embedding_provider

        settings = get_settings()
        text_embeddings = build_embedding_provider(settings)
        indexer = SearchIndexer(
            active_engine,
            embedding_provider=text_embeddings,
        )
        visual_reader = build_visual_reader(settings)
        metadata_extractor = build_metadata_extractor(settings)
        active_processor = lambda task: process_ingest_task(
            active_engine,
            task,
            indexer=indexer,
            visual_reader=visual_reader,
            metadata_extractor=metadata_extractor,
            asset_root=Path(settings.local_file_storage_path) if settings.file_storage_backend == "local" else None,
        )
    else:
        active_processor = processor
    runner = IngestTaskRunner(active_engine)
    result = runner.run_next(active_processor)
    LOGGER.info("AgroMech worker run_once result: %s", result)
    return result


def consume_forever(*, engine: Engine | None = None) -> None:
    from agromech_worker.rabbitmq import consume

    settings = get_settings()
    active_engine = engine or get_engine()
    consume(settings, lambda: run_once(engine=active_engine))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    LOGGER.info("AgroMech worker health: %s", health_status())
    try:
        run_once()
    except Exception:
        LOGGER.exception("AgroMech worker run_once failed")


if __name__ == "__main__":
    main()
