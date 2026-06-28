from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4

from sqlalchemy import Engine, delete, insert, select

from agromech_api.chunk_quality import is_referenceable_chunk
from agromech_api.config import Settings, get_settings
from agromech_api.db.enums import AssetType, ChunkType
from agromech_api.db.models import document_assets, document_chunks, documents
from agromech_api.image_ingestion import render_pdf_pages
from agromech_api.ingestion import IngestFailure
from agromech_api.paddleocr_client import (
    OcrApiError,
    OcrPage,
    OcrRegion,
    OcrResult,
    PaddleOcrApiClient,
    build_paddleocr_client,
)
from agromech_api.text_ingestion import local_file_path


# Fetches one remote crop URL and returns its bytes. Behind a seam so the
# region-asset path can be exercised offline with canned bytes, mirroring the
# OCR transport seam in ``paddleocr_client``.
RegionImageFetcher = Callable[[str], bytes]

# Layout block labels whose ``block_content`` holds full ``<table>`` HTML.
TABLE_REGION_LABELS = {"table"}


@dataclass(frozen=True)
class OcrIngestionResult:
    page_count: int
    text_chunk_count: int
    table_chunk_count: int = 0
    page_asset_count: int = 0
    region_asset_count: int = 0


def process_ocr_document(
    engine: Engine,
    document_id: str,
    *,
    client: PaddleOcrApiClient | None = None,
    settings: Settings | None = None,
    ocr_result: OcrResult | None = None,
    optional_payload: dict[str, object] | None = None,
    persist_visual: bool = False,
    asset_root: Path | None = None,
    region_image_fetcher: RegionImageFetcher | None = None,
) -> OcrIngestionResult:
    """Recognize a document via the PaddleOCR cloud API and persist evidence.

    Always builds TEXT chunks from each page's recognized Markdown. When
    ``persist_visual`` is set, it additionally preserves the multimodal evidence
    the API returns:

    - ``table`` layout blocks → TABLE chunks whose ``content`` keeps the full
      ``<table>`` HTML (row/column structure intact, not flattened to text).
    - rendered page images → ``PAGE_IMAGE`` assets, and the API's cropped
      figure/table regions → ``EXTRACTED_IMAGE`` assets, linked to their parent
      page through ``source_locator`` (page↔region parent/child).

    ``ocr_result`` / ``region_image_fetcher`` are injectable so the persistence
    path runs offline in tests without touching the network. Failures surface as
    ``IngestFailure(stage=...)`` so the ingest state machine records them.
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
    table_rows: list[dict[str, object]] = []
    for page in ocr_result.pages:
        page_number = page.page_index + 1
        text_row = _text_chunk_row(document_id, page, page_number)
        if text_row is not None:
            text_rows.append(text_row)
        if persist_visual:
            table_rows.extend(_table_chunk_rows(document_id, page, page_number))

    if not text_rows and not table_rows:
        raise IngestFailure(
            "ocr_no_text_extracted",
            "OCR produced no referenceable text content",
            stage="ocr",
        )

    _replace_chunks(engine, document_id, text_rows, table_rows)

    page_asset_count = 0
    region_asset_count = 0
    if persist_visual:
        page_asset_count, region_asset_count = _persist_visual_assets(
            engine,
            document_id,
            ocr_result,
            settings=settings,
            asset_root=asset_root,
            region_image_fetcher=region_image_fetcher,
        )

    return OcrIngestionResult(
        page_count=len(ocr_result.pages),
        text_chunk_count=len(text_rows),
        table_chunk_count=len(table_rows),
        page_asset_count=page_asset_count,
        region_asset_count=region_asset_count,
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


def _table_chunk_rows(document_id: str, page: OcrPage, page_number: int) -> list[dict[str, object]]:
    """One TABLE chunk per ``table`` layout block, HTML structure preserved.

    The API returns full ``<table>`` HTML in ``block_content`` (rows/columns
    restored). We keep that HTML verbatim as the chunk content so retrieval and
    answering can use the cell relationships, not a flattened plain-text dump.
    """
    rows: list[dict[str, object]] = []
    for region in page.regions:
        if region.region_type not in TABLE_REGION_LABELS:
            continue
        html = region.text.strip()
        source_locator = {
            "type": "ocr_table",
            "page": page_number,
            "region_type": region.region_type,
            "bbox": region.bbox,
        }
        if not is_referenceable_chunk(html, source_locator):
            continue
        rows.append(
            {
                "id": str(uuid4()),
                "document_id": document_id,
                "chunk_type": ChunkType.TABLE.value,
                "content": html,
                "summary": html[:240],
                "page_number": page_number,
                "source_locator": source_locator,
            }
        )
    return rows


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


def _replace_chunks(
    engine: Engine,
    document_id: str,
    text_rows: list[dict[str, object]],
    table_rows: list[dict[str, object]],
) -> None:
    with engine.begin() as connection:
        connection.execute(
            delete(document_chunks).where(
                document_chunks.c.document_id == document_id,
                document_chunks.c.chunk_type.in_(
                    [ChunkType.TEXT.value, ChunkType.TABLE.value]
                ),
            )
        )
        if text_rows:
            connection.execute(insert(document_chunks), text_rows)
        if table_rows:
            connection.execute(insert(document_chunks), table_rows)


def _persist_visual_assets(
    engine: Engine,
    document_id: str,
    ocr_result: OcrResult,
    *,
    settings: Settings,
    asset_root: Path | None,
    region_image_fetcher: RegionImageFetcher | None,
) -> tuple[int, int]:
    """Persist page images and their child region crops as document assets.

    page level: each PDF page is rendered locally (PyMuPDF) into a ``PAGE_IMAGE``
    asset. region level: the API's cropped figure/table images are downloaded
    into ``EXTRACTED_IMAGE`` assets that point back at their parent page through
    ``source_locator['parent']`` (page↔region parent/child).
    """
    with engine.connect() as connection:
        document = connection.execute(
            select(documents).where(documents.c.id == document_id)
        ).mappings().one()

    source_path = local_file_path(document["storage_uri"])
    rendered_pages = dict(
        render_pdf_pages(document_id, source_path, asset_root=asset_root)
    )

    region_dir = source_path.parent / ".agromech-assets" / document_id / "regions"
    fetcher = region_image_fetcher or _fetch_region_image

    page_rows: list[dict[str, object]] = []
    region_rows: list[dict[str, object]] = []
    for page in ocr_result.pages:
        page_number = page.page_index + 1
        page_asset_id = str(uuid4())
        page_path = rendered_pages.get(page_number)
        if page_path is None:
            # No rendered page (e.g. non-PDF source); skip the page asset but
            # still allow region crops to be linked logically by page number.
            page_storage_uri = None
        else:
            page_storage_uri = f"file://{page_path}"
            page_rows.append(
                {
                    "id": page_asset_id,
                    "document_id": document_id,
                    "asset_type": AssetType.PAGE_IMAGE.value,
                    "storage_uri": page_storage_uri,
                    "mime_type": "image/png",
                    "page_number": page_number,
                    "source_locator": {
                        "type": "ocr_page",
                        "source_file": document["original_file_name"],
                        "page": page_number,
                        "width": page.width,
                        "height": page.height,
                    },
                    "ocr_text": page.markdown or None,
                    "visual_observation": None,
                }
            )

        for region in page.regions:
            region_row = _region_asset_row(
                document_id,
                page,
                page_number,
                region,
                parent_asset_id=page_asset_id if page_storage_uri else None,
                region_dir=region_dir,
                fetcher=fetcher,
            )
            if region_row is not None:
                region_rows.append(region_row)

    _replace_visual_assets(engine, document_id, page_rows, region_rows)
    return len(page_rows), len(region_rows)


def _region_asset_row(
    document_id: str,
    page: OcrPage,
    page_number: int,
    region: OcrRegion,
    *,
    parent_asset_id: str | None,
    region_dir: Path,
    fetcher: RegionImageFetcher,
) -> dict[str, object] | None:
    if not region.image_url:
        return None
    try:
        content = fetcher(region.image_url)
    except OcrApiError:
        # A single crop that cannot be downloaded is non-fatal: keep the page
        # and other regions; the figure simply will not have a stored crop.
        return None
    if not content:
        return None

    region_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(region.image_path or region.image_url).suffix or ".jpg"
    region_path = region_dir / f"page-{page_number}-region-{uuid4().hex}{suffix}"
    region_path.write_bytes(content)

    source_locator: dict[str, object] = {
        "type": "ocr_region",
        "page": page_number,
        "region_type": region.region_type,
        "bbox": region.bbox,
    }
    if parent_asset_id is not None:
        source_locator["parent"] = {
            "asset_id": parent_asset_id,
            "asset_type": AssetType.PAGE_IMAGE.value,
            "page": page_number,
        }
    return {
        "id": str(uuid4()),
        "document_id": document_id,
        "asset_type": AssetType.EXTRACTED_IMAGE.value,
        "storage_uri": f"file://{region_path}",
        "mime_type": _guess_mime(suffix),
        "page_number": page_number,
        "source_locator": source_locator,
        "ocr_text": region.text or None,
        "visual_observation": None,
    }


def _replace_visual_assets(
    engine: Engine,
    document_id: str,
    page_rows: list[dict[str, object]],
    region_rows: list[dict[str, object]],
) -> None:
    with engine.begin() as connection:
        connection.execute(
            delete(document_assets)
            .where(document_assets.c.document_id == document_id)
            .where(
                document_assets.c.asset_type.in_(
                    [AssetType.PAGE_IMAGE.value, AssetType.EXTRACTED_IMAGE.value]
                )
            )
        )
        if page_rows:
            connection.execute(insert(document_assets), page_rows)
        if region_rows:
            connection.execute(insert(document_assets), region_rows)


def _fetch_region_image(url: str) -> bytes:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30.0) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise OcrApiError(f"Region image fetch failed with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise OcrApiError(f"Region image fetch failed: {exc.reason}") from exc


def _guess_mime(suffix: str) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix.lower(), "image/jpeg")
