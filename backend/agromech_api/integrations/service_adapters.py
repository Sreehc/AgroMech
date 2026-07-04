from __future__ import annotations

from dataclasses import dataclass

from agromech_api.core.config import Settings


@dataclass(frozen=True)
class ServiceTimeouts:
    llm_seconds: float
    retrieval_seconds: float
    connection_seconds: float

    @classmethod
    def from_settings(cls, settings: Settings) -> "ServiceTimeouts":
        return cls(
            llm_seconds=settings.llm_request_timeout_seconds,
            retrieval_seconds=settings.retrieval_timeout_seconds,
            connection_seconds=settings.dependency_connect_timeout_seconds,
        )
