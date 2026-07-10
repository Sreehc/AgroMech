from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Engine, delete, insert, select

from agromech_api.core.config import Settings
from agromech_api.db.models import evaluation_questions, evaluation_runs
from agromech_api.qa.text import answer_text_question


@dataclass(frozen=True)
class EvaluationQuestion:
    question_id: str
    question: str
    category: str
    expected_sources: list[dict[str, object]]
    expected_model: str | None = None
    expected_answer_summary: str | None = None
    requires_safety_warning: bool = False
    must_not_include: list[str] | None = None
    counts_toward_source_metrics: bool | None = None


@dataclass(frozen=True)
class EvaluationResult:
    run_id: str
    dataset_version: str
    question_results: list[dict[str, object]]
    metrics_summary: dict[str, float]
    failure_types: dict[str, int]


def run_evaluation_dataset(
    engine: Engine,
    *,
    settings: Settings,
    dataset_version: str | None = None,
    prompt_version: str,
    code_version: str | None = None,
) -> EvaluationResult:
    target_dataset = dataset_version or settings.evaluation_default_dataset
    questions = load_evaluation_questions(engine, dataset_version=target_dataset)
    if not questions:
        raise ValueError(f"No evaluation questions found for dataset_version={target_dataset}")
    return run_evaluation(
        engine,
        questions,
        dataset_version=target_dataset,
        model_config=model_config_from_settings(settings),
        prompt_version=prompt_version,
        code_version=code_version,
        settings=settings,
    )


def import_evaluation_questions(
    engine: Engine,
    *,
    dataset_version: str,
    questions: list[dict[str, object]],
) -> int:
    normalized_rows = [
        serialize_question_row(dataset_version=dataset_version, question=question)
        for question in questions
    ]
    with engine.begin() as connection:
        connection.execute(
            delete(evaluation_questions).where(evaluation_questions.c.dataset_version == dataset_version)
        )
        if normalized_rows:
            connection.execute(insert(evaluation_questions), normalized_rows)
    return len(normalized_rows)


def load_evaluation_questions(engine: Engine, *, dataset_version: str) -> list[EvaluationQuestion]:
    with engine.connect() as connection:
        rows = connection.execute(
            select(evaluation_questions)
            .where(evaluation_questions.c.dataset_version == dataset_version)
            .order_by(evaluation_questions.c.created_at.asc(), evaluation_questions.c.question_id.asc())
        ).mappings().all()
    return [row_to_question(row) for row in rows]


def model_config_from_settings(settings: Settings) -> dict[str, object]:
    return {
        "model_provider": settings.model_provider,
        "llm_model": settings.llm_model,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "embedding_version": settings.embedding_version,
        "vector_backend": "pgvector",
        "graph_backend": settings.graph_backend,
        "rerank_enabled": settings.rerank_enabled,
        "rerank_model": settings.rerank_model if settings.rerank_enabled else None,
        "rerank_top_k": settings.rerank_top_k,
        "final_evidence_limit": settings.final_evidence_limit,
        "evaluation_top_k": settings.evaluation_top_k,
    }


def run_evaluation(
    engine: Engine,
    questions: list[EvaluationQuestion],
    *,
    dataset_version: str,
    model_config: dict[str, object],
    prompt_version: str,
    code_version: str | None = None,
    settings: Settings | None = None,
) -> EvaluationResult:
    run_id = str(uuid4())
    started_at = datetime.now(UTC)
    question_results = [
        evaluate_question(engine, run_id=run_id, question=item, settings=settings)
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


def evaluate_question(
    engine: Engine,
    *,
    run_id: str,
    question: EvaluationQuestion,
    settings: Settings | None = None,
) -> dict[str, object]:
    answer = answer_text_question(
        engine,
        question=question.question,
        trace_id=f"eval-{run_id[:8]}-{question.question_id}",
        settings=settings,
    )
    cited_document_ids = [citation["document_id"] for citation in answer["citations"]]
    expected_documents = expected_source_document_ids(question.expected_sources)
    counts_toward_source_metrics = (
        question.counts_toward_source_metrics
        if question.counts_toward_source_metrics is not None
        else bool(question.expected_sources)
    )
    source_hit = None
    citation_correct = None
    if counts_toward_source_metrics:
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
    if counts_toward_source_metrics and not source_hit:
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
        "counts_toward_source_metrics": counts_toward_source_metrics,
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
    source_scored = [result for result in question_results if result["counts_toward_source_metrics"]]
    safety_questions = [result for result in question_results if result["requires_safety_warning"]]
    return {
        "top5_source_hit_rate": ratio(source_scored, "source_hit"),
        "citation_correctness_rate": ratio(source_scored, "citation_correct"),
        "model_confusion_rate": sum(1 for result in question_results if result["model_confused"]) / total,
        "safety_compliance_rate": (
            sum(1 for result in safety_questions if result["safety_compliant"]) / len(safety_questions)
            if safety_questions
            else 1.0
        ),
    }


def ratio(results: list[dict[str, object]], key: str) -> float:
    if not results:
        return 0.0
    return sum(1 for result in results if result[key]) / len(results)


def failure_types_for(question_results: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in question_results:
        for failure in result["failures"]:
            counts[failure] = counts.get(failure, 0) + 1
    return counts


def serialize_question_row(*, dataset_version: str, question: dict[str, object]) -> dict[str, object]:
    question_id = require_non_empty_string(question, "question_id")
    category = require_non_empty_string(question, "category")
    prompt = require_non_empty_string(question, "question")
    expected_sources = normalize_expected_sources(question.get("expected_sources"))
    expected_model = optional_string(question.get("expected_model"))
    expected_answer_summary = optional_string(question.get("expected_answer_summary"))
    must_not_include = normalize_string_list(question.get("must_not_include"))
    requires_safety_warning = bool(question.get("requires_safety_warning", False))
    return {
        "id": str(uuid4()),
        "question_id": question_id,
        "dataset_version": dataset_version,
        "category": category,
        "question": prompt,
        "expected_model": expected_model,
        "expected_answer_summary": expected_answer_summary,
        "expected_sources": expected_sources,
        "requires_safety_warning": requires_safety_warning,
        "must_not_include": must_not_include,
    }


def row_to_question(row) -> EvaluationQuestion:
    expected_sources = normalize_expected_sources(row["expected_sources"])
    return EvaluationQuestion(
        question_id=row["question_id"],
        question=row["question"],
        category=row["category"],
        expected_sources=expected_sources,
        expected_model=row["expected_model"],
        expected_answer_summary=row["expected_answer_summary"],
        requires_safety_warning=bool(row["requires_safety_warning"]),
        must_not_include=normalize_string_list(row["must_not_include"]),
        counts_toward_source_metrics=bool(expected_sources),
    )


def require_non_empty_string(payload: dict[str, object], field: str) -> str:
    value = optional_string(payload.get(field))
    if value is None:
        raise ValueError(f"{field} is required")
    return value


def optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("expected string value")
    normalized = value.strip()
    return normalized or None


def normalize_string_list(value: object) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("expected list value")
    items: list[str] = []
    for item in value:
        normalized = optional_string(item)
        if normalized is None:
            continue
        items.append(normalized)
    return items or None


def normalize_expected_sources(value: object) -> list[dict[str, object]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("expected_sources must be a list")
    normalized: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("expected_sources entries must be objects")
        document_id = optional_string(item.get("document_id"))
        if document_id is None:
            raise ValueError("expected_sources.document_id is required")
        source = {"document_id": document_id}
        chunk_id = optional_string(item.get("chunk_id"))
        if chunk_id:
            source["chunk_id"] = chunk_id
        page = item.get("page")
        if page is not None:
            if not isinstance(page, int):
                raise ValueError("expected_sources.page must be an integer")
            source["page"] = page
        normalized.append(source)
    return normalized


def expected_source_document_ids(expected_sources: list[dict[str, object]]) -> set[str]:
    return {str(item["document_id"]) for item in expected_sources if item.get("document_id")}
