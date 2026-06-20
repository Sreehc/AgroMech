from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import status
from sqlalchemy import Engine, asc, insert, select, update

from agromech_api.db.enums import DocumentStatus, IngestTaskStatus, TaskType
from agromech_api.db.models import documents, ingest_tasks
from agromech_api.documents import TaskResult, get_document_or_404
from agromech_api.errors import AppError, ErrorCode


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
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

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
            self.mark_failed(task, exc)
            return "failed"
        except Exception as exc:
            self.mark_failed(
                task,
                IngestFailure("unexpected_error", str(exc), stage="unexpected"),
            )
            return "failed"

        self.mark_succeeded(task)
        return "succeeded"

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

    def mark_failed(self, task: QueuedTask, failure: IngestFailure) -> None:
        now = datetime.now(UTC)
        with self.engine.begin() as connection:
            connection.execute(
                update(ingest_tasks)
                .where(ingest_tasks.c.id == task.id)
                .values(
                    status=IngestTaskStatus.FAILED.value,
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
                    status=DocumentStatus.FAILED.value,
                    failure_stage=failure.stage,
                    failure_code=failure.code,
                    failure_message=failure.message,
                    updated_at=now,
                )
            )


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
