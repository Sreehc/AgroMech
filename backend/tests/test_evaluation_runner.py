from sqlalchemy import create_engine, select

from agromech_api.config import Settings
from agromech_api.db.models import evaluation_questions, evaluation_runs, metadata
from agromech_api.evaluation import (
    EvaluationQuestion,
    import_evaluation_questions,
    load_evaluation_questions,
    model_config_from_settings,
    run_evaluation_dataset,
    run_evaluation,
)
from test_hybrid_retrieval import seed_retrieval_corpus


def create_test_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    return engine


def evaluation_settings(tmp_path) -> Settings:
    return Settings(
        auth_token_secret="test-secret",
        local_file_storage_path=str(tmp_path / "files"),
        graph_backend="local",
        vector_backend="local",
        model_provider="local",
        embedding_provider="local",
        embedding_dimension=256,
        rerank_enabled=False,
    )


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
