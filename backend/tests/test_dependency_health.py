from fastapi.testclient import TestClient

from agromech_api.core.infrastructure import DependencyCheck
from agromech_api.main import create_app


def test_dependency_health_endpoint_returns_dependency_statuses() -> None:
    def fake_checker() -> list[DependencyCheck]:
        return [
            DependencyCheck(name="postgres", status="ok", target="localhost:5432"),
            DependencyCheck(name="neo4j", status="ok", target="localhost:7687"),
            DependencyCheck(name="file_storage", status="ok", target="local:./.agromech-data/storage/files"),
            DependencyCheck(name="pgvector", status="ok", target="postgresql://localhost/agromech"),
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
            {"name": "pgvector", "status": "ok", "target": "postgresql://localhost/agromech"},
            {"name": "bailian", "status": "ok", "target": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
        ],
    }


def test_dependency_health_endpoint_returns_degraded_when_any_dependency_is_unavailable() -> None:
    def fake_checker() -> list[DependencyCheck]:
        return [
            DependencyCheck(name="postgres", status="ok", target="localhost:5432"),
            DependencyCheck(name="pgvector", status="unavailable", target="postgresql://localhost/agromech", error="extension missing"),
        ]

    client = TestClient(create_app(dependency_checker=fake_checker))

    response = client.get("/health/dependencies")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["dependencies"][1]["error"] == "extension missing"
