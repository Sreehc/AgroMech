from fastapi.testclient import TestClient

from agromech_api.infrastructure import DependencyCheck
from agromech_api.main import create_app


def test_dependency_health_endpoint_returns_dependency_statuses() -> None:
    def fake_checker() -> list[DependencyCheck]:
        return [
            DependencyCheck(name="postgres", status="ok", target="localhost:5432"),
            DependencyCheck(name="milvus", status="ok", target="localhost:19530"),
            DependencyCheck(name="neo4j", status="ok", target="localhost:7687"),
        ]

    client = TestClient(create_app(dependency_checker=fake_checker))

    response = client.get("/health/dependencies")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "dependencies": [
            {"name": "postgres", "status": "ok", "target": "localhost:5432"},
            {"name": "milvus", "status": "ok", "target": "localhost:19530"},
            {"name": "neo4j", "status": "ok", "target": "localhost:7687"},
        ],
    }
