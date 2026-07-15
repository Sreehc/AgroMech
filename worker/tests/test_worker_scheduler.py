from sqlalchemy import create_engine, insert, select

from agromech_api.db.enums import DocumentStatus, IngestTaskStatus, TaskType
from agromech_api.db.models import documents, ingest_tasks, metadata
from agromech_api.core.config import Settings
from agromech_worker.main import preflight_dependencies, run_once


def test_worker_run_once_processes_next_queued_task(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
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
                status=DocumentStatus.QUEUED.value,
                created_by_role="admin",
            )
        )
        connection.execute(
            insert(ingest_tasks).values(
                id="task-1",
                document_id="doc-1",
                task_type=TaskType.INGEST.value,
                status=IngestTaskStatus.QUEUED.value,
                attempt_count=0,
                stage="queued",
            )
        )

    assert run_once(engine=engine, processor=lambda _task: None) == "succeeded"

    with engine.connect() as connection:
        status = connection.execute(select(documents.c.status)).scalar_one()
        task_status = connection.execute(select(ingest_tasks.c.status)).scalar_one()
    assert status == "indexed"
    assert task_status == "succeeded"


def test_worker_preflight_checks_database_then_declares_queue_without_consuming(monkeypatch, tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'preflight.db'}")
    observed: list[Settings] = []
    settings = Settings(_env_file=None, rabbitmq_queue="candidate-queue")
    monkeypatch.setattr(
        "agromech_worker.rabbitmq.preflight_queue",
        lambda active_settings: observed.append(active_settings),
    )

    preflight_dependencies(settings=settings, engine=engine)

    assert observed == [settings]
