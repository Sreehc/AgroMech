from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Engine, insert

from agromech_api.db.models import evaluation_runs
from agromech_api.text_qa import answer_text_question


@dataclass(frozen=True)
class EvaluationQuestion:
    question_id: str
    question: str
    category: str
    expected_source_document_ids: list[str]
    expected_model: str | None = None
    requires_safety_warning: bool = False


@dataclass(frozen=True)
class EvaluationResult:
    run_id: str
    dataset_version: str
    question_results: list[dict[str, object]]
    metrics_summary: dict[str, float]
    failure_types: dict[str, int]


def run_evaluation(
    engine: Engine,
    questions: list[EvaluationQuestion],
    *,
    dataset_version: str,
    model_config: dict[str, object],
    prompt_version: str,
    code_version: str | None = None,
) -> EvaluationResult:
    run_id = str(uuid4())
    started_at = datetime.now(UTC)
    question_results = [
        evaluate_question(engine, run_id=run_id, question=item)
        for item in questions
    ]
    metrics_summary = metrics_for(question_results)
    failure_types = failure_types_for(question_results)
    finished_at = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            insert(evaluation_runs).values(
                id=str(uuid4()),
                run_id=run_id,
                dataset_version=dataset_version,
                model_config=model_config,
                prompt_version=prompt_version,
                code_version=code_version,
                metrics_summary=metrics_summary,
                failure_types=failure_types,
                started_at=started_at,
                finished_at=finished_at,
            )
        )
    return EvaluationResult(
        run_id=run_id,
        dataset_version=dataset_version,
        question_results=question_results,
        metrics_summary=metrics_summary,
        failure_types=failure_types,
    )


def evaluate_question(engine: Engine, *, run_id: str, question: EvaluationQuestion) -> dict[str, object]:
    answer = answer_text_question(
        engine,
        question=question.question,
        trace_id=f"eval-{run_id[:8]}-{question.question_id}",
    )
    cited_document_ids = [citation["document_id"] for citation in answer["citations"]]
    expected_documents = set(question.expected_source_document_ids)
    source_hit = bool(expected_documents.intersection(cited_document_ids))
    citation_correct = source_hit and bool(cited_document_ids)
    safety_compliant = True
    if question.requires_safety_warning:
        safety_compliant = bool(answer["safety_warnings"])
    model_confused = False
    if question.expected_model:
        evidence_text = " ".join(
            str(value)
            for citation in answer["citations"]
            for value in [citation["document_title"], citation["evidence_snippet"]]
        ).lower()
        model_confused = question.expected_model.lower() not in evidence_text
    failures = []
    if not answer["citations"]:
        failures.append("evidence_insufficient")
    if not source_hit:
        failures.append("source_miss")
    if model_confused:
        failures.append("model_confusion")
    if not safety_compliant:
        failures.append("safety_missing")
    return {
        "question_id": question.question_id,
        "category": question.category,
        "trace_id": answer["trace_id"],
        "answer": answer["answer"],
        "citations": answer["citations"],
        "source_hit": source_hit,
        "citation_correct": citation_correct,
        "model_confused": model_confused,
        "safety_compliant": safety_compliant,
        "requires_safety_warning": question.requires_safety_warning,
        "failures": failures,
    }


def metrics_for(question_results: list[dict[str, object]]) -> dict[str, float]:
    total = len(question_results)
    if total == 0:
        return {
            "top5_source_hit_rate": 0.0,
            "citation_correctness_rate": 0.0,
            "model_confusion_rate": 0.0,
            "safety_compliance_rate": 0.0,
        }
    safety_questions = [result for result in question_results if result["requires_safety_warning"]]
    return {
        "top5_source_hit_rate": ratio(question_results, "source_hit"),
        "citation_correctness_rate": ratio(question_results, "citation_correct"),
        "model_confusion_rate": sum(1 for result in question_results if result["model_confused"]) / total,
        "safety_compliance_rate": (
            sum(1 for result in safety_questions if result["safety_compliant"]) / len(safety_questions)
            if safety_questions
            else 1.0
        ),
    }


def ratio(results: list[dict[str, object]], key: str) -> float:
    return sum(1 for result in results if result[key]) / len(results)


def failure_types_for(question_results: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in question_results:
        for failure in result["failures"]:
            counts[failure] = counts.get(failure, 0) + 1
    return counts
