from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable

from agromech_api.config import Settings


LOGGER = logging.getLogger("agromech.worker.rabbitmq")


def handle_task_message(channel, method, body: bytes, runner: Callable[[], str]) -> None:
    try:
        json.loads(body.decode("utf-8"))
    except Exception:
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    try:
        runner()
    except Exception:
        LOGGER.exception("RabbitMQ task handling failed before DB state was reliable")
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
        return

    channel.basic_ack(delivery_tag=method.delivery_tag)


def consume(settings: Settings, runner: Callable[[], str], *, stop_when_idle: bool = False) -> None:
    import pika

    parameters = pika.URLParameters(settings.rabbitmq_url)
    while True:
        connection = pika.BlockingConnection(parameters)
        try:
            channel = connection.channel()
            channel.queue_declare(queue=settings.rabbitmq_queue, durable=True)
            channel.basic_qos(prefetch_count=settings.rabbitmq_consume_prefetch)

            def callback(ch, method, properties, body):
                handle_task_message(ch, method, body, runner)
                if stop_when_idle:
                    ch.stop_consuming()

            channel.basic_consume(queue=settings.rabbitmq_queue, on_message_callback=callback)
            channel.start_consuming()
            return
        except KeyboardInterrupt:
            return
        except Exception:
            LOGGER.exception("RabbitMQ consumer connection failed; retrying")
            time.sleep(settings.rabbitmq_reconnect_seconds)
        finally:
            try:
                connection.close()
            except Exception:
                pass
