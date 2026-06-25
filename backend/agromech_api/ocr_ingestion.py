from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import Engine, delete, insert, select

from agromech_api.chunk_quality import is_referenceable_chunk
from agromech_api.config import Settings, get_settings
from agromech_api.db.enums import ChunkType
from agromech_api.db.models import document_chunks, documents
from agromech_api.ingestion import IngestFailure
from agromech_api.paddleocr_client import (
    OcrApiError,
    OcrPage,
    OcrResult,
    PaddleOcrApiClient,
    build_paddleocr_client,
)
from agromech_api.text_ingestion import local_file_path


@dataclass(frozen=True)
class OcrIngestionResult:
    page_count: int
    text_chunk_count: int


def process_ocr_document(
    engine: Engine,
    document_id: str,
    *,
    client: PaddleOcrApiClient | None = None,
    settings: Settings | None = None,
    ocr_result: OcrResult | None = None,
    optional_payload: dict[str, object] | None = None,
) -> OcrIngestionResult:
    """Recognize all text in a document via the PaddleOCR cloud API.

    Basic text-only ingestion: take every page's recognized text and persist it
    as TEXT chunks for the text index. Layout structure, table HTML, and cropped
    region images are intentionally not used this round.

    ``ocr_result`` can be injected to run the persistence path offline in tests
    without touching the network. Failures surface as ``IngestFailure(stage=...)``
    so the ingest state machine records them.
    """
    settings = settings or get_settings()
    if ocr_result is None:
        ocr_result = _run_ocr(
            engine,
            document_id,
            client=client,
            settings=settings,
            optional_payload=optional_payload,
        )

    if not ocr_result.pages:
        raise IngestFailure("ocr_no_pages", "OCR returned no pages", stage="ocr")

    text_rows: list[dict[str, object]] = []
    for page in ocr_result.pages:
        page_number = page.page_index + 1
        row = _text_chunk_row(document_id, page, page_number)
        if row is not None:
            text_rows.append(row)

    if not text_rows:
        raise IngestFailure(
            "ocr_no_text_extracted",
            "OCR produced no referenceable text content",
            stage="ocr",
        )

    _replace_text_chunks(engine, document_id, text_rows)
    return OcrIngestionResult(
        page_count=len(ocr_result.pages),
        text_chunk_count=len(text_rows),
    )


def _run_ocr(
    engine: Engine,
    document_id: str,
    *,
    client: PaddleOcrApiClient | None,
    settings: Settings,
    optional_payload: dict[str, object] | None,
) -> OcrResult:
    with engine.connect() as connection:
        document = connection.execute(
            select(documents).where(documents.c.id == document_id)
        ).mappings().one()

    source_path = local_file_path(document["storage_uri"])
    if not source_path.exists():
        raise IngestFailure("source_file_missing", "Source file is missing", stage="parse")

    active_client = client or build_paddleocr_client(settings)
    try:
        return active_client.parse_document(
            content=source_path.read_bytes(),
            filename=document["original_file_name"],
            optional_payload=optional_payload,
        )
    except OcrApiError as exc:
        raise IngestFailure("ocr_api_failed", str(exc), stage="ocr") from exc


def _text_chunk_row(document_id: str, page: OcrPage, page_number: int) -> dict[str, object] | None:
    text = _page_text(page)
    source_locator = {"type": "ocr_text", "page": page_number}
    if not is_referenceable_chunk(text, source_locator):
        return None
    return {
        "id": str(uuid4()),
        "document_id": document_id,
        "chunk_type": ChunkType.TEXT.value,
        "content": text,
        "summary": text[:240],
        "page_number": page_number,
        "source_locator": source_locator,
    }


def _page_text(page: OcrPage) -> str:
    """Return all recognized text for a page.

    Prefer the page's full Markdown text (everything the OCR recognized,
    including text inside tables as plain text). Fall back to concatenating
    layout block text when Markdown is empty.
    """
    markdown = (page.markdown or "").strip()
    if markdown:
        return markdown
    block_texts = [region.text.strip() for region in page.regions if region.text.strip()]
    return "\n".join(block_texts).strip()


def _replace_text_chunks(
    engine: Engine,
    document_id: str,
    text_rows: list[dict[str, object]],
) -> None:
    with engine.begin() as connection:
        connection.execute(
            delete(document_chunks).where(
                document_chunks.c.document_id == document_id,
                document_chunks.c.chunk_type == ChunkType.TEXT.value,
            )
        )
        if text_rows:
            connection.execute(insert(document_chunks), text_rows)
