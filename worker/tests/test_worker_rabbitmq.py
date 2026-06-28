from __future__ import annotations

from agromech_worker.rabbitmq import handle_task_message


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
