from __future__ import annotations

import json

from agromech_api.config import Settings
from agromech_api.task_queue import InMemoryTaskPublisher, TaskMessage, build_task_publisher


def test_in_memory_task_publisher_records_serializable_message() -> None:
    publisher = InMemoryTaskPublisher()
    message = TaskMessage(task_id="task-1", document_id="doc-1", task_type="ingest", attempt=0)

    publisher.publish(message)

    assert publisher.messages == [message]
    payload = json.loads(message.to_json())
    assert payload["task_id"] == "task-1"
    assert payload["document_id"] == "doc-1"
    assert payload["task_type"] == "ingest"
    assert payload["attempt"] == 0
    assert "created_at" in payload


def test_build_task_publisher_returns_noop_when_disabled() -> None:
    settings = Settings(
        _env_file=None,
        file_storage_backend="local",
        vector_backend="local",
        graph_backend="local",
        model_provider="local",
        embedding_provider="local",
        rabbitmq_publish_enabled=False,
    )

    publisher = build_task_publisher(settings)

    publisher.publish(TaskMessage(task_id="task-1", document_id="doc-1", task_type="ingest"))
