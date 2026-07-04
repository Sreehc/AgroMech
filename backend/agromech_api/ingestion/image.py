from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine, delete, insert, select

from agromech_api.ingestion.chunk_quality import is_referenceable_chunk
from agromech_api.db.enums import AssetType, ChunkType
from agromech_api.db.models import document_assets, document_chunks, documents
from agromech_api.ingestion.runner import IngestFailure
from agromech_api.ingestion.text import local_file_path


OcrReader = Callable[[Path], str]

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}


class OcrUnavailable(RuntimeError):
    """Raised when the configured OCR engine cannot be loaded."""


@dataclass(frozen=True)
class ImageAssetCandidate:
    asset_type: str
    storage_uri: str
    mime_type: str
    source_locator: dict[str, object]
    page_number: int | None = None
    ocr_text: str | None = None
    visual_observation: dict[str, object] | None = None


@dataclass(frozen=True)
class ImageIngestionResult:
    asset_count: int
    chunk_count: int


def is_image_document(filename: str, mime_type: str) -> bool:
    return Path(filename).suffix.lower() in IMAGE_EXTENSIONS or mime_type in IMAGE_MIME_TYPES


def is_pdf_document(filename: str, mime_type: str) -> bool:
    return Path(filename).suffix.lower() == ".pdf" or mime_type == "application/pdf"


def process_image_document(
    engine: Engine,
    document_id: str,
    *,
    ocr_reader: OcrReader | None = None,
    fail_on_all_ocr_failure: bool = True,
    asset_root: Path | None = None,
) -> ImageIngestionResult:
    with engine.connect() as connection:
        document = connection.execute(
            select(documents).where(documents.c.id == document_id)
        ).mappings().one()

    source_path = local_file_path(document["storage_uri"])
    if not source_path.exists():
        raise IngestFailure("source_file_missing", "Source file is missing", stage="parse")

    reader = ocr_reader or default_ocr_reader
    if is_pdf_document(document["original_file_name"], document["mime_type"]):
        assets = pdf_page_assets(
            document_id,
            source_path,
            document["original_file_name"],
            reader,
            asset_root=asset_root,
        )
    elif is_image_document(document["original_file_name"], document["mime_type"]):
        assets = source_image_assets(source_path, document["original_file_name"], document["mime_type"], reader)
    else:
        raise IngestFailure(
            "unsupported_image_type",
            f"Unsupported image document type: {document['mime_type']}",
            stage="parse",
        )

    if not assets:
        raise IngestFailure("no_image_assets", "No image assets extracted", stage="render")

    chunkable_assets = [asset for asset in assets if asset.ocr_text]
    if not chunkable_assets and fail_on_all_ocr_failure:
        insert_diagnostic_assets(engine, document_id, assets)
        raise IngestFailure("ocr_failed", "OCR failed for all image assets", stage="ocr")

    return replace_image_assets_and_chunks(engine, document_id, assets)


def source_image_assets(
    source_path: Path,
    original_file_name: str,
    mime_type: str,
    ocr_reader: OcrReader,
) -> list[ImageAssetCandidate]:
    source_locator = {"type": "image", "source_file": original_file_name}
    ocr_text, visual_observation = read_ocr(source_path, ocr_reader)
    return [
        ImageAssetCandidate(
            asset_type=AssetType.SOURCE_IMAGE.value,
            storage_uri=f"file://{source_path}",
            mime_type=mime_type,
            source_locator=source_locator,
            ocr_text=ocr_text,
            visual_observation=visual_observation,
        )
    ]


def pdf_page_assets(
    document_id: str,
    source_path: Path,
    original_file_name: str,
    ocr_reader: OcrReader,
    *,
    asset_root: Path | None = None,
) -> list[ImageAssetCandidate]:
    rendered_pages = render_pdf_pages(document_id, source_path, asset_root=asset_root)
    assets: list[ImageAssetCandidate] = []
    for page_number, page_path in rendered_pages:
        source_locator = {
            "type": "pdf_page",
            "source_file": original_file_name,
            "page": page_number,
        }
        ocr_text, visual_observation = read_ocr(page_path, ocr_reader)
        assets.append(
            ImageAssetCandidate(
                asset_type=AssetType.PAGE_IMAGE.value,
                storage_uri=f"file://{page_path}",
                mime_type="image/png",
                source_locator=source_locator,
                page_number=page_number,
                ocr_text=ocr_text,
                visual_observation=visual_observation,
            )
        )
    return assets


def render_pdf_pages(
    document_id: str,
    source_path: Path,
    *,
    asset_root: Path | None = None,
) -> list[tuple[int, Path]]:
    try:
        import fitz
    except ImportError as exc:
        raise IngestFailure("pdf_render_unavailable", "PyMuPDF is not installed", stage="render") from exc

    if asset_root is None:
        output_dir = source_path.parent / ".agromech-assets" / document_id
    else:
        output_dir = asset_root / "rendered-pages" / document_id
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[tuple[int, Path]] = []
    try:
        with fitz.open(source_path) as pdf:
            for index, page in enumerate(pdf, start=1):
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                output_path = output_dir / f"page-{index}.png"
                pixmap.save(output_path)
                rendered.append((index, output_path))
    except Exception as exc:
        raise IngestFailure("pdf_render_failed", "PDF pages could not be rendered", stage="render") from exc
    return rendered


def read_ocr(path: Path, ocr_reader: OcrReader) -> tuple[str | None, dict[str, object]]:
    try:
        text = ocr_reader(path).strip()
    except OcrUnavailable as exc:
        return (
            None,
            {
                "ocr": {
                    "status": "failed",
                    "error_code": "ocr_unavailable",
                    "error_message": str(exc),
                }
            },
        )
    except Exception as exc:
        return (
            None,
            {
                "ocr": {
                    "status": "failed",
                    "error_code": "ocr_failed",
                    "error_message": str(exc),
                }
            },
        )
    if not text:
        return (
            None,
            {
                "ocr": {
                    "status": "failed",
                    "error_code": "ocr_empty",
                    "error_message": "OCR returned no text",
                }
            },
        )
    return text, {"ocr": {"status": "succeeded"}}


def default_ocr_reader(path: Path) -> str:
    try:
        from paddleocr import PaddleOCR
    except ImportError as exc:
        raise OcrUnavailable("PaddleOCR is not installed") from exc

    result = PaddleOCR(use_angle_cls=True, lang="ch").ocr(str(path), cls=True)
    lines: list[str] = []
    for page_result in result or []:
        for item in page_result or []:
            if len(item) >= 2 and isinstance(item[1], (list, tuple)) and item[1]:
                lines.append(str(item[1][0]))
    return "\n".join(lines)


def insert_diagnostic_assets(
    engine: Engine,
    document_id: str,
    assets: list[ImageAssetCandidate],
) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(document_assets),
            [asset_row(document_id, asset, str(uuid4())) for asset in assets],
        )


def replace_image_assets_and_chunks(
    engine: Engine,
    document_id: str,
    assets: list[ImageAssetCandidate],
) -> ImageIngestionResult:
    asset_rows = []
    chunk_rows = []
    for asset in assets:
        asset_id = str(uuid4())
        asset_rows.append(asset_row(document_id, asset, asset_id))
        if is_referenceable_chunk(asset.ocr_text, asset.source_locator):
            chunk_rows.append(
                {
                    "id": str(uuid4()),
                    "document_id": document_id,
                    "asset_id": asset_id,
                    "chunk_type": ChunkType.IMAGE.value,
                    "content": asset.ocr_text,
                    "summary": asset.ocr_text[:240],
                    "page_number": asset.page_number,
                    "source_locator": asset.source_locator,
                }
            )

    with engine.begin() as connection:
        connection.execute(
            delete(document_chunks).where(
                document_chunks.c.document_id == document_id,
                document_chunks.c.chunk_type == ChunkType.IMAGE.value,
            )
        )
        connection.execute(
            delete(document_assets).where(document_assets.c.document_id == document_id)
            .where(
                document_assets.c.asset_type.in_(
                    [
                        AssetType.PAGE_IMAGE.value,
                        AssetType.SOURCE_IMAGE.value,
                        AssetType.EXTRACTED_IMAGE.value,
                    ]
                )
            )
        )
        if asset_rows:
            connection.execute(insert(document_assets), asset_rows)
        if chunk_rows:
            connection.execute(insert(document_chunks), chunk_rows)
    return ImageIngestionResult(asset_count=len(asset_rows), chunk_count=len(chunk_rows))


def asset_row(
    document_id: str,
    asset: ImageAssetCandidate,
    asset_id: str,
) -> dict[str, object]:
    return {
        "id": asset_id,
        "document_id": document_id,
        "asset_type": asset.asset_type,
        "storage_uri": asset.storage_uri,
        "mime_type": asset.mime_type,
        "page_number": asset.page_number,
        "source_locator": asset.source_locator,
        "ocr_text": asset.ocr_text,
        "visual_observation": asset.visual_observation,
    }
