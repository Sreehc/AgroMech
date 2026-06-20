from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, File, Form, UploadFile, status
from sqlalchemy import Engine, insert, select

from agromech_api.auth import UserContext, require_roles
from agromech_api.config import Settings
from agromech_api.db.enums import DocumentStatus, IngestTaskStatus, TaskType, UserRole
from agromech_api.db.models import documents, ingest_tasks
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


def register_document_routes(app, *, settings: Settings, engine: Engine) -> None:
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
