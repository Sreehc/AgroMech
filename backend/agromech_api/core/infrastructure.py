from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import text

from agromech_api.core.config import Settings
from agromech_api.core.database import get_engine


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

    postgres_host = database_url.hostname or settings.postgres_host
    postgres_port = database_url.port or settings.postgres_port

    if not postgres_host:
        raise ValueError("DATABASE_URL must include a host")

    targets = {
        "postgres": DependencyTarget("postgres", postgres_host, postgres_port),
    }
    if settings.graph_backend == "neo4j":
        neo4j_url = urlsplit(settings.neo4j_uri)
        if not neo4j_url.hostname:
            raise ValueError("NEO4J_URI must include a host")
        targets["neo4j"] = DependencyTarget("neo4j", neo4j_url.hostname, neo4j_url.port or 7687)
    return targets


def check_tcp_dependency(
    target: DependencyTarget,
    timeout_seconds: float,
) -> DependencyCheck:
    try:
        with socket.create_connection((target.host, target.port), timeout=timeout_seconds):
            return DependencyCheck(target.name, "ok", target.label)
    except OSError as exc:
        return DependencyCheck(target.name, "unavailable", target.label, str(exc))


def check_file_storage(settings: Settings) -> DependencyCheck:
    """Check the configured file storage backend without leaking credentials."""
    if settings.file_storage_backend == "oss":
        return check_oss_storage(settings)
    return check_local_storage(settings)


def check_local_storage(settings: Settings) -> DependencyCheck:
    target = settings.local_file_storage_path
    try:
        root = Path(settings.local_file_storage_path)
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".health-probe"
        probe.write_bytes(b"ok")
        probe.unlink()
        return DependencyCheck("file_storage", "ok", f"local:{target}")
    except OSError as exc:
        return DependencyCheck("file_storage", "unavailable", f"local:{target}", str(exc))


def check_oss_storage(settings: Settings) -> DependencyCheck:
    target = f"oss:{settings.oss_bucket}@{settings.oss_region}"
    try:
        import oss2
    except ImportError as exc:
        return DependencyCheck("file_storage", "unavailable", target, f"oss2 not installed: {exc}")
    try:
        auth = oss2.Auth(settings.oss_access_key_id, settings.oss_access_key_secret)
        bucket = oss2.Bucket(
            auth,
            settings.oss_endpoint,
            settings.oss_bucket,
            connect_timeout=settings.dependency_connect_timeout_seconds,
        )
        # get_bucket_info validates credentials, endpoint and bucket existence
        # in one cheap private call without listing or writing objects.
        bucket.get_bucket_info()
        return DependencyCheck("file_storage", "ok", target)
    except Exception as exc:  # noqa: BLE001 - surface any OSS error as unavailable, sanitized
        return DependencyCheck("file_storage", "unavailable", target, sanitize_oss_error(exc))


def sanitize_oss_error(exc: Exception) -> str:
    """Return a short, credential-free description of an OSS failure."""
    name = type(exc).__name__
    status_code = getattr(exc, "status", None)
    code = getattr(exc, "code", None)
    parts = [name]
    if status_code:
        parts.append(f"status={status_code}")
    if code:
        parts.append(f"code={code}")
    return " ".join(parts)


def check_pgvector_extension(engine=None) -> DependencyCheck:
    active_engine = engine or get_engine()
    target = str(active_engine.url)
    try:
        with active_engine.connect() as connection:
            extension = connection.execute(
                text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
            ).scalar_one_or_none()
        if extension == "vector":
            return DependencyCheck("pgvector", "ok", target)
        return DependencyCheck("pgvector", "unavailable", target, "pgvector extension is not installed")
    except Exception as exc:  # noqa: BLE001 - health checks report dependency status instead of raising
        return DependencyCheck("pgvector", "unavailable", target, str(exc))


def check_bailian_config(settings: Settings) -> DependencyCheck:
    target = settings.bailian_base_url or "unconfigured"
    bailian_enabled = "bailian" in {settings.model_provider, settings.embedding_provider}
    if not bailian_enabled:
        return DependencyCheck("bailian", "unavailable", target, "bailian configuration missing")
    if not settings.bailian_api_key or not settings.bailian_base_url:
        return DependencyCheck("bailian", "unavailable", target, "bailian configuration missing")
    return DependencyCheck("bailian", "ok", settings.bailian_base_url)


def check_infrastructure(settings: Settings) -> list[DependencyCheck]:
    checks = [
        check_tcp_dependency(target, settings.dependency_connect_timeout_seconds)
        for target in dependency_targets(settings).values()
    ]
    checks.append(check_file_storage(settings))
    checks.append(check_pgvector_extension())
    checks.append(check_bailian_config(settings))
    return checks
