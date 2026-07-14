from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from agromech_api.core.config import Settings
from agromech_api.core.infrastructure import DependencyCheck
from agromech_api.main import create_app


def test_dependency_health_endpoint_returns_dependency_statuses() -> None:
    def fake_checker() -> list[DependencyCheck]:
        return [
            DependencyCheck(name="postgres", status="ok", target="localhost:5432"),
            DependencyCheck(name="neo4j", status="ok", target="localhost:7687"),
            DependencyCheck(name="file_storage", status="ok", target="local:./.agromech-data/storage/files"),
            DependencyCheck(name="pgvector", status="ok", target="postgres:extension/vector"),
            DependencyCheck(name="bailian", status="ok", target="https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ]

    client = TestClient(create_app(dependency_checker=fake_checker))

    response = client.get("/health/dependencies")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "dependencies": [
            {"name": "postgres", "status": "ok", "target": "localhost:5432"},
            {"name": "neo4j", "status": "ok", "target": "localhost:7687"},
            {"name": "file_storage", "status": "ok", "target": "local:./.agromech-data/storage/files"},
            {"name": "pgvector", "status": "ok", "target": "postgres:extension/vector"},
            {"name": "bailian", "status": "ok", "target": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
        ],
    }


def test_dependency_health_endpoint_returns_degraded_when_any_dependency_is_unavailable() -> None:
    def fake_checker() -> list[DependencyCheck]:
        return [
            DependencyCheck(name="postgres", status="ok", target="localhost:5432"),
            DependencyCheck(name="pgvector", status="unavailable", target="postgres:extension/vector", error="extension missing"),
        ]

    client = TestClient(create_app(dependency_checker=fake_checker))

    response = client.get("/health/dependencies")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["dependencies"][1]["error"] == "extension missing"


def test_dependency_health_endpoint_treats_not_applicable_as_healthy() -> None:
    client = TestClient(
        create_app(
            dependency_checker=lambda: [
                DependencyCheck("postgres", "ok", "localhost:5432"),
                DependencyCheck(
                    "bailian",
                    "not_applicable",
                    "unconfigured",
                ),
            ]
        )
    )

    response = client.get("/health/dependencies")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "dependencies": [
            {"name": "postgres", "status": "ok", "target": "localhost:5432"},
            {
                "name": "bailian",
                "status": "not_applicable",
                "target": "unconfigured",
            },
        ],
    }


def test_dependency_health_endpoint_uses_app_database_engine(monkeypatch) -> None:
    captured = {}
    settings = Settings(_env_file=None)
    database_engine = object()

    def fake_check_infrastructure(active_settings, *, engine=None):
        captured["settings"] = active_settings
        captured["engine"] = engine
        return [DependencyCheck(name="pgvector", status="ok", target="postgres:extension/vector")]

    monkeypatch.setattr("agromech_api.main.check_infrastructure", fake_check_infrastructure)
    client = TestClient(create_app(settings=settings, database_engine=database_engine))

    response = client.get("/health/dependencies")

    assert response.status_code == 200
    assert captured == {"settings": settings, "engine": database_engine}


def test_readiness_returns_503_when_required_search_dependency_is_missing() -> None:
    client = TestClient(
        create_app(
            dependency_checker=lambda: [
                DependencyCheck("postgres", "ok", "localhost:5432"),
                DependencyCheck("pgvector", "ok", "postgres:extension/vector"),
                DependencyCheck(
                    "pg_search",
                    "unavailable",
                    "postgres:extension/pg_search",
                    "missing",
                ),
            ]
        )
    )

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"


def test_readiness_treats_not_applicable_dependencies_as_healthy() -> None:
    client = TestClient(
        create_app(
            dependency_checker=lambda: [
                DependencyCheck("postgres", "ok", "localhost:5432"),
                DependencyCheck(
                    "pg_search",
                    "not_applicable",
                    "postgres:extension/pg_search",
                ),
                DependencyCheck("bailian", "not_applicable", "unconfigured"),
            ]
        )
    )

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_sqlite_local_readiness_does_not_require_postgres_tcp(
    tmp_path,
    monkeypatch,
) -> None:
    tcp_attempts = []

    def refuse_tcp_connection(address, *_args, **_kwargs):
        tcp_attempts.append(address)
        raise ConnectionRefusedError("no PostgreSQL TCP service")

    monkeypatch.setattr(
        "agromech_api.core.infrastructure.socket.create_connection",
        refuse_tcp_connection,
    )
    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        postgres_host="127.0.0.1",
        postgres_port=9,
        file_storage_backend="local",
        local_file_storage_path=str(tmp_path / "files"),
        graph_backend="local",
        model_provider="local",
        embedding_provider="local",
        visual_embedding_provider="local",
    )
    engine = create_engine(settings.database_url)

    try:
        client = TestClient(create_app(settings=settings, database_engine=engine))
        response = client.get("/health/ready")
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    dependencies = {check["name"]: check for check in response.json()["dependencies"]}
    assert dependencies["postgres"] == {
        "name": "postgres",
        "status": "not_applicable",
        "target": "sqlite:database",
    }
    assert tcp_attempts == []
