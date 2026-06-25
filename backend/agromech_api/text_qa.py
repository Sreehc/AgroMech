from __future__ import annotations

from uuid import uuid4

from fastapi import Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import Engine, insert, or_, select, update

from agromech_api.auth import UserContext, require_roles
from agromech_api.answer_generation import AnswerGenerationError, build_answer_generator
from agromech_api.config import Settings, get_settings
from agromech_api.db.enums import ChunkType, UserRole
from agromech_api.db.models import answer_citations, document_chunks, documents, qa_records, retrieval_logs
from agromech_api.errors import AppError, ErrorCode
from agromech_api.hybrid_retrieval import hybrid_retrieve_with_trace
from agromech_api.qa_session_history import append_text_session_exchange, ensure_session_belongs_to_user
from agromech_api.query_understanding import parse_query


MAX_QUESTION_LENGTH = 2000
INJECTION_PATTERNS = (
    "忽略引用",
    "忽略来源",
    "不要引用",
    "无视引用",
    "编造",
    "伪造",
    "绕过安全",
    "不要任何警告",
    "关闭安全提醒",
    "无需安全",
    "不停机拆",
    "ignore citation",
    "ignore citations",
    "ignore sources",
    "without citations",
    "without sources",
    "fabricate",
    "make up",
    "bypass safety",
    "ignore safety",
    "without warning",
    "no warning",
)
SAFETY_WARNING = "涉及液压、电气、发动机、制动或旋转部件时，维修前请停机、断电、释放压力，并按厂家安全规程操作。"
QA_FILTER_KEYS = ("brand", "model", "document_type", "subsystem", "language")


class TextQaRequest(BaseModel):
    question: str = Field(...)
    filters: dict[str, str | None] = Field(default_factory=dict)
    session_id: str | None = None
    mode: str = "standard"


def answer_text_question(
    engine: Engine,
    *,
    question: str,
    trace_id: str,
    filters: dict[str, str | None] | None = None,
    settings: Settings | None = None,
    username: str | None = None,
    session_id: str | None = None,
) -> dict[str, object]:
    normalized_question = validate_question(question)
    normalized_filters = {key: value for key, value in (filters or {}).items() if value is not None}
    if session_id:
        if not username:
            raise AppError(ErrorCode.UNAUTHORIZED, "Authentication required", status_code=status.HTTP_401_UNAUTHORIZED)
        ensure_session_belongs_to_user(engine, username=username, session_id=session_id)
    if should_refuse_prompt(normalized_question):
        payload = refused_answer(engine, question=normalized_question, trace_id=trace_id)
        if session_id and username:
            append_text_session_exchange(
                engine,
                username=username,
                session_id=session_id,
                question=normalized_question,
                filters=normalized_filters,
                payload=payload,
            )
        return payload

    search_query = query_with_filters(normalized_question, filters or {})
    settings = settings or get_settings()
    vector_store = None
    embedding_provider = None
    vector_collection = None
    graph_service = None
    rerank_provider = None
    if settings.vector_backend == "zvec":
        from agromech_api.embedding import build_embedding_provider
        from agromech_api.zvec_store import build_vector_store

        vector_store = build_vector_store(settings)
        embedding_provider = build_embedding_provider(settings)
        vector_collection = settings.zvec_collection
    if settings.graph_backend == "neo4j":
        from agromech_api.graph_rag import build_graph_service

        graph_service = build_graph_service(engine, settings)
    if settings.rerank_enabled:
        from agromech_api.rerank import build_rerank_provider

        rerank_provider = build_rerank_provider(settings)
    retrieval = hybrid_retrieve_with_trace(
        engine,
        search_query,
        trace_id=trace_id,
        logged_query=normalized_question,
        filters=normalized_filters,
        vector_store=vector_store,
        vector_collection=vector_collection,
        embedding_provider=embedding_provider,
        graph_service=graph_service,
        rerank_provider=rerank_provider,
        rerank_top_k=settings.rerank_top_k,
        settings=settings,
    )
    if retrieval["status"] == "evidence_insufficient":
        payload = evidence_insufficient_answer(trace_id)
        record_qa(engine, question=normalized_question, payload=payload)
        if session_id and username:
            append_text_session_exchange(
                engine,
                username=username,
                session_id=session_id,
                question=normalized_question,
                filters=normalized_filters,
                payload=payload,
            )
        return payload

    final_evidence = retrieval["final_evidence"][: settings.final_evidence_limit]
    retrieval["final_evidence"] = final_evidence
    trim_retrieval_final_evidence(engine, trace_id=trace_id, final_evidence=final_evidence)
    citations = build_citations(engine, final_evidence)
    if not citations:
        payload = evidence_insufficient_answer(trace_id)
        record_qa(engine, question=normalized_question, payload=payload)
        if session_id and username:
            append_text_session_exchange(
                engine,
                username=username,
                session_id=session_id,
                question=normalized_question,
                filters=normalized_filters,
                payload=payload,
            )
        return payload

    parsed = parse_query(search_query, engine=engine)
    safety_warnings = [SAFETY_WARNING] if parsed.safety_sensitive or evidence_is_safety_sensitive(citations) else []
    uncertainty = uncertainty_payload(parsed.scope_uncertain, citations)
    answer_generator = build_answer_generator(settings)
    if answer_generator is not None:
        try:
            generated = answer_generator.generate(
                question=normalized_question,
                citations=citations,
                safety_warnings=safety_warnings,
                uncertainty=uncertainty,
                filters=parsed.filters,
            )
        except AnswerGenerationError as exc:
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                str(exc),
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        answer = str(generated["answer"])
        sections = dict(generated["sections"])
    else:
        sections = {
            "conclusion": conclusion_from_citation(citations[0]),
            "applicability": applicability_section(parsed.filters, citations),
            "possible_causes": possible_causes_section(citations),
            "inspection_steps": inspection_steps_section(citations),
            "safety_reminder": safety_warnings,
            "citations": citation_section(citations),
            "uncertainty": uncertainty,
        }
        answer = compose_answer(sections)
    sections.setdefault("citations", citation_section(citations))
    sections.setdefault("uncertainty", uncertainty)
    sections.setdefault("safety_reminder", safety_warnings)
    payload = {
        "answer": answer,
        "sections": sections,
        "citations": citations,
        "trace_id": trace_id,
        "uncertainty": uncertainty,
        "safety_warnings": safety_warnings,
    }
    record_qa(engine, question=normalized_question, payload=payload)
    if session_id and username:
        append_text_session_exchange(
            engine,
            username=username,
            session_id=session_id,
            question=normalized_question,
            filters=normalized_filters,
            payload=payload,
        )
    return payload


def validate_question(question: str) -> str:
    normalized = question.strip()
    if not normalized:
        raise AppError(ErrorCode.QUESTION_REQUIRED, "Question is required", status_code=status.HTTP_400_BAD_REQUEST)
    if len(normalized) > MAX_QUESTION_LENGTH:
        raise AppError(
            ErrorCode.QUESTION_TOO_LONG,
            "Question exceeds maximum length",
            status_code=status.HTTP_400_BAD_REQUEST,
            details={"max_length": MAX_QUESTION_LENGTH},
        )
    return normalized


def should_refuse_prompt(question: str) -> bool:
    lowered = question.lower()
    return any(pattern in lowered for pattern in INJECTION_PATTERNS)


def query_with_filters(question: str, filters: dict[str, str | None]) -> str:
    terms = [question]
    for key in QA_FILTER_KEYS:
        value = normalized_filter_value(filters.get(key))
        if value and value not in question:
            terms.insert(0, value)
    return " ".join(terms)


def normalized_filter_value(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def refused_answer(engine: Engine, *, question: str, trace_id: str) -> dict[str, object]:
    uncertainty = {"level": "high", "reasons": ["unsafe_prompt"]}
    payload = {
        "answer": "不能编造维修步骤、忽略引用或绕过安全要求。请基于可验证资料重新提问。",
        "sections": {
            "conclusion": "已拒绝不安全请求。",
            "citations": [],
            "uncertainty": uncertainty,
        },
        "citations": [],
        "trace_id": trace_id,
        "uncertainty": uncertainty,
        "safety_warnings": [],
    }
    record_qa(engine, question=question, payload=payload)
    return payload


def evidence_insufficient_answer(trace_id: str) -> dict[str, object]:
    uncertainty = {"level": "high", "reasons": ["evidence_insufficient"]}
    return {
        "answer": "未找到足够来源证据，无法给出确定性结论。",
        "sections": {
            "conclusion": "证据不足。",
            "citations": [],
            "uncertainty": uncertainty,
        },
        "citations": [],
        "trace_id": trace_id,
        "uncertainty": uncertainty,
        "safety_warnings": [],
    }


def build_citations(engine: Engine, evidence_items: list[dict[str, object]]) -> list[dict[str, object]]:
    applicable_items = [item for item in evidence_items if not item.get("not_applicable")]
    document_ids = {str(item["document_id"]) for item in applicable_items}
    with engine.connect() as connection:
        rows = connection.execute(select(documents).where(documents.c.id.in_(document_ids))).mappings().all()
    titles = {row["id"]: row["title"] for row in rows}
    citations = []
    for item in applicable_items:
        evidence_snippet = build_evidence_window(engine, item)
        citations.append(
            {
                "document_id": item["document_id"],
                "document_title": titles.get(str(item["document_id"]), "Unknown document"),
                "chunk_id": item["chunk_id"],
                "source_locator": item["source_locator"],
                "evidence_snippet": evidence_snippet,
                "evidence_type": item["chunk_type"],
                "accessible": True,
            }
        )
    return citations


def build_evidence_window(engine: Engine, evidence_item: dict[str, object]) -> str:
    chunk_type = str(evidence_item["chunk_type"])
    if chunk_type == ChunkType.TABLE.value:
        return table_evidence_window(evidence_item)
    if chunk_type == ChunkType.TEXT.value:
        return text_evidence_window(engine, evidence_item)
    return clipped_text(str(evidence_item["content"]))


def table_evidence_window(evidence_item: dict[str, object]) -> str:
    content = str(evidence_item["content"])
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return clipped_text(content)
    header = lines[0]
    data_lines = lines[1:3]
    return clipped_text("\n".join([header, *data_lines]))


def text_evidence_window(engine: Engine, evidence_item: dict[str, object]) -> str:
    source_locator = evidence_item.get("source_locator") or {}
    if not isinstance(source_locator, dict):
        return clipped_text(str(evidence_item["content"]))
    locator_type = source_locator.get("type")
    if locator_type not in {"text", "markdown", "docx"}:
        return clipped_text(str(evidence_item["content"]))

    chunk_id = str(evidence_item["chunk_id"])
    line_start = source_locator.get("line_start")
    line_end = source_locator.get("line_end")
    if not isinstance(line_start, int) or not isinstance(line_end, int):
        return clipped_text(str(evidence_item["content"]))

    with engine.connect() as connection:
        current_chunk = connection.execute(
            select(document_chunks).where(document_chunks.c.id == chunk_id)
        ).mappings().one_or_none()
        if current_chunk is None:
            return clipped_text(str(evidence_item["content"]))

        neighbor_rows = connection.execute(
            select(document_chunks)
            .where(document_chunks.c.document_id == current_chunk["document_id"])
            .where(document_chunks.c.chunk_type == ChunkType.TEXT.value)
            .where(
                or_(
                    document_chunks.c.id == chunk_id,
                    document_chunks.c.source_locator["line_end"].as_integer() == line_start - 1,
                    document_chunks.c.source_locator["line_start"].as_integer() == line_end + 1,
                )
            )
            .order_by(document_chunks.c.source_locator["line_start"].as_integer())
        ).mappings().all()

    if not neighbor_rows:
        return clipped_text(str(evidence_item["content"]))
    return clipped_text("\n".join(str(row["content"]) for row in neighbor_rows if row["content"]))


def clipped_text(content: str, *, limit: int = 360) -> str:
    return content[:limit]


def trim_retrieval_final_evidence(
    engine: Engine,
    *,
    trace_id: str,
    final_evidence: list[dict[str, object]],
) -> None:
    with engine.begin() as connection:
        connection.execute(
            update(retrieval_logs)
            .where(retrieval_logs.c.trace_id == trace_id)
            .values(final_evidence=final_evidence)
        )


def uncertainty_payload(scope_uncertain: bool, citations: list[dict[str, object]]) -> dict[str, object]:
    reasons = []
    document_ids = {citation["document_id"] for citation in citations}
    if scope_uncertain:
        reasons.append("scope_uncertain")
    if len(document_ids) > 1:
        reasons.append("multiple_sources")
    return {"level": "medium" if reasons else "low", "reasons": reasons}


def evidence_is_safety_sensitive(citations: list[dict[str, object]]) -> bool:
    safety_terms = ("液压", "hydraulic", "电气", "发动机", "制动", "rotating", "旋转")
    text = " ".join(str(citation["evidence_snippet"]).lower() for citation in citations)
    return any(term in text for term in safety_terms)


def conclusion_from_citation(citation: dict[str, object]) -> str:
    return f"根据来源证据，相关资料片段为：{citation['evidence_snippet']}"


def applicability_section(filters: dict[str, object], citations: list[dict[str, object]]) -> str:
    model = filters.get("model")
    if model:
        return f"适用范围优先限定为 {model}，以引用资料为准。"
    document_titles = sorted({str(citation["document_title"]) for citation in citations})
    return f"适用范围需结合来源资料确认：{', '.join(document_titles)}。"


def possible_causes_section(citations: list[dict[str, object]]) -> list[str]:
    return [str(citation["evidence_snippet"]) for citation in citations[:3]]


def inspection_steps_section(citations: list[dict[str, object]]) -> list[str]:
    return [f"核对引用 {index} 的来源定位和原文内容。" for index, _citation in enumerate(citations[:3], start=1)]


def citation_section(citations: list[dict[str, object]]) -> list[str]:
    return [
        f"{citation['document_title']} / {citation['chunk_id']}"
        for citation in citations
    ]


def compose_answer(sections: dict[str, object]) -> str:
    lines = [str(sections["conclusion"]), str(sections["applicability"])]
    safety_reminder = sections.get("safety_reminder") or []
    if safety_reminder:
        lines.extend(str(item) for item in safety_reminder)
    lines.append("以上结论仅基于当前检索到的来源证据。")
    return "\n".join(lines)


def record_qa(engine: Engine, *, question: str, payload: dict[str, object]) -> None:
    qa_record_id = str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            insert(qa_records).values(
                id=qa_record_id,
                trace_id=payload["trace_id"],
                question=question,
                answer=payload["answer"],
                sections=payload["sections"],
                uncertainty=payload["uncertainty"],
            )
        )
        for citation in payload["citations"]:
            connection.execute(
                insert(answer_citations).values(
                    id=str(uuid4()),
                    qa_record_id=qa_record_id,
                    document_id=citation["document_id"],
                    chunk_id=citation["chunk_id"],
                    citation_payload=citation,
                    accessible=citation["accessible"],
                )
            )


def register_text_qa_routes(app, *, settings: Settings, engine: Engine) -> None:
    @app.post("/qa/text", tags=["qa"])
    def text_qa(
        payload: TextQaRequest,
        request: Request,
        user: UserContext = Depends(require_roles(UserRole.ADMIN, UserRole.MAINTAINER, UserRole.USER, UserRole.EVALUATOR)),
    ) -> dict[str, object]:
        return answer_text_question(
            engine,
            question=payload.question,
            filters=payload.filters,
            trace_id=request.state.trace_id,
            settings=settings,
            username=user.username,
            session_id=payload.session_id,
        )
