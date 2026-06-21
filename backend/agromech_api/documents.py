from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4

from fastapi import Depends, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import Engine, desc, func, insert, select, update

from agromech_api.auth import UserContext, require_roles
from agromech_api.config import Settings
from agromech_api.db.enums import AssetType, DocumentStatus, IngestTaskStatus, TaskType, UserRole
from agromech_api.db.models import document_assets, document_chunks, documents, ingest_tasks
from agromech_api.errors import AppError, ErrorCode
from agromech_api.file_storage import LocalFileStorage


SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".xlsx",
    ".csv",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".md",
    ".markdown",
    ".txt",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


@dataclass(frozen=True)
class UploadResult:
    document_id: str
    task_id: str
    status: str


@dataclass(frozen=True)
class TaskResult:
    document_id: str
    task_id: str
    status: str


def extension_for(filename: str) -> str:
    return Path(filename).suffix.lower()


def validate_supported_file(filename: str) -> str:
    extension = extension_for(filename)
    if extension not in SUPPORTED_EXTENSIONS:
        raise AppError(
            ErrorCode.UNSUPPORTED_FILE_TYPE,
            "Unsupported file type",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            details={"extension": extension or None},
        )
    return extension


def validate_file_size(extension: str, content: bytes, settings: Settings) -> None:
    limit_mb = settings.upload_max_image_size_mb if extension in IMAGE_EXTENSIONS else settings.upload_max_file_size_mb
    limit_bytes = limit_mb * 1024 * 1024
    if len(content) > limit_bytes:
        raise AppError(
            ErrorCode.FILE_TOO_LARGE,
            "File size exceeds configured limit",
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            details={"limit_bytes": limit_bytes},
        )


def content_type_for(filename: str, provided_content_type: str | None) -> str:
    guessed, _encoding = mimetypes.guess_type(filename)
    return provided_content_type or guessed or "application/octet-stream"


def create_document_upload(
    *,
    engine: Engine,
    settings: Settings,
    user: UserContext,
    filename: str,
    content_type: str | None,
    content: bytes,
    brand: str | None,
    model: str | None,
    document_type: str | None,
    language: str | None,
    source: str | None,
) -> UploadResult:
    extension = validate_supported_file(filename)
    validate_file_size(extension, content, settings)

    file_hash = hashlib.sha256(content).hexdigest()
    storage = LocalFileStorage(settings.local_file_storage_path)

    with engine.begin() as connection:
        duplicate = connection.execute(
            select(documents.c.id).where(documents.c.file_hash == file_hash).limit(1)
        ).scalar_one_or_none()
        if duplicate:
            raise AppError(
                ErrorCode.DUPLICATE_OF,
                "Duplicate file",
                status_code=status.HTTP_409_CONFLICT,
                details={"document_id": duplicate},
            )

        stored_file = storage.save(file_hash=file_hash, original_name=filename, content=content)
        document_id = str(uuid4())
        task_id = str(uuid4())
        connection.execute(
            insert(documents).values(
                id=document_id,
                title=Path(filename).stem,
                original_file_name=filename,
                file_hash=file_hash,
                file_size_bytes=len(content),
                mime_type=content_type_for(filename, content_type),
                storage_uri=stored_file.uri,
                brand=brand,
                model=model,
                document_type=document_type,
                language=language,
                source=source,
                status=DocumentStatus.QUEUED.value,
                created_by_role=user.role.value,
            )
        )
        connection.execute(
            insert(ingest_tasks).values(
                id=task_id,
                document_id=document_id,
                task_type=TaskType.INGEST.value,
                status=IngestTaskStatus.QUEUED.value,
                attempt_count=0,
                stage="queued",
            )
        )

    return UploadResult(document_id=document_id, task_id=task_id, status=DocumentStatus.QUEUED.value)


def document_summary(row) -> dict[str, object]:
    return {
        "id": row["id"],
        "title": row["title"],
        "original_file_name": row["original_file_name"],
        "brand": row["brand"],
        "model": row["model"],
        "document_type": row["document_type"],
        "language": row["language"],
        "status": row["status"],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


def task_payload(row) -> dict[str, object]:
    return {
        "id": row["id"],
        "document_id": row["document_id"],
        "task_type": row["task_type"],
        "status": row["status"],
        "attempt_count": row["attempt_count"],
        "stage": row["stage"],
        "error_code": row["error_code"],
        "error_message": row["error_message"],
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
    }


def chunk_summary(row) -> dict[str, object]:
    return {
        "id": row["id"],
        "chunk_type": row["chunk_type"],
        "summary": row["summary"],
        "page_number": row["page_number"],
        "section_title": row["section_title"],
    }


def source_position(row) -> dict[str, object]:
    return {
        "page_number": row["page_number"],
        "section_title": row["section_title"],
        "worksheet_name": row["worksheet_name"],
        "row_start": row["row_start"],
        "row_end": row["row_end"],
    }


def evidence_snippet(content: str, limit: int = 500) -> str:
    normalized = " ".join(content.split())
    return normalized[:limit]


def local_asset_path(storage_uri: str) -> Path:
    parsed = urlparse(storage_uri)
    if parsed.scheme != "file":
        raise AppError(
            ErrorCode.NOT_FOUND,
            "Document asset file not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return Path(unquote(parsed.path))


def page_asset_payload(document_id: str, asset) -> dict[str, object]:
    if asset is None:
        return {
            "page_image_url": None,
            "render_status": "not_rendered",
        }
    path = local_asset_path(asset["storage_uri"])
    if not path.exists():
        return {
            "page_image_url": None,
            "render_status": "missing",
        }
    return {
        "page_image_url": f"/documents/{document_id}/assets/{asset['id']}",
        "render_status": "rendered",
    }


def area_highlight_from_locator(source_locator: dict[str, object], page_number: int | None) -> dict[str, object] | None:
    bbox = source_locator.get("bbox")
    if not isinstance(bbox, dict):
        return None

    required_keys = ("x", "y", "width", "height")
    if not all(isinstance(bbox.get(key), (int, float)) for key in required_keys):
        return None

    normalized_bbox = {key: float(bbox[key]) for key in required_keys}
    return {
        "type": "area",
        "page_number": page_number,
        "source_locator": source_locator,
        "bbox": normalized_bbox,
    }


def preview_payload(
    document,
    chunk,
    *,
    accessible: bool,
    reason: str | None = None,
    page_asset=None,
) -> dict[str, object]:
    snippet = evidence_snippet(chunk["content"]) if chunk else None
    source_locator = chunk["source_locator"] if chunk else {}
    position = source_position(chunk) if chunk else {
        "page_number": None,
        "section_title": None,
        "worksheet_name": None,
        "row_start": None,
        "row_end": None,
    }
    is_pdf = document["mime_type"] == "application/pdf"
    preview_type = "unavailable" if not accessible else ("pdf" if is_pdf else "text")
    pdf_asset = page_asset_payload(document["id"], page_asset) if accessible and is_pdf else None
    highlights = []
    if accessible and snippet:
        highlights.append(
            {
                "type": "text",
                "text": snippet,
                "page_number": position["page_number"],
                "source_locator": source_locator,
            }
        )
        if is_pdf:
            area_highlight = area_highlight_from_locator(source_locator, position["page_number"])
            if area_highlight:
                highlights.append(area_highlight)

    return {
        "document_id": document["id"],
        "document_title": document["title"],
        "chunk_id": chunk["id"] if chunk else None,
        "preview_type": preview_type,
        "accessible": accessible,
        "source_locator": source_locator,
        "source_position": position,
        "evidence_snippet": snippet,
        "text_preview": snippet if accessible and not is_pdf else None,
        "pdf_page": {
            "page_number": position["page_number"],
            "page_image_url": pdf_asset["page_image_url"] if pdf_asset else None,
            "render_status": pdf_asset["render_status"] if pdf_asset else "not_rendered",
        }
        if accessible and is_pdf
        else None,
        "highlights": highlights,
        "unavailable_reason": reason,
    }


def document_preview(engine: Engine, document_id: str, chunk_id: str | None = None) -> dict[str, object]:
    with engine.connect() as connection:
        document = connection.execute(
            select(documents).where(documents.c.id == document_id)
        ).mappings().one_or_none()
        if document is None:
            raise AppError(ErrorCode.NOT_FOUND, "Document not found", status_code=status.HTTP_404_NOT_FOUND)

        chunk_filters = [document_chunks.c.document_id == document_id]
        if chunk_id:
            chunk_filters.append(document_chunks.c.id == chunk_id)
        chunk = connection.execute(
            select(document_chunks).where(*chunk_filters).order_by(document_chunks.c.created_at).limit(1)
        ).mappings().one_or_none()
        page_asset = None
        if chunk and document["mime_type"] == "application/pdf" and chunk["page_number"] is not None:
            page_asset = connection.execute(
                select(document_assets)
                .where(
                    document_assets.c.document_id == document_id,
                    document_assets.c.asset_type == AssetType.PAGE_IMAGE.value,
                    document_assets.c.page_number == chunk["page_number"],
                )
                .limit(1)
            ).mappings().one_or_none()

    if chunk is None:
        return preview_payload(document, None, accessible=False, reason="chunk_not_found")
    if document["status"] == DocumentStatus.DELETED.value:
        return preview_payload(document, chunk, accessible=False, reason="document_deleted")
    if document["mime_type"] == "application/pdf":
        if chunk["page_number"] is None:
            return preview_payload(document, chunk, accessible=False, reason="pdf_page_locator_missing")
        if page_asset is not None and page_asset_payload(document_id, page_asset)["render_status"] == "missing":
            return preview_payload(document, chunk, accessible=False, reason="pdf_page_file_missing")

    return preview_payload(document, chunk, accessible=True, page_asset=page_asset)


def get_document_page_asset_or_404(engine: Engine, document_id: str, asset_id: str):
    with engine.connect() as connection:
        asset = connection.execute(
            select(document_assets).where(
                document_assets.c.id == asset_id,
                document_assets.c.document_id == document_id,
                document_assets.c.asset_type == AssetType.PAGE_IMAGE.value,
            )
        ).mappings().one_or_none()
    if asset is None:
        raise AppError(ErrorCode.NOT_FOUND, "Document asset not found", status_code=status.HTTP_404_NOT_FOUND)

    path = local_asset_path(asset["storage_uri"])
    if not path.exists():
        raise AppError(ErrorCode.NOT_FOUND, "Document asset file not found", status_code=status.HTTP_404_NOT_FOUND)
    return asset, path


def get_document_or_404(engine: Engine, document_id: str):
    with engine.connect() as connection:
        row = connection.execute(
            select(documents).where(documents.c.id == document_id)
        ).mappings().one_or_none()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "Document not found", status_code=status.HTTP_404_NOT_FOUND)
    return row


def create_reprocess_task(engine: Engine, document_id: str) -> TaskResult:
    get_document_or_404(engine, document_id)
    task_id = str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            insert(ingest_tasks).values(
                id=task_id,
                document_id=document_id,
                task_type=TaskType.REPROCESS.value,
                status=IngestTaskStatus.QUEUED.value,
                attempt_count=0,
                stage="queued",
            )
        )
        connection.execute(
            update(documents)
            .where(documents.c.id == document_id)
            .values(status=DocumentStatus.REPROCESSING.value, updated_at=datetime.now(UTC))
        )
    return TaskResult(document_id=document_id, task_id=task_id, status=IngestTaskStatus.QUEUED.value)


def create_delete_task(engine: Engine, document_id: str) -> TaskResult:
    get_document_or_404(engine, document_id)
    task_id = str(uuid4())
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            insert(ingest_tasks).values(
                id=task_id,
                document_id=document_id,
                task_type=TaskType.DELETE.value,
                status=IngestTaskStatus.QUEUED.value,
                attempt_count=0,
                stage="queued",
            )
        )
        connection.execute(
            update(documents)
            .where(documents.c.id == document_id)
            .values(status=DocumentStatus.DELETING.value, updated_at=now)
        )
    return TaskResult(document_id=document_id, task_id=task_id, status=IngestTaskStatus.QUEUED.value)


def mark_document_deleted(engine: Engine, document_id: str) -> dict[str, str]:
    get_document_or_404(engine, document_id)
    with engine.begin() as connection:
        connection.execute(
            update(documents)
            .where(documents.c.id == document_id)
            .values(
                status=DocumentStatus.DELETED.value,
                deleted_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
    return {"document_id": document_id, "status": DocumentStatus.DELETED.value}


def register_document_routes(app, *, settings: Settings, engine: Engine) -> None:
    @app.get("/documents", tags=["documents"])
    def list_documents(
        brand: str | None = None,
        model: str | None = None,
        document_type: str | None = None,
        language: str | None = None,
        status_filter: str | None = Query(default=None, alias="status"),
        _user: UserContext = Depends(require_roles(UserRole.ADMIN, UserRole.MAINTAINER, UserRole.USER, UserRole.EVALUATOR)),
    ) -> dict[str, object]:
        filters = []
        if brand:
            filters.append(documents.c.brand == brand)
        if model:
            filters.append(documents.c.model == model)
        if document_type:
            filters.append(documents.c.document_type == document_type)
        if language:
            filters.append(documents.c.language == language)
        if status_filter:
            filters.append(documents.c.status == status_filter)
        else:
            filters.append(documents.c.status != DocumentStatus.DELETED.value)

        query = select(documents).where(*filters).order_by(desc(documents.c.updated_at))
        total_query = select(func.count()).select_from(documents).where(*filters)
        with engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
            total = connection.execute(total_query).scalar_one()

        return {"total": total, "items": [document_summary(row) for row in rows]}

    @app.get("/documents/{document_id}/preview", tags=["documents"])
    def preview_document(
        document_id: str,
        chunk_id: str | None = None,
        _user: UserContext = Depends(require_roles(UserRole.ADMIN, UserRole.MAINTAINER, UserRole.USER, UserRole.EVALUATOR)),
    ) -> dict[str, object]:
        return document_preview(engine, document_id, chunk_id)

    @app.get("/documents/{document_id}/assets/{asset_id}", tags=["documents"])
    def get_document_asset(
        document_id: str,
        asset_id: str,
        _user: UserContext = Depends(require_roles(UserRole.ADMIN, UserRole.MAINTAINER, UserRole.USER, UserRole.EVALUATOR)),
    ) -> FileResponse:
        asset, path = get_document_page_asset_or_404(engine, document_id, asset_id)
        return FileResponse(path, media_type=asset["mime_type"] or "application/octet-stream")

    @app.get("/documents/{document_id}", tags=["documents"])
    def document_detail(
        document_id: str,
        _user: UserContext = Depends(require_roles(UserRole.ADMIN, UserRole.MAINTAINER, UserRole.USER, UserRole.EVALUATOR)),
    ) -> dict[str, object]:
        with engine.connect() as connection:
            document = connection.execute(
                select(documents).where(documents.c.id == document_id)
            ).mappings().one_or_none()
            if document is None:
                raise AppError(ErrorCode.NOT_FOUND, "Document not found", status_code=status.HTTP_404_NOT_FOUND)
            recent_task = connection.execute(
                select(ingest_tasks)
                .where(ingest_tasks.c.document_id == document_id)
                .order_by(desc(ingest_tasks.c.created_at))
                .limit(1)
            ).mappings().one_or_none()
            chunks = connection.execute(
                select(document_chunks)
                .where(document_chunks.c.document_id == document_id)
                .order_by(document_chunks.c.created_at)
                .limit(10)
            ).mappings().all()

        return {
            "id": document["id"],
            "title": document["title"],
            "metadata": {
                "brand": document["brand"],
                "model": document["model"],
                "document_type": document["document_type"],
                "language": document["language"],
                "source": document["source"],
                "original_file_name": document["original_file_name"],
                "mime_type": document["mime_type"],
                "file_size_bytes": document["file_size_bytes"],
            },
            "status": document["status"],
            "failure": {
                "stage": document["failure_stage"],
                "code": document["failure_code"],
                "message": document["failure_message"],
            },
            "recent_task": task_payload(recent_task) if recent_task else None,
            "chunks": [chunk_summary(row) for row in chunks],
        }

    @app.get("/tasks/{task_id}", tags=["tasks"])
    def get_task(
        task_id: str,
        _user: UserContext = Depends(require_roles(UserRole.ADMIN, UserRole.MAINTAINER, UserRole.USER, UserRole.EVALUATOR)),
    ) -> dict[str, object]:
        with engine.connect() as connection:
            task = connection.execute(
                select(ingest_tasks).where(ingest_tasks.c.id == task_id)
            ).mappings().one_or_none()
        if task is None:
            raise AppError(ErrorCode.NOT_FOUND, "Task not found", status_code=status.HTTP_404_NOT_FOUND)
        return task_payload(task)

    @app.post("/documents/{document_id}/reprocess", status_code=status.HTTP_201_CREATED, tags=["documents"])
    def reprocess_document(
        document_id: str,
        _user: UserContext = Depends(require_roles(UserRole.ADMIN, UserRole.MAINTAINER)),
    ) -> dict[str, str]:
        result = create_reprocess_task(engine, document_id)
        return {
            "document_id": result.document_id,
            "task_id": result.task_id,
            "status": result.status,
        }

    @app.delete("/documents/{document_id}", tags=["documents"])
    def delete_document(
        document_id: str,
        _user: UserContext = Depends(require_roles(UserRole.ADMIN, UserRole.MAINTAINER)),
    ) -> dict[str, str]:
        return mark_document_deleted(engine, document_id)

    @app.post("/documents", status_code=status.HTTP_201_CREATED, tags=["documents"])
    async def upload_document(
        file: UploadFile = File(...),
        brand: str | None = Form(default=None),
        model: str | None = Form(default=None),
        document_type: str | None = Form(default=None),
        language: str | None = Form(default=None),
        source: str | None = Form(default=None),
        user: UserContext = Depends(require_roles(UserRole.ADMIN, UserRole.MAINTAINER)),
    ) -> dict[str, str]:
        content = await file.read()
        result = create_document_upload(
            engine=engine,
            settings=settings,
            user=user,
            filename=file.filename or "upload",
            content_type=file.content_type,
            content=content,
            brand=brand,
            model=model,
            document_type=document_type,
            language=language,
            source=source,
        )
        return {
            "document_id": result.document_id,
            "task_id": result.task_id,
            "status": result.status,
        }
