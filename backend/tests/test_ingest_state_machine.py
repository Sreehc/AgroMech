import pytest
from sqlalchemy import create_engine, insert, select

from agromech_api.db.enums import DocumentStatus, IngestTaskStatus, TaskType
from agromech_api.db.models import documents, ingest_tasks, metadata
from agromech_api.errors import AppError, ErrorCode
from agromech_api.ingestion import IngestFailure, IngestTaskRunner, retry_failed_task


def create_test_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    return engine


def seed_task(
    engine,
    *,
    document_status=DocumentStatus.QUEUED.value,
    task_status=IngestTaskStatus.QUEUED.value,
    task_type=TaskType.INGEST.value,
):
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id="doc-1",
                title="Manual",
                original_file_name="manual.txt",
                file_hash="hash-doc-1",
                file_size_bytes=10,
                mime_type="text/plain",
                storage_uri="file:///tmp/manual.txt",
                status=document_status,
                created_by_role="admin",
            )
        )
        connection.execute(
            insert(ingest_tasks).values(
                id="task-1",
                document_id="doc-1",
                task_type=task_type,
                status=task_status,
                attempt_count=0,
                stage="queued",
            )
        )


def fetch_state(engine):
    with engine.connect() as connection:
        document = connection.execute(select(documents)).mappings().one()
        task = connection.execute(select(ingest_tasks)).mappings().one()
    return document, task


def test_successful_ingest_moves_queued_task_to_indexed(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_task(engine)
    runner = IngestTaskRunner(engine)

    result = runner.run_next(lambda _task: None)

    assert result == "succeeded"
    document, task = fetch_state(engine)
    assert document["status"] == "indexed"
    assert task["status"] == "succeeded"
    assert task["attempt_count"] == 1
    assert task["stage"] == "indexed"
    assert task["error_code"] is None
    assert task["error_message"] is None
    assert task["started_at"] is not None
    assert task["finished_at"] is not None


def test_failed_ingest_records_error_and_marks_document_failed(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_task(engine)
    runner = IngestTaskRunner(engine)

    result = runner.run_next(
        lambda _task: (_ for _ in ()).throw(
            IngestFailure("parse_failed", "Parser could not read file", stage="parse")
        )
    )

    assert result == "failed"
    document, task = fetch_state(engine)
    assert document["status"] == "failed"
    assert document["failure_stage"] == "parse"
    assert document["failure_code"] == "parse_failed"
    assert document["failure_message"] == "Parser could not read file"
    assert task["status"] == "failed"
    assert task["attempt_count"] == 1
    assert task["stage"] == "parse"
    assert task["error_code"] == "parse_failed"
    assert task["error_message"] == "Parser could not read file"
    assert task["finished_at"] is not None


def test_retry_failed_task_creates_new_queued_task(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_task(engine, document_status=DocumentStatus.FAILED.value, task_status=IngestTaskStatus.FAILED.value)

    result = retry_failed_task(engine, "task-1")

    assert result.status == "queued"
    assert result.task_id != "task-1"
    with engine.connect() as connection:
        tasks = connection.execute(select(ingest_tasks).order_by(ingest_tasks.c.created_at)).mappings().all()
        document_status = connection.execute(select(documents.c.status)).scalar_one()
    assert len(tasks) == 2
    assert tasks[-1]["status"] == "queued"
    assert tasks[-1]["task_type"] == "reprocess"
    assert document_status == "reprocessing"


def test_retry_rejects_non_failed_task(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_task(engine, task_status=IngestTaskStatus.SUCCEEDED.value)

    with pytest.raises(AppError) as exc:
        retry_failed_task(engine, "task-1")

    assert exc.value.code == ErrorCode.VALIDATION_ERROR
    assert exc.value.status_code == 409
    assert exc.value.message == "Only failed tasks can be retried"


def test_delete_task_moves_document_to_deleted(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_task(
        engine,
        document_status=DocumentStatus.DELETING.value,
        task_type=TaskType.DELETE.value,
    )
    runner = IngestTaskRunner(engine)

    result = runner.run_next(lambda _task: None)

    assert result == "succeeded"
    document, task = fetch_state(engine)
    assert document["status"] == "deleted"
    assert task["status"] == "succeeded"
    assert task["stage"] == "deleted"


def test_runner_returns_idle_when_no_queued_task(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    runner = IngestTaskRunner(engine)

    assert runner.run_next(lambda _task: None) == "idle"
