import argparse
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, insert, select

from agromech_api.core.config import Settings
from agromech_api.db.enums import ChunkType
from agromech_api.db.models import document_chunks, evaluation_questions, evaluation_runs, metadata, retrieval_logs
from agromech_api.evaluation.runner import (
    EvaluationQuestion,
    import_evaluation_questions,
    load_evaluation_questions,
    model_config_from_settings,
    ndcg_at_k,
    recall_at_k,
    retrieve_evaluation_candidates,
    run_evaluation_dataset,
    run_evaluation,
)
from agromech_api.rag.retrieval.filters import build_retrieval_filters
from agromech_api.rag.retrieval.fusion import RankedHit
from agromech_api.rag.retrieval.hybrid import hybrid_retrieve_with_trace
from test_hybrid_retrieval import seed_retrieval_corpus


def evaluate_retrieval_script():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "evaluate-retrieval.py"
    spec = importlib.util.spec_from_file_location("evaluate_retrieval_script", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_test_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    return engine


def evaluation_settings(tmp_path) -> Settings:
    return Settings(
        auth_token_secret="test-secret",
        local_file_storage_path=str(tmp_path / "files"),
        graph_backend="local",
        model_provider="local",
        embedding_provider="local",
        embedding_dimension=256,
        rerank_enabled=False,
    )


def test_recall_at_k_uses_expected_chunk_or_document_ids() -> None:
    retrieved = [
        {"chunk_id": "chunk-x", "document_id": "doc-x"},
        {"chunk_id": "chunk-a", "document_id": "doc-a"},
    ]
    expected = [{"chunk_id": "chunk-a", "document_id": "doc-a"}, {"document_id": "doc-b"}]

    assert recall_at_k(retrieved, expected, k=20) == 0.5


def test_ndcg_at_k_rewards_earlier_relevant_evidence() -> None:
    expected = [{"document_id": "doc-a"}, {"document_id": "doc-b"}]
    early = [{"document_id": "doc-a"}, {"document_id": "doc-x"}, {"document_id": "doc-b"}]
    late = [{"document_id": "doc-x"}, {"document_id": "doc-a"}, {"document_id": "doc-b"}]

    assert ndcg_at_k(early, expected, k=10) > ndcg_at_k(late, expected, k=10)


def test_ndcg_at_k_does_not_count_the_same_expected_document_twice() -> None:
    expected = [{"document_id": "doc-a"}]
    retrieved = [
        {"chunk_id": "a-1", "document_id": "doc-a"},
        {"chunk_id": "a-2", "document_id": "doc-a"},
    ]

    assert ndcg_at_k(retrieved, expected, k=10) == 1.0


def test_evaluation_reads_rank_six_when_normal_fusion_limit_is_five(tmp_path) -> None:
    class TopTwentyBm25Retriever:
        def search(self, _engine, _query, *, filters, limit):
            _ = filters
            return [
                RankedHit(
                    chunk_id=f"chunk-evaluation-{rank:02}",
                    rank=rank,
                    score=float(21 - rank),
                )
                for rank in range(1, min(limit, 20) + 1)
            ]

    class FailingEmbeddingProvider:
        def embed(self, _query):
            raise RuntimeError("dense unavailable")

    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(document_chunks),
            [
                {
                    "id": f"chunk-evaluation-{rank:02}",
                    "document_id": "doc-m7040",
                    "chunk_type": ChunkType.TEXT.value,
                    "content": f"evaluation ranking evidence {rank}",
                    "source_locator": {"type": "text", "line_start": rank, "line_end": rank},
                }
                for rank in range(1, 21)
            ],
        )
    settings = Settings(
        auth_token_secret="test-secret",
        local_file_storage_path=str(tmp_path / "files"),
        graph_backend="local",
        model_provider="local",
        embedding_provider="local",
        embedding_dimension=256,
        rerank_enabled=False,
        bm25_top_k=20,
        dense_top_k=20,
        fusion_top_k=5,
        rerank_top_k=5,
        final_evidence_limit=5,
    )

    result = hybrid_retrieve_with_trace(
        engine,
        "evaluation ranking",
        trace_id="trace-evaluation-top-twenty",
        filters=build_retrieval_filters(request_filters={}, viewer_user_id=None),
        embedding_provider=FailingEmbeddingProvider(),
        bm25_retriever=TopTwentyBm25Retriever(),
        settings=settings,
    )

    assert len(result["final_evidence"]) == settings.final_evidence_limit
    with engine.connect() as connection:
        retrieval_log = connection.execute(
            select(retrieval_logs).where(retrieval_logs.c.trace_id == "trace-evaluation-top-twenty")
        ).mappings().one()
    retrieved = retrieve_evaluation_candidates(
        engine,
        query="evaluation ranking",
        filters=build_retrieval_filters(request_filters={}, viewer_user_id=None),
        settings=settings,
        embedding_provider=FailingEmbeddingProvider(),
        bm25_retriever=TopTwentyBm25Retriever(),
    )

    assert len(retrieval_log["rerank"]["items"]) == settings.fusion_top_k
    assert len(retrieved) == 20
    assert recall_at_k(retrieved, [{"chunk_id": "chunk-evaluation-06", "document_id": "doc-m7040"}], k=20) == 1.0
    assert ndcg_at_k(retrieved, [{"chunk_id": "chunk-evaluation-06", "document_id": "doc-m7040"}], k=10) > 0.0


def test_evaluate_retrieval_acceptance_allows_improved_metrics() -> None:
    script = evaluate_retrieval_script()
    baseline = {"recall_at_20": 0.6, "ndcg_at_10": 0.7, "retrieval_p95_ms": 10.0}
    metrics = {
        "protected_identifier_cases": 1,
        "protected_identifier_preservation": 1.0,
        "unauthorized_final_evidence": 0,
        "explicit_model_confusion": 0,
        "recall_at_20": 0.7,
        "ndcg_at_10": 0.7,
        "retrieval_p95_ms": 15.0,
    }

    script.assert_acceptance(metrics, baseline)


def test_evaluate_retrieval_acceptance_rejects_unchanged_metrics() -> None:
    script = evaluate_retrieval_script()
    baseline = {"recall_at_20": 0.7, "ndcg_at_10": 0.7, "retrieval_p95_ms": 10.0}
    metrics = {
        "protected_identifier_cases": 1,
        "protected_identifier_preservation": 1.0,
        "unauthorized_final_evidence": 0,
        "explicit_model_confusion": 0,
        "recall_at_20": 0.7,
        "ndcg_at_10": 0.7,
        "retrieval_p95_ms": 10.0,
    }

    with pytest.raises(SystemExit, match="至少有一项"):
        script.assert_acceptance(metrics, baseline)


def test_evaluate_retrieval_defaults_to_configured_dataset(monkeypatch) -> None:
    script = evaluate_retrieval_script()
    settings = SimpleNamespace(evaluation_default_dataset="release-smoke")
    captured = {}

    monkeypatch.setattr(
        script,
        "parse_args",
        lambda: argparse.Namespace(dataset=None, prompt_version="retrieval-v2", baseline=None),
    )
    monkeypatch.setattr(script, "get_settings", lambda: settings)
    monkeypatch.setattr(script, "get_engine", lambda: "engine")
    monkeypatch.setattr(
        script,
        "run_evaluation_dataset",
        lambda engine, **kwargs: captured.update(engine=engine, **kwargs)
        or SimpleNamespace(metrics_summary={}),
    )

    assert script.main() == 0
    assert captured["engine"] == "engine"
    assert captured["settings"] is settings
    assert captured["dataset_version"] == "release-smoke"


def test_evaluation_summary_includes_retrieval_metrics(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)

    result = run_evaluation(
        engine,
        [
            EvaluationQuestion(
                question_id="q1",
                question="M7040 E01 hydraulic pump",
                category="fault",
                expected_sources=[{"document_id": "doc-m7040", "chunk_id": "chunk-m7040"}],
                expected_model="M7040",
            )
        ],
        dataset_version="curated-v2",
        model_config={},
        prompt_version="p1",
        settings=evaluation_settings(tmp_path),
    )

    assert result.metrics_summary["recall_at_20"] == 1.0
    assert result.metrics_summary["ndcg_at_10"] == 1.0
    assert result.metrics_summary["protected_identifier_cases"] == 1
    assert result.metrics_summary["protected_identifier_preservation"] == 1.0
    assert result.metrics_summary["unauthorized_final_evidence"] == 0
    assert result.metrics_summary["explicit_model_confusion"] == 0
    assert result.metrics_summary["retrieval_p95_ms"] >= 0


def test_evaluation_runner_records_run_metadata_metrics_and_failure_types(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    questions = [
        EvaluationQuestion(
            question_id="q1",
            question="M7040 E01 hydraulic pump repair",
            category="fault_code",
            expected_sources=[{"document_id": "doc-m7040", "page": 12, "chunk_id": "chunk-m7040"}],
            expected_model="M7040",
            requires_safety_warning=True,
        ),
        EvaluationQuestion(
            question_id="q2",
            question="orchard sprayer calibration nozzle",
            category="missing",
            expected_sources=[{"document_id": "doc-missing"}],
        ),
    ]

    result = run_evaluation(
        engine,
        questions,
        dataset_version="curated-v1",
        model_config={"retrieval": "deterministic"},
        prompt_version="p0",
        code_version="test-sha",
        settings=evaluation_settings(tmp_path),
    )

    assert result.dataset_version == "curated-v1"
    assert len(result.question_results) == 2
    assert result.metrics_summary["top5_source_hit_rate"] == 0.5
    assert result.metrics_summary["citation_correctness_rate"] == 0.5
    assert result.metrics_summary["safety_compliance_rate"] == 1.0
    assert result.failure_types["evidence_insufficient"] == 1

    with engine.connect() as connection:
        row = connection.execute(select(evaluation_runs)).mappings().one()

    assert row["run_id"] == result.run_id
    assert row["dataset_version"] == "curated-v1"
    assert row["model_config"] == {"retrieval": "deterministic"}
    assert row["prompt_version"] == "p0"
    assert row["code_version"] == "test-sha"
    assert row["metrics_summary"] == result.metrics_summary
    assert row["failure_types"] == result.failure_types
    assert row["started_at"] is not None
    assert row["finished_at"] is not None


def test_evaluation_runner_can_rerun_same_question_set(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    questions = [
        EvaluationQuestion(
            question_id="q1",
            question="M7040 E01 hydraulic pump repair",
            category="fault_code",
            expected_sources=[{"document_id": "doc-m7040"}],
        )
    ]

    first = run_evaluation(
        engine,
        questions,
        dataset_version="curated-v1",
        model_config={},
        prompt_version="p0",
        settings=evaluation_settings(tmp_path),
    )
    second = run_evaluation(
        engine,
        questions,
        dataset_version="curated-v1",
        model_config={},
        prompt_version="p0",
        settings=evaluation_settings(tmp_path),
    )

    assert first.run_id != second.run_id
    with engine.connect() as connection:
        rows = connection.execute(select(evaluation_runs)).mappings().all()
    assert len(rows) == 2
    assert {row["dataset_version"] for row in rows} == {"curated-v1"}


def test_import_and_load_evaluation_questions_round_trips_dataset_records(tmp_path) -> None:
    engine = create_test_engine(tmp_path)

    imported = import_evaluation_questions(
        engine,
        dataset_version="curated-v1",
        questions=[
            {
                "question_id": "fault-code-001",
                "category": "fault_code",
                "question": "M7040 E01 是什么意思？",
                "expected_model": "M7040",
                "expected_answer_summary": "检查液压泵压力并确认 E01 含义。",
                "expected_sources": [{"document_id": "doc-m7040", "page": 12, "chunk_id": "chunk-m7040"}],
                "requires_safety_warning": False,
                "must_not_include": ["编造步骤"],
            },
            {
                "question_id": "general-001",
                "category": "general",
                "question": "如何检查液压泵压力？",
                "expected_sources": [],
                "requires_safety_warning": True,
            },
        ],
    )

    assert imported == 2

    loaded = load_evaluation_questions(engine, dataset_version="curated-v1")
    assert [item.question_id for item in loaded] == ["fault-code-001", "general-001"]
    assert loaded[0].expected_sources == [{"document_id": "doc-m7040", "page": 12, "chunk_id": "chunk-m7040"}]
    assert loaded[0].expected_answer_summary == "检查液压泵压力并确认 E01 含义。"
    assert loaded[0].must_not_include == ["编造步骤"]
    assert loaded[1].counts_toward_source_metrics is False

    with engine.connect() as connection:
        rows = connection.execute(
            select(evaluation_questions).where(evaluation_questions.c.dataset_version == "curated-v1")
        ).mappings().all()

    assert len(rows) == 2


def test_evaluation_runner_excludes_questions_without_expected_sources_from_formal_source_metrics(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    questions = [
        EvaluationQuestion(
            question_id="q1",
            question="M7040 E01 hydraulic pump repair",
            category="fault_code",
            expected_sources=[{"document_id": "doc-m7040"}],
        ),
        EvaluationQuestion(
            question_id="q2",
            question="How should operators inspect hydraulic hoses before starting work?",
            category="safety",
            expected_sources=[],
            requires_safety_warning=True,
        ),
    ]

    result = run_evaluation(
        engine,
        questions,
        dataset_version="curated-v1",
        model_config={"retrieval": "deterministic"},
        prompt_version="p0",
        settings=evaluation_settings(tmp_path),
    )

    source_optional = next(item for item in result.question_results if item["question_id"] == "q2")
    assert source_optional["counts_toward_source_metrics"] is False
    assert source_optional["source_hit"] is None
    assert "source_miss" not in source_optional["failures"]
    assert result.metrics_summary["top5_source_hit_rate"] == 1.0
    assert result.metrics_summary["citation_correctness_rate"] == 1.0


def test_run_evaluation_dataset_loads_fixed_question_set_and_records_default_model_config(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    settings = evaluation_settings(tmp_path)
    import_evaluation_questions(
        engine,
        dataset_version="curated-mvp",
        questions=[
            {
                "question_id": "q1",
                "category": "fault_code",
                "question": "M7040 E01 hydraulic pump repair",
                "expected_model": "M7040",
                "expected_sources": [{"document_id": "doc-m7040", "chunk_id": "chunk-m7040"}],
            },
            {
                "question_id": "q2",
                "category": "missing",
                "question": "orchard sprayer calibration nozzle",
                "expected_sources": [{"document_id": "doc-missing"}],
            },
        ],
    )

    result = run_evaluation_dataset(
        engine,
        settings=settings,
        prompt_version="prompt-v1",
        code_version="sha-1234",
    )

    assert result.dataset_version == "curated-mvp"
    assert len(result.question_results) == 2

    with engine.connect() as connection:
        row = connection.execute(select(evaluation_runs)).mappings().one()

    assert row["dataset_version"] == "curated-mvp"
    assert row["prompt_version"] == "prompt-v1"
    assert row["code_version"] == "sha-1234"
    assert row["model_config"] == model_config_from_settings(settings)
    assert row["metrics_summary"]["top5_source_hit_rate"] == 0.5
    assert row["metrics_summary"]["citation_correctness_rate"] == 0.5


def test_run_evaluation_dataset_allows_dataset_override(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    settings = evaluation_settings(tmp_path)
    import_evaluation_questions(
        engine,
        dataset_version="curated-alt",
        questions=[
            {
                "question_id": "q-alt",
                "category": "fault_code",
                "question": "M7040 E01 hydraulic pump repair",
                "expected_sources": [{"document_id": "doc-m7040"}],
            }
        ],
    )

    result = run_evaluation_dataset(
        engine,
        settings=settings,
        dataset_version="curated-alt",
        prompt_version="prompt-v2",
    )

    assert result.dataset_version == "curated-alt"
    assert [item["question_id"] for item in result.question_results] == ["q-alt"]


def test_run_evaluation_dataset_rejects_empty_fixed_question_set(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    settings = evaluation_settings(tmp_path)

    try:
        run_evaluation_dataset(
            engine,
            settings=settings,
            dataset_version="missing-dataset",
            prompt_version="prompt-v1",
        )
    except ValueError as exc:
        assert str(exc) == "No evaluation questions found for dataset_version=missing-dataset"
    else:
        raise AssertionError("expected ValueError for empty evaluation dataset")
