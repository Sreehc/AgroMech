from __future__ import annotations

import sys
from types import SimpleNamespace

from agromech_api.core.config import Settings
from agromech_worker.rabbitmq import handle_task_message, preflight_queue


class AckRecorder:
    def __init__(self) -> None:
        self.acked: list[int] = []
        self.nacked: list[tuple[int, bool]] = []

    def basic_ack(self, delivery_tag: int) -> None:
        self.acked.append(delivery_tag)

    def basic_nack(self, delivery_tag: int, requeue: bool) -> None:
        self.nacked.append((delivery_tag, requeue))


class Method:
    delivery_tag = 7


def test_handle_task_message_acks_successful_runner_result() -> None:
    channel = AckRecorder()
    calls: list[str] = []

    handle_task_message(channel, Method(), b'{"task_id":"task-1"}', lambda: calls.append("run") or "succeeded")

    assert calls == ["run"]
    assert channel.acked == [7]
    assert channel.nacked == []


def test_handle_task_message_acks_idle_as_duplicate_or_stale() -> None:
    channel = AckRecorder()

    handle_task_message(channel, Method(), b'{"task_id":"task-1"}', lambda: "idle")

    assert channel.acked == [7]
    assert channel.nacked == []


def test_handle_task_message_rejects_malformed_json_without_requeue() -> None:
    channel = AckRecorder()

    handle_task_message(channel, Method(), b"not-json", lambda: "succeeded")

    assert channel.acked == []
    assert channel.nacked == [(7, False)]


def test_preflight_queue_declares_queue_without_registering_a_consumer(monkeypatch) -> None:
    events: list[tuple[str, object]] = []

    class Channel:
        def queue_declare(self, *, queue: str, durable: bool) -> None:
            events.append(("queue_declare", (queue, durable)))

        def basic_consume(self, **_kwargs) -> None:
            raise AssertionError("preflight must not register a RabbitMQ consumer")

    class Connection:
        def channel(self) -> Channel:
            events.append(("channel", None))
            return Channel()

        def close(self) -> None:
            events.append(("close", None))

    pika = SimpleNamespace(
        URLParameters=lambda url: events.append(("url", url)) or url,
        BlockingConnection=lambda parameters: events.append(("connect", parameters)) or Connection(),
    )
    monkeypatch.setitem(sys.modules, "pika", pika)
    settings = Settings(_env_file=None, rabbitmq_url="amqp://queue", rabbitmq_queue="agromech.ingest")

    preflight_queue(settings)

    assert events == [
        ("url", "amqp://queue"),
        ("connect", "amqp://queue"),
        ("channel", None),
        ("queue_declare", ("agromech.ingest", True)),
        ("close", None),
    ]
