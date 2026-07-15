import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, select

from auth_helpers import auth_token_for_user
from agromech_api.core.config import Settings
from agromech_api.db.enums import UserRole
from agromech_api.db.models import metadata, retrieval_logs
from agromech_api.rag.retrieval.hybrid import hybrid_retrieve_with_trace
from agromech_api.main import create_app
from test_hybrid_retrieval import create_test_engine, seed_retrieval_corpus


def trace_settings(tmp_path: Path) -> Settings:
    return Settings(
        auth_token_secret="test-secret",
        local_file_storage_path=str(tmp_path / "files"),
        graph_backend="local",
        model_provider="local",
        embedding_provider="local",
        embedding_dimension=256,
    )


def trace_client(tmp_path: Path, role: UserRole = UserRole.EVALUATOR) -> tuple[TestClient, object, str]:
    settings = trace_settings(tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    token = auth_token_for_user(engine, settings, username=role.value, role=role)
    return TestClient(create_app(settings=settings, database_engine=engine)), engine, token


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_retrieve_trace_records_rewrite_and_rrf_fusion(tmp_path: Path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)

    result = hybrid_retrieve_with_trace(
        engine,
        "M7040 E01 hydraulic pump",
        trace_id="trace-fusion",
        query_rewrite={
            "original_query": "M7040 的 E01 怎么修？",
            "query": "M7040 E01 hydraulic pump",
            "provider": "bailian",
            "model": "qwen3.6-flash",
            "fallback": False,
            "reason": "model_rewrite",
        },
    )

    assert result["trace_id"] == "trace-fusion"
    assert result["status"] == "ok"
    assert result["candidates"][0]["chunk_id"] == "chunk-m7040"

    with engine.connect() as connection:
        log = connection.execute(select(retrieval_logs)).mappings().one()

    assert log["trace_id"] == "trace-fusion"
    assert log["query_rewrite"]["final"]["provider"] == "bailian"
    assert log["query_rewrite"]["final"]["original_query"] == "M7040 的 E01 怎么修？"
    assert log["query_rewrite"]["final"]["query"] == "M7040 E01 hydraulic pump"
    assert len(log["query_rewrite"]["attempts"]) == 1
    assert log["fusion"]["final"]["rrf_k"] == 60
    assert log["fusion"]["retrieval_duration_ms"] >= 0
    assert set(log["channels"]["used"]).issubset({"dense", "bm25"})
    assert "channel_ranks" in log["fusion"]["final"]["items"][0]
    assert log["model_config"]["bm25_backend"] == "pg_search"
    assert log["model_config"]["query_rewrite_model"] == "qwen3.6-flash"


class TraceFailingRerankProvider:
    def rerank(self, _query: str, _documents: list[str]) -> list[float]:
        raise RuntimeError("rerank timeout")


def test_retrieve_with_trace_records_deterministic_fallback_details(tmp_path: Path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)

    result = hybrid_retrieve_with_trace(
        engine,
        "M7040 E01 hydraulic repair",
        trace_id="trace-rerank-fallback",
        rerank_provider=TraceFailingRerankProvider(),
        rerank_top_k=5,
    )

    assert result["trace_id"] == "trace-rerank-fallback"
    with engine.connect() as connection:
        log = connection.execute(
            select(retrieval_logs).where(retrieval_logs.c.trace_id == "trace-rerank-fallback")
        ).mappings().one()

    assert {"channel": "rerank", "reason": "rerank_degraded"} in log["channels"]["degraded"]
    assert log["rerank"]["strategy"] == "deterministic_evidence_rerank"
    assert log["rerank"]["fallback"] is True
    assert "model_match" in log["rerank"]["items"][0]["factors"]
    assert "text_relevance" in log["rerank"]["items"][0]["factors"]


def test_text_qa_trace_records_original_question_filters_and_model_config(tmp_path: Path) -> None:
    settings = trace_settings(tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    seed_retrieval_corpus(engine)
    token = auth_token_for_user(engine, settings, username=UserRole.USER.value, role=UserRole.USER)
    client = TestClient(create_app(settings=settings, database_engine=engine))

    response = client.post(
        "/qa/text",
        headers={"Authorization": f"Bearer {token}", "X-Trace-Id": "trace-text-contract"},
        json={
            "question": "E01 液压告警怎么排查？",
            "filters": {
                "brand": "Kubota",
                "model": "M7040",
                "document_type": "manual",
                "language": "zh-CN",
            },
        },
    )

    assert response.status_code == 200
    with engine.connect() as connection:
        log = connection.execute(
            select(retrieval_logs).where(retrieval_logs.c.trace_id == "trace-text-contract")
        ).mappings().one()

    assert log["query"] == "E01 液压告警怎么排查？"
    assert log["filters"]["brand"] == "Kubota"
    assert log["filters"]["model"] == "M7040"
    assert log["filters"]["document_type"] == "manual"
    assert log["filters"]["language"] == "zh-CN"
    assert log["model_config"]["embedding_provider"] == "local"
    assert log["model_config"]["model_provider"] == "local"
    assert log["model_config"]["embedding_version"] == settings.embedding_version
    assert log["model_config"]["rerank_top_k"] == settings.rerank_top_k
    assert log["model_config"]["final_evidence_limit"] == settings.final_evidence_limit


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
                model_config={"embedding_version": "emb-v1", "rerank_model": "qwen3-rerank"},
                query_rewrite={
                    "attempts": [{"query": "M7040 E01 hydraulic pump", "provider": "bailian", "fallback": False}],
                    "final": {"query": "M7040 E01 hydraulic pump", "provider": "bailian", "fallback": False},
                },
                fusion={
                    "attempts": [{"rrf_k": 60, "channel_counts": {"dense": 50, "bm25": 50}, "items": [{"chunk_id": "chunk-a"}]}],
                    "final": {"rrf_k": 60, "channel_counts": {"dense": 50, "bm25": 50}, "items": [{"chunk_id": "chunk-a"}]},
                    "retrieval_duration_ms": 12.0,
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
    assert payload["model_config"] == {"embedding_version": "emb-v1", "rerank_model": "qwen3-rerank"}
    assert payload["candidates"][0]["content"] == "full evidence"
    assert payload["rerank"]["items"][0]["before_rank"] == 2
    assert payload["query_rewrite"]["final"]["query"] == "M7040 E01 hydraulic pump"
    assert payload["fusion"]["final"]["items"] == [{"chunk_id": "chunk-a"}]


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
                model_config={"embedding_version": "emb-v1"},
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
    assert payload["model_config"] == {"embedding_version": "emb-v1"}
    assert "candidates" not in payload
    assert payload["final_evidence"] == [{"chunk_id": "chunk-a"}]


def test_trace_api_returns_summary_to_maintainer_without_debug_details(tmp_path: Path) -> None:
    client, engine, token = trace_client(tmp_path, role=UserRole.MAINTAINER)
    with engine.begin() as connection:
        connection.execute(
            insert(retrieval_logs).values(
                id="log-3",
                trace_id="trace-maintainer",
                query="M7040 E01",
                filters={"model": "M7040"},
                channels={"used": ["keyword"], "degraded": []},
                model_config={"embedding_version": "emb-v1"},
                query_rewrite={
                    "attempts": [{"query": "secret internal rewrite", "provider": "bailian", "fallback": False}],
                    "final": {"query": "secret internal rewrite", "provider": "bailian", "fallback": False},
                },
                fusion={
                    "attempts": [{"rrf_k": 60, "channel_counts": {"dense": 50, "bm25": 50}, "items": [{"chunk_id": "chunk-a"}]}],
                    "final": {"rrf_k": 60, "channel_counts": {"dense": 50, "bm25": 50}, "items": [{"chunk_id": "chunk-a"}]},
                    "retrieval_duration_ms": 12.0,
                },
                candidates=[{"chunk_id": "chunk-a", "content": "full evidence", "score": 4.2}],
                rerank={"items": [{"chunk_id": "chunk-a", "before_rank": 1, "after_rank": 1}]},
                final_evidence=[{"chunk_id": "chunk-a", "content": "full evidence"}],
            )
        )

    response = client.get("/retrieval-traces/trace-maintainer", headers=auth_header(token))

    assert response.status_code == 200
    payload = response.json()
    assert payload["trace_id"] == "trace-maintainer"
    assert payload["model_config"] == {"embedding_version": "emb-v1"}
    assert "candidates" not in payload
    assert "rerank" not in payload
    assert payload["final_evidence"] == [{"chunk_id": "chunk-a"}]
    assert payload["query_rewrite"] == {"provider": "bailian", "fallback": False}
    assert payload["fusion"] == {"rrf_k": 60, "channel_counts": {"dense": 50, "bm25": 50}}
    assert "items" not in payload["fusion"]


def test_standard_user_trace_hides_rewritten_query_and_fused_items(tmp_path: Path) -> None:
    client, engine, token = trace_client(tmp_path, role=UserRole.USER)
    with engine.begin() as connection:
        connection.execute(
            insert(retrieval_logs).values(
                id="log-rrf",
                trace_id="trace-rrf-summary",
                query="M7040 E01",
                filters={"model": "M7040"},
                channels={"used": ["dense", "bm25"], "degraded": []},
                model_config={},
                query_rewrite={
                    "attempts": [{"query": "secret internal rewrite", "provider": "bailian", "fallback": False}],
                    "final": {"query": "secret internal rewrite", "provider": "bailian", "fallback": False},
                },
                fusion={
                    "attempts": [{"rrf_k": 60, "channel_counts": {"dense": 50, "bm25": 50}, "items": [{"chunk_id": "chunk-a"}]}],
                    "final": {"rrf_k": 60, "channel_counts": {"dense": 50, "bm25": 50}, "items": [{"chunk_id": "chunk-a"}]},
                    "retrieval_duration_ms": 12.0,
                },
                candidates=[],
                rerank={},
                final_evidence=[],
            )
        )

    payload = client.get("/retrieval-traces/trace-rrf-summary", headers=auth_header(token)).json()

    assert payload["query_rewrite"] == {"provider": "bailian", "fallback": False}
    assert payload["fusion"] == {"rrf_k": 60, "channel_counts": {"dense": 50, "bm25": 50}}
    assert "items" not in payload["fusion"]


def test_trace_api_redacts_sensitive_details_from_full_trace_roles(tmp_path: Path) -> None:
    client, engine, token = trace_client(tmp_path, role=UserRole.ADMIN)
    with engine.begin() as connection:
        connection.execute(
            insert(retrieval_logs).values(
                id="log-4",
                trace_id="trace-sensitive",
                query="M7040 E01",
                filters={
                    "model": "M7040",
                    "api_key": "sk-test-secret",
                    "internal_path": "/Users/agromech/.env",
                },
                channels={
                    "used": ["keyword"],
                    "degraded": [{"channel": "rerank", "reason": "Traceback at /var/log/agromech/rerank.log"}],
                },
                model_config={
                    "embedding_version": "emb-v1",
                    "api_key": "sk-live-secret",
                    "prompt_path": "/srv/app/prompts/answer.txt",
                },
                candidates=[
                    {
                        "chunk_id": "chunk-a",
                        "content": "full evidence",
                        "access_token": "token-value",
                        "metadata": {"source_path": "/srv/agromech/private/manual.pdf"},
                    }
                ],
                rerank={
                    "items": [{"chunk_id": "chunk-a", "before_rank": 1, "after_rank": 1}],
                    "stack_trace": "Traceback (most recent call last):\n  File \"/srv/app/rerank.py\", line 1",
                },
                final_evidence=[
                    {"chunk_id": "chunk-a", "content": "full evidence", "password": "plain-secret"}
                ],
            )
        )

    response = client.get("/retrieval-traces/trace-sensitive", headers=auth_header(token))

    assert response.status_code == 200
    payload = response.json()
    assert payload["filters"]["model"] == "M7040"
    assert payload["model_config"]["embedding_version"] == "emb-v1"
    assert payload["candidates"][0]["content"] == "full evidence"
    serialized_payload = json.dumps(payload, ensure_ascii=False)
    assert "sk-test-secret" not in serialized_payload
    assert "sk-live-secret" not in serialized_payload
    assert "token-value" not in serialized_payload
    assert "plain-secret" not in serialized_payload
    assert "/Users/agromech" not in serialized_payload
    assert "/srv/agromech" not in serialized_payload
    assert "/srv/app/prompts" not in serialized_payload
    assert "Traceback" not in serialized_payload
    assert payload["filters"]["api_key"] == "[redacted]"
    assert payload["model_config"]["api_key"] == "[redacted]"
    assert payload["candidates"][0]["access_token"] == "[redacted]"
    assert payload["final_evidence"][0]["password"] == "[redacted]"
