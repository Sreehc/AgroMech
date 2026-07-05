from __future__ import annotations

from fastapi import Depends, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import Engine, desc, func, or_, select

from agromech_api.security.auth import UserContext, require_roles
from agromech_api.core.config import Settings
from agromech_api.db.enums import DocumentStatus, TaskType, UserRole
from agromech_api.db.models import document_chunks, documents, ingest_tasks
from agromech_api.documents.service import (
    chunk_summary,
    create_delete_task,
    create_document_upload,
    create_reprocess_task,
    document_preview,
    document_summary,
    get_document_page_asset_or_404,
    task_payload,
)
from agromech_api.core.errors import AppError, ErrorCode
from agromech_api.integrations.queue.task_queue import TaskMessage, TaskPublisher


READ_ROLES = (UserRole.ADMIN, UserRole.MAINTAINER, UserRole.USER, UserRole.EVALUATOR)


def visibility_filter(user: UserContext):
    # 可见性统一规则：任何角色都只能看到公用文档或本人拥有的私有文档。
    # 私库仅归属者本人可见（含 admin/maintainer），与检索侧保持一致。
    if user.user_id is None:
        return documents.c.visibility == "public"
    return or_(
        documents.c.visibility == "public",
        documents.c.owner_user_id == user.user_id,
    )


def assert_document_visible(document, user: UserContext) -> None:
    # 单文档访问的可见性校验：不可见时按 404 处理，避免泄露私有文档是否存在。
    if document["visibility"] == "public":
        return
    if user.user_id is not None and document["owner_user_id"] == user.user_id:
        return
    raise AppError(ErrorCode.NOT_FOUND, "Document not found", status_code=status.HTTP_404_NOT_FOUND)


def require_visible_document(engine: Engine, document_id: str, user: UserContext):
    # 加载文档并校验可见性；不存在或不可见都按 404 处理。
    with engine.connect() as connection:
        document = connection.execute(
            select(documents).where(documents.c.id == document_id)
        ).mappings().one_or_none()
    if document is None:
        raise AppError(ErrorCode.NOT_FOUND, "Document not found", status_code=status.HTTP_404_NOT_FOUND)
    assert_document_visible(document, user)
    return document


def can_manage_public_library(user: UserContext) -> bool:
    return user.role in {UserRole.ADMIN, UserRole.MAINTAINER}


def require_mutable_document(engine: Engine, document_id: str, user: UserContext):
    # 变更权限：admin/maintainer 可操作任意文档；其他角色仅能操作本人拥有的文档。
    # 先按可见性加载（不可见按 404），再判定是否可变更（不可变更按 403）。
    document = require_visible_document(engine, document_id, user)
    if can_manage_public_library(user):
        return document
    if user.user_id is not None and document["owner_user_id"] == user.user_id:
        return document
    raise AppError(
        ErrorCode.FORBIDDEN,
        "You do not have permission to modify this document",
        status_code=status.HTTP_403_FORBIDDEN,
    )


def register_document_routes(app, *, settings: Settings, engine: Engine, task_publisher: TaskPublisher) -> None:
    @app.get("/documents", tags=["documents"])
    def list_documents(
        brand: str | None = None,
        model: str | None = None,
        document_type: str | None = None,
        language: str | None = None,
        status_filter: str | None = Query(default=None, alias="status"),
        user: UserContext = Depends(require_roles(*READ_ROLES)),
    ) -> dict[str, object]:
        filters = [visibility_filter(user)]
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
            items = []
            for row in rows:
                latest_chunk = connection.execute(
                    select(document_chunks.c.summary)
                    .where(
                        document_chunks.c.document_id == row["id"],
                        document_chunks.c.summary.is_not(None),
                    )
                    .order_by(document_chunks.c.created_at)
                    .limit(1)
                ).mappings().one_or_none()
                recent_task = connection.execute(
                    select(ingest_tasks)
                    .where(ingest_tasks.c.document_id == row["id"])
                    .order_by(desc(ingest_tasks.c.created_at))
                    .limit(1)
                ).mappings().one_or_none()
                enriched_row = dict(row)
                enriched_row["summary"] = latest_chunk["summary"] if latest_chunk else None
                enriched_row["recent_task"] = (
                    {
                        "id": recent_task["id"],
                        "task_type": recent_task["task_type"],
                        "status": recent_task["status"],
                        "stage": recent_task["stage"],
                    }
                    if recent_task
                    else None
                )
                items.append(document_summary(enriched_row))

        return {"total": total, "items": items}

    @app.get("/documents/{document_id}/preview", tags=["documents"])
    def preview_document(
        document_id: str,
        chunk_id: str | None = None,
        user: UserContext = Depends(require_roles(*READ_ROLES)),
    ) -> dict[str, object]:
        require_visible_document(engine, document_id, user)
        return document_preview(engine, document_id, chunk_id)

    @app.get("/documents/{document_id}/assets/{asset_id}", tags=["documents"])
    def get_document_asset(
        document_id: str,
        asset_id: str,
        user: UserContext = Depends(require_roles(*READ_ROLES)),
    ) -> FileResponse:
        require_visible_document(engine, document_id, user)
        asset, path = get_document_page_asset_or_404(engine, document_id, asset_id)
        return FileResponse(path, media_type=asset["mime_type"] or "application/octet-stream")

    @app.get("/documents/{document_id}", tags=["documents"])
    def document_detail(
        document_id: str,
        user: UserContext = Depends(require_roles(*READ_ROLES)),
    ) -> dict[str, object]:
        with engine.connect() as connection:
            document = connection.execute(
                select(documents).where(documents.c.id == document_id)
            ).mappings().one_or_none()
            if document is None:
                raise AppError(ErrorCode.NOT_FOUND, "Document not found", status_code=status.HTTP_404_NOT_FOUND)
            assert_document_visible(document, user)
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
            "visibility": document["visibility"],
            "owner_user_id": document["owner_user_id"],
            "accessible": document["status"] != DocumentStatus.DELETED.value,
            "failure": {
                "stage": document["failure_stage"],
                "code": document["failure_code"],
                "message": document["failure_message"],
            },
            "recent_task": task_payload(recent_task) if recent_task else None,
            "chunks": [
                {
                    **chunk_summary(row),
                    "accessible": document["status"] != DocumentStatus.DELETED.value,
                }
                for row in chunks
            ],
            "updated_at": document["updated_at"].isoformat() if document["updated_at"] else None,
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
        user: UserContext = Depends(require_roles(*READ_ROLES)),
    ) -> dict[str, str]:
        require_mutable_document(engine, document_id, user)
        result = create_reprocess_task(engine, document_id)
        task_publisher.publish(
            TaskMessage(
                task_id=result.task_id,
                document_id=result.document_id,
                task_type=TaskType.REPROCESS.value,
            )
        )
        return {
            "document_id": result.document_id,
            "task_id": result.task_id,
            "status": result.status,
        }

    @app.delete("/documents/{document_id}", tags=["documents"])
    def delete_document(
        document_id: str,
        user: UserContext = Depends(require_roles(*READ_ROLES)),
    ) -> dict[str, str]:
        require_mutable_document(engine, document_id, user)
        result = create_delete_task(engine, document_id)
        task_publisher.publish(
            TaskMessage(
                task_id=result.task_id,
                document_id=result.document_id,
                task_type=TaskType.DELETE.value,
            )
        )
        return {
            "document_id": result.document_id,
            "status": DocumentStatus.DELETING.value,
        }

    @app.post("/documents", status_code=status.HTTP_201_CREATED, tags=["documents"])
    async def upload_document(
        file: UploadFile = File(...),
        brand: str | None = Form(default=None),
        model: str | None = Form(default=None),
        document_type: str | None = Form(default=None),
        language: str | None = Form(default=None),
        source: str | None = Form(default=None),
        visibility: str = Form(default="private"),
        user: UserContext = Depends(require_roles(*READ_ROLES)),
    ) -> dict[str, str | None]:
        # 公用库上传仅限 admin/maintainer；普通 user/evaluator 只能传到本人私库。
        # 越权请求公用可见性时直接拒绝，而非静默降级，避免误判归属。
        if visibility == "public" and not can_manage_public_library(user):
            raise AppError(
                ErrorCode.FORBIDDEN,
                "Only administrators and maintainers can publish to the public library",
                status_code=status.HTTP_403_FORBIDDEN,
            )
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
            visibility=visibility,
        )
        task_publisher.publish(
            TaskMessage(
                task_id=result.task_id,
                document_id=result.document_id,
                task_type=TaskType.INGEST.value,
            )
        )
        return {
            "document_id": result.document_id,
            "task_id": result.task_id,
            "status": result.status,
            "duplicate_of": result.duplicate_of,
        }
