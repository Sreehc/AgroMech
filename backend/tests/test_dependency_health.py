from fastapi.testclient import TestClient

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
