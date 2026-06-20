from __future__ import annotations

import logging


LOGGER = logging.getLogger("agromech.worker")


def health_status() -> dict[str, str]:
    return {"status": "ok", "service": "worker"}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    LOGGER.info("AgroMech worker health: %s", health_status())


if __name__ == "__main__":
    main()

