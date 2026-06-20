from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, select

from agromech_api.auth import create_access_token
from agromech_api.config import Settings
from agromech_api.db.enums import UserRole
from agromech_api.db.models import metadata, retrieval_logs
from agromech_api.hybrid_retrieval import hybrid_retrieve_with_trace
from agromech_api.main import create_app
from test_hybrid_retrieval import create_test_engine, seed_retrieval_corpus


def trace_settings(tmp_path: Path) -> Settings:
    return Settings(
        admin_username="admin",
        admin_password="secret",
        auth_token_secret="test-secret",
        local_file_storage_path=str(tmp_path / "files"),
    )


def trace_client(tmp_path: Path, role: UserRole = UserRole.EVALUATOR) -> tuple[TestClient, object, str]:
    settings = trace_settings(tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    token = create_access_token(username=role.value, role=role, settings=settings)
    return TestClient(create_app(settings=settings, database_engine=engine)), engine, token


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_retrieve_with_trace_logs_query_filters_channels_rerank_and_final_evidence(tmp_path: Path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)

    result = hybrid_retrieve_with_trace(
        engine,
        "M7040 E01 hydraulic repair",
        trace_id="trace-rerank",
        degraded_channels={"graph": "neo4j timeout"},
    )

    assert result["trace_id"] == "trace-rerank"
    assert result["status"] == "ok"
    assert result["candidates"][0]["chunk_id"] == "chunk-m7040"

    with engine.connect() as connection:
        log = connection.execute(select(retrieval_logs)).mappings().one()

    assert log["trace_id"] == "trace-rerank"
    assert log["query"] == "M7040 E01 hydraulic repair"
    assert log["filters"]["model"] == "M7040"
    assert set(log["channels"]["used"]) >= {"keyword", "vector", "structured"}
    assert log["channels"]["degraded"] == [{"channel": "graph", "reason": "neo4j timeout"}]
    assert log["candidates"][0]["chunk_id"]
    assert log["final_evidence"][0]["chunk_id"] == "chunk-m7040"
    assert log["rerank"]["strategy"] == "deterministic_evidence_rerank"
    assert {
        "chunk_id",
        "before_rank",
        "after_rank",
        "before_score",
        "after_score",
        "channels",
    }.issubset(log["rerank"]["items"][0])


def test_trace_api_returns_full_trace_to_evaluator(tmp_path: Path) -> None:
    client, engine, token = trace_client(tmp_path)
    with engine.begin() as connection:
        connection.execute(
            insert(retrieval_logs).values(
                id="log-1",
                trace_id="trace-api",
                query="M7040 E01",
                filters={"model": "M7040"},
                channels={
                    "used": ["keyword", "vector"],
                    "degraded": [{"channel": "rerank", "reason": "service timeout"}],
                },
                candidates=[{"chunk_id": "chunk-a", "content": "full evidence", "score": 4.2}],
                rerank={
                    "strategy": "deterministic_evidence_rerank",
                    "items": [{"chunk_id": "chunk-a", "before_rank": 2, "after_rank": 1}],
                },
                final_evidence=[{"chunk_id": "chunk-a", "content": "full evidence"}],
            )
        )

    response = client.get("/retrieval-traces/trace-api", headers=auth_header(token))

    assert response.status_code == 200
    payload = response.json()
    assert payload["trace_id"] == "trace-api"
    assert payload["filters"] == {"model": "M7040"}
    assert payload["channels"]["degraded"][0]["channel"] == "rerank"
    assert payload["candidates"][0]["content"] == "full evidence"
    assert payload["rerank"]["items"][0]["before_rank"] == 2


def test_trace_api_hides_full_candidates_from_standard_user(tmp_path: Path) -> None:
    client, engine, token = trace_client(tmp_path, role=UserRole.USER)
    with engine.begin() as connection:
        connection.execute(
            insert(retrieval_logs).values(
                id="log-2",
                trace_id="trace-summary",
                query="M7040 E01",
                filters={"model": "M7040"},
                channels={"used": ["keyword"], "degraded": []},
                candidates=[{"chunk_id": "chunk-a", "content": "full evidence", "score": 4.2}],
                rerank={"items": [{"chunk_id": "chunk-a", "before_rank": 1, "after_rank": 1}]},
                final_evidence=[{"chunk_id": "chunk-a", "content": "full evidence"}],
            )
        )

    response = client.get("/retrieval-traces/trace-summary", headers=auth_header(token))

    assert response.status_code == 200
    payload = response.json()
    assert payload["trace_id"] == "trace-summary"
    assert payload["channels"] == {"used": ["keyword"], "degraded": []}
    assert "candidates" not in payload
    assert payload["final_evidence"] == [{"chunk_id": "chunk-a"}]
