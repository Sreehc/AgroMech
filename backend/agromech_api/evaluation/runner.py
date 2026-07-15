from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import math
from uuid import uuid4

from sqlalchemy import Engine, delete, insert, select

from agromech_api.core.config import Settings
from agromech_api.db.enums import DocumentStatus
from agromech_api.db.models import documents, evaluation_questions, evaluation_runs, retrieval_logs
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
        "bm25_backend": "pg_search",
        "bm25_top_k": settings.bm25_top_k,
        "dense_top_k": settings.dense_top_k,
        "dense_only_min_similarity": settings.dense_only_min_similarity,
        "rrf_k": settings.rrf_k,
        "rrf_dense_weight": settings.rrf_dense_weight,
        "rrf_bm25_weight": settings.rrf_bm25_weight,
        "fusion_top_k": settings.fusion_top_k,
        "query_rewrite_enabled": settings.query_rewrite_enabled,
        "query_rewrite_model": settings.query_rewrite_model if settings.query_rewrite_enabled else None,
        "query_rewrite_timeout_seconds": settings.query_rewrite_timeout_seconds,
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
        filters={"model": question.expected_model} if question.expected_model else None,
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
    with engine.connect() as connection:
        retrieval_log = connection.execute(
            select(retrieval_logs).where(retrieval_logs.c.trace_id == answer["trace_id"])
        ).mappings().one()
    rewrite_container = dict(retrieval_log["query_rewrite"] or {})
    rewrite = dict(rewrite_container.get("final") or {})
    rewrite_duration_ms = sum(
        float(item.get("duration_ms", 0.0))
        for item in rewrite_container.get("attempts", [])
        if isinstance(item, dict)
    )
    protected = [str(value) for value in rewrite.get("protected_identifiers", [])]
    rewritten_query = str(rewrite.get("query") or question.question)
    protected_preserved = all(value.lower() in rewritten_query.lower() for value in protected)
    candidates = list(retrieval_log["candidates"] or [])
    candidates_by_chunk = {
        str(item["chunk_id"]): item for item in candidates if item.get("chunk_id")
    }
    rerank_items = sorted(
        (retrieval_log["rerank"] or {}).get("items", []),
        key=lambda item: int(item.get("after_rank", 10**9)),
    )
    retrieved = [
        candidates_by_chunk[str(item["chunk_id"])]
        for item in rerank_items
        if item.get("chunk_id") and str(item["chunk_id"]) in candidates_by_chunk
    ]
    if not retrieved:
        retrieved = candidates
    final_document_ids = {
        str(item["document_id"])
        for item in retrieval_log["final_evidence"] or []
        if item.get("document_id")
    }
    with engine.connect() as connection:
        final_documents = (
            connection.execute(
                select(
                    documents.c.id,
                    documents.c.visibility,
                    documents.c.status,
                    documents.c.deleted_at,
                    documents.c.model,
                ).where(documents.c.id.in_(final_document_ids))
            )
            .mappings()
            .all()
            if final_document_ids
            else []
        )
    unauthorized = [
        row
        for row in final_documents
        if row["visibility"] != "public"
        or row["status"] != DocumentStatus.INDEXED.value
        or row["deleted_at"] is not None
    ]
    unauthorized_count = len(final_document_ids) - len(final_documents) + len(unauthorized)
    wrong_model = [
        row
        for row in final_documents
        if question.expected_model
        and str(row["model"] or "").lower() != question.expected_model.lower()
    ]
    model_confused = bool(wrong_model)
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
        "retrieved_sources": retrieved,
        "recall_at_20": recall_at_k(retrieved, question.expected_sources, k=20),
        "ndcg_at_10": ndcg_at_k(retrieved, question.expected_sources, k=10),
        "has_protected_identifiers": bool(protected),
        "protected_identifiers_preserved": protected_preserved,
        "unauthorized_final_evidence": unauthorized_count,
        "wrong_model_final_evidence": len(wrong_model),
        "retrieval_duration_ms": rewrite_duration_ms + float(
            (retrieval_log["fusion"] or {}).get("retrieval_duration_ms", 0.0)
        ),
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
            "recall_at_20": 0.0,
            "ndcg_at_10": 0.0,
            "protected_identifier_cases": 0,
            "protected_identifier_preservation": 0.0,
            "unauthorized_final_evidence": 0,
            "explicit_model_confusion": 0,
            "retrieval_p95_ms": 0.0,
        }
    source_scored = [result for result in question_results if result["counts_toward_source_metrics"]]
    safety_questions = [result for result in question_results if result["requires_safety_warning"]]
    protected_scored = [result for result in question_results if result["has_protected_identifiers"]]
    return {
        "top5_source_hit_rate": ratio(source_scored, "source_hit"),
        "citation_correctness_rate": ratio(source_scored, "citation_correct"),
        "model_confusion_rate": sum(1 for result in question_results if result["model_confused"]) / total,
        "safety_compliance_rate": (
            sum(1 for result in safety_questions if result["safety_compliant"]) / len(safety_questions)
            if safety_questions
            else 1.0
        ),
        "recall_at_20": (
            sum(float(result["recall_at_20"]) for result in source_scored) / len(source_scored)
            if source_scored
            else 0.0
        ),
        "ndcg_at_10": (
            sum(float(result["ndcg_at_10"]) for result in source_scored) / len(source_scored)
            if source_scored
            else 0.0
        ),
        "protected_identifier_cases": len(protected_scored),
        "protected_identifier_preservation": ratio(protected_scored, "protected_identifiers_preserved"),
        "unauthorized_final_evidence": sum(
            int(result["unauthorized_final_evidence"]) for result in question_results
        ),
        "explicit_model_confusion": sum(
            int(result["wrong_model_final_evidence"]) for result in question_results
        ),
        "retrieval_p95_ms": percentile(
            [float(result["retrieval_duration_ms"]) for result in question_results], 0.95
        ),
    }


def ratio(results: list[dict[str, object]], key: str) -> float:
    if not results:
        return 0.0
    return sum(1 for result in results if result[key]) / len(results)


def source_key(source: dict[str, object]) -> tuple[str, str]:
    if source.get("chunk_id"):
        return "chunk", str(source["chunk_id"])
    return "document", str(source["document_id"])


def source_is_relevant(candidate: dict[str, object], expected: dict[str, object]) -> bool:
    kind, value = source_key(expected)
    return str(candidate.get("chunk_id" if kind == "chunk" else "document_id")) == value


def recall_at_k(
    retrieved: list[dict[str, object]], expected: list[dict[str, object]], *, k: int
) -> float:
    if not expected:
        return 0.0
    matched = sum(
        1
        for source in expected
        if any(source_is_relevant(candidate, source) for candidate in retrieved[:k])
    )
    return matched / len(expected)


def ndcg_at_k(
    retrieved: list[dict[str, object]], expected: list[dict[str, object]], *, k: int
) -> float:
    if not expected:
        return 0.0
    remaining = list(expected)
    gains: list[float] = []
    for candidate in retrieved[:k]:
        match = next(
            (source for source in remaining if source_is_relevant(candidate, source)), None
        )
        gains.append(1.0 if match else 0.0)
        if match:
            remaining.remove(match)
    dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal = sum(1.0 / math.log2(index + 2) for index in range(min(len(expected), k)))
    return dcg / ideal if ideal else 0.0


def percentile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * probability) - 1)]


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
