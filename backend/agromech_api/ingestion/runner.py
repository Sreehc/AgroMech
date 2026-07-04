from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import status
from sqlalchemy import Engine, asc, delete, insert, select, update

from agromech_api.db.enums import DocumentStatus, IngestTaskStatus, TaskType
from agromech_api.db.models import (
    answer_citations,
    chunk_entity_links,
    chunk_search_index,
    document_assets,
    document_chunks,
    document_entity_extractions,
    documents,
    embedding_references,
    graph_edges,
    ingest_tasks,
)
from agromech_api.documents.service import TaskResult, get_document_or_404
from agromech_api.core.errors import AppError, ErrorCode


@dataclass(frozen=True)
class QueuedTask:
    id: str
    document_id: str
    task_type: str
    attempt_count: int
    stage: str | None


class IngestFailure(Exception):
    def __init__(self, code: str, message: str, *, stage: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage


class IngestTaskRunner:
    DEFAULT_MAX_ATTEMPTS = 3

    def __init__(self, engine: Engine, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> None:
        self.engine = engine
        self.max_attempts = max_attempts

    def next_queued_task(self) -> QueuedTask | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(ingest_tasks)
                .where(ingest_tasks.c.status == IngestTaskStatus.QUEUED.value)
                .order_by(asc(ingest_tasks.c.created_at))
                .limit(1)
            ).mappings().one_or_none()
        if row is None:
            return None
        return QueuedTask(
            id=row["id"],
            document_id=row["document_id"],
            task_type=row["task_type"],
            attempt_count=row["attempt_count"],
            stage=row["stage"],
        )

    def run_next(self, processor) -> str:
        task = self.next_queued_task()
        if task is None:
            return "idle"

        self.mark_processing(task)
        try:
            processor(task)
        except IngestFailure as exc:
            return self.mark_failed(task, exc)
        except Exception as exc:
            return self.mark_failed(
                task,
                IngestFailure("unexpected_error", str(exc), stage="unexpected"),
            )

        self.mark_succeeded(task)
        return "succeeded"

    def attempts_exhausted(self, task: QueuedTask) -> bool:
        # attempt_count on the task is the value before mark_processing incremented it,
        # so the just-completed attempt is task.attempt_count + 1.
        return (task.attempt_count + 1) >= self.max_attempts

    def mark_processing(self, task: QueuedTask) -> None:
        now = datetime.now(UTC)
        if task.task_type == TaskType.REPROCESS.value:
            document_status = DocumentStatus.REPROCESSING.value
        elif task.task_type == TaskType.DELETE.value:
            document_status = DocumentStatus.DELETING.value
        else:
            document_status = DocumentStatus.PROCESSING.value
        with self.engine.begin() as connection:
            connection.execute(
                update(ingest_tasks)
                .where(ingest_tasks.c.id == task.id)
                .values(
                    status=IngestTaskStatus.PROCESSING.value,
                    attempt_count=ingest_tasks.c.attempt_count + 1,
                    stage="processing",
                    error_code=None,
                    error_message=None,
                    started_at=now,
                    finished_at=None,
                    updated_at=now,
                )
            )
            connection.execute(
                update(documents)
                .where(documents.c.id == task.document_id)
                .values(status=document_status, updated_at=now)
            )

    def mark_succeeded(self, task: QueuedTask) -> None:
        now = datetime.now(UTC)
        stage = "deleted" if task.task_type == TaskType.DELETE.value else "indexed"
        document_status = (
            DocumentStatus.DELETED.value
            if task.task_type == TaskType.DELETE.value
            else DocumentStatus.INDEXED.value
        )
        with self.engine.begin() as connection:
            if task.task_type == TaskType.DELETE.value:
                cleanup_deleted_document(connection, task.document_id)
            connection.execute(
                update(ingest_tasks)
                .where(ingest_tasks.c.id == task.id)
                .values(
                    status=IngestTaskStatus.SUCCEEDED.value,
                    stage=stage,
                    error_code=None,
                    error_message=None,
                    finished_at=now,
                    updated_at=now,
                )
            )
            connection.execute(
                update(documents)
                .where(documents.c.id == task.document_id)
                .values(
                    status=document_status,
                    failure_stage=None,
                    failure_code=None,
                    failure_message=None,
                    updated_at=now,
                )
            )

    def mark_failed(self, task: QueuedTask, failure: IngestFailure) -> str:
        now = datetime.now(UTC)
        task_status = IngestTaskStatus.DEAD.value if self.attempts_exhausted(task) else IngestTaskStatus.FAILED.value
        with self.engine.begin() as connection:
            document_status = DocumentStatus.FAILED.value
            if task.task_type == TaskType.REPROCESS.value and has_searchable_index(connection, task.document_id):
                document_status = DocumentStatus.INDEXED.value
            connection.execute(
                update(ingest_tasks)
                .where(ingest_tasks.c.id == task.id)
                .values(
                    status=task_status,
                    stage=failure.stage,
                    error_code=failure.code,
                    error_message=failure.message,
                    finished_at=now,
                    updated_at=now,
                )
            )
            connection.execute(
                update(documents)
                .where(documents.c.id == task.document_id)
                .values(
                    status=document_status,
                    failure_stage=failure.stage,
                    failure_code=failure.code,
                    failure_message=failure.message,
                    updated_at=now,
                )
            )
        return task_status


def has_searchable_index(connection, document_id: str) -> bool:
    return (
        connection.execute(
            select(chunk_search_index.c.id)
            .where(chunk_search_index.c.document_id == document_id)
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )


def cleanup_deleted_document(connection, document_id: str) -> None:
    chunk_ids = connection.execute(
        select(document_chunks.c.id).where(document_chunks.c.document_id == document_id)
    ).scalars().all()
    connection.execute(
        update(answer_citations)
        .where(answer_citations.c.document_id == document_id)
        .values(document_id=None, chunk_id=None, accessible=False)
    )
    if chunk_ids:
        connection.execute(delete(chunk_search_index).where(chunk_search_index.c.chunk_id.in_(chunk_ids)))
        connection.execute(delete(embedding_references).where(embedding_references.c.chunk_id.in_(chunk_ids)))
        connection.execute(delete(chunk_entity_links).where(chunk_entity_links.c.chunk_id.in_(chunk_ids)))
        connection.execute(
            update(graph_edges)
            .where(graph_edges.c.source_chunk_id.in_(chunk_ids))
            .where(graph_edges.c.is_active.is_(True))
            .values(is_active=False, valid_to=datetime.now(UTC))
        )
    connection.execute(delete(document_entity_extractions).where(document_entity_extractions.c.document_id == document_id))
    connection.execute(delete(document_assets).where(document_assets.c.document_id == document_id))
    connection.execute(delete(document_chunks).where(document_chunks.c.document_id == document_id))


def retry_failed_task(engine: Engine, task_id: str) -> TaskResult:
    with engine.connect() as connection:
        task = connection.execute(
            select(ingest_tasks).where(ingest_tasks.c.id == task_id)
        ).mappings().one()

    if task["status"] != IngestTaskStatus.FAILED.value:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Only failed tasks can be retried",
            status_code=status.HTTP_409_CONFLICT,
        )

    get_document_or_404(engine, task["document_id"])
    new_task_id = str(uuid4())
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            insert(ingest_tasks).values(
                id=new_task_id,
                document_id=task["document_id"],
                task_type=TaskType.REPROCESS.value,
                status=IngestTaskStatus.QUEUED.value,
                attempt_count=0,
                stage="queued",
            )
        )
        connection.execute(
            update(documents)
            .where(documents.c.id == task["document_id"])
            .values(status=DocumentStatus.REPROCESSING.value, updated_at=now)
        )

    return TaskResult(
        document_id=task["document_id"],
        task_id=new_task_id,
        status=IngestTaskStatus.QUEUED.value,
    )
