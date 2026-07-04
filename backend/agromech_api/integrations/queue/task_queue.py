from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from agromech_api.core.config import Settings


@dataclass(frozen=True)
class TaskMessage:
    task_id: str
    document_id: str
    task_type: str
    attempt: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_json(self) -> str:
        return json.dumps(
            {
                "task_id": self.task_id,
                "document_id": self.document_id,
                "task_type": self.task_type,
                "attempt": self.attempt,
                "created_at": self.created_at,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


class TaskPublisher(Protocol):
    def publish(self, message: TaskMessage) -> None: ...


class NoopTaskPublisher:
    def publish(self, message: TaskMessage) -> None:
        return None


class InMemoryTaskPublisher:
    def __init__(self) -> None:
        self.messages: list[TaskMessage] = []

    def publish(self, message: TaskMessage) -> None:
        self.messages.append(message)


class RabbitMqTaskPublisher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def publish(self, message: TaskMessage) -> None:
        import pika

        parameters = pika.URLParameters(self.settings.rabbitmq_url)
        connection = pika.BlockingConnection(parameters)
        try:
            channel = connection.channel()
            channel.queue_declare(queue=self.settings.rabbitmq_queue, durable=True)
            channel.basic_publish(
                exchange=self.settings.rabbitmq_exchange,
                routing_key=self.settings.rabbitmq_routing_key or self.settings.rabbitmq_queue,
                body=message.to_json().encode("utf-8"),
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=2,
                ),
            )
        finally:
            connection.close()


def build_task_publisher(settings: Settings) -> TaskPublisher:
    if not settings.rabbitmq_publish_enabled:
        return NoopTaskPublisher()
    return RabbitMqTaskPublisher(settings)
