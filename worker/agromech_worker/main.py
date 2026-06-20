from __future__ import annotations

import logging

from sqlalchemy import Engine

from agromech_api.database import get_engine
from agromech_api.ingestion import IngestTaskRunner, QueuedTask


LOGGER = logging.getLogger("agromech.worker")


def health_status() -> dict[str, str]:
    return {"status": "ok", "service": "worker"}


def default_processor(task: QueuedTask) -> None:
    LOGGER.info(
        "Processed ingest task placeholder: task_id=%s document_id=%s task_type=%s",
        task.id,
        task.document_id,
        task.task_type,
    )


def run_once(*, engine: Engine | None = None, processor=default_processor) -> str:
    runner = IngestTaskRunner(engine or get_engine())
    result = runner.run_next(processor)
    LOGGER.info("AgroMech worker run_once result: %s", result)
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    LOGGER.info("AgroMech worker health: %s", health_status())
    try:
        run_once()
    except Exception:
        LOGGER.exception("AgroMech worker run_once failed")


if __name__ == "__main__":
    main()
