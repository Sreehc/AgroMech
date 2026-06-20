from sqlalchemy import create_engine, select

from agromech_api.db.models import evaluation_runs, metadata
from agromech_api.evaluation import EvaluationQuestion, run_evaluation
from test_hybrid_retrieval import seed_retrieval_corpus


def create_test_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    return engine


def test_evaluation_runner_records_run_metadata_metrics_and_failure_types(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    questions = [
        EvaluationQuestion(
            question_id="q1",
            question="M7040 E01 hydraulic pump repair",
            category="fault_code",
            expected_source_document_ids=["doc-m7040"],
            expected_model="M7040",
            requires_safety_warning=True,
        ),
        EvaluationQuestion(
            question_id="q2",
            question="orchard sprayer calibration nozzle",
            category="missing",
            expected_source_document_ids=["doc-missing"],
        ),
    ]

    result = run_evaluation(
        engine,
        questions,
        dataset_version="curated-v1",
        model_config={"retrieval": "deterministic"},
        prompt_version="p0",
        code_version="test-sha",
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
            expected_source_document_ids=["doc-m7040"],
        )
    ]

    first = run_evaluation(engine, questions, dataset_version="curated-v1", model_config={}, prompt_version="p0")
    second = run_evaluation(engine, questions, dataset_version="curated-v1", model_config={}, prompt_version="p0")

    assert first.run_id != second.run_id
    with engine.connect() as connection:
        rows = connection.execute(select(evaluation_runs)).mappings().all()
    assert len(rows) == 2
    assert {row["dataset_version"] for row in rows} == {"curated-v1"}
