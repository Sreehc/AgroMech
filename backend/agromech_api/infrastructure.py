from __future__ import annotations

import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from agromech_api.config import Settings


@dataclass(frozen=True)
class DependencyTarget:
    name: str
    host: str
    port: int

    @property
    def label(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass(frozen=True)
class DependencyCheck:
    name: str
    status: str
    target: str
    error: str | None = None

    def to_dict(self) -> dict[str, str]:
        payload = {
            "name": self.name,
            "status": self.status,
            "target": self.target,
        }
        if self.error:
            payload["error"] = self.error
        return payload


def dependency_targets(settings: Settings) -> dict[str, DependencyTarget]:
    database_url = urlsplit(settings.database_url)
    neo4j_url = urlsplit(settings.neo4j_uri)

    postgres_host = database_url.hostname or settings.postgres_host
    postgres_port = database_url.port or settings.postgres_port

    if not postgres_host:
        raise ValueError("DATABASE_URL must include a host")
    if not neo4j_url.hostname:
        raise ValueError("NEO4J_URI must include a host")

    return {
        "postgres": DependencyTarget("postgres", postgres_host, postgres_port),
        "milvus": DependencyTarget("milvus", settings.milvus_host, settings.milvus_port),
        "neo4j": DependencyTarget("neo4j", neo4j_url.hostname, neo4j_url.port or 7687),
    }


def check_tcp_dependency(
    target: DependencyTarget,
    timeout_seconds: float,
) -> DependencyCheck:
    try:
        with socket.create_connection((target.host, target.port), timeout=timeout_seconds):
            return DependencyCheck(target.name, "ok", target.label)
    except OSError as exc:
        return DependencyCheck(target.name, "unavailable", target.label, str(exc))


def check_infrastructure(settings: Settings) -> list[DependencyCheck]:
    return [
        check_tcp_dependency(target, settings.dependency_connect_timeout_seconds)
        for target in dependency_targets(settings).values()
    ]
