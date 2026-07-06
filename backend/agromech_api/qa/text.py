from __future__ import annotations

from fastapi import status
from sqlalchemy import Engine

from agromech_api.core.config import Settings, get_settings
from agromech_api.core.errors import AppError, ErrorCode
from agromech_api.rag.agent.controller import AgentController
from agromech_api.rag.generation.answer import AnswerGenerationError, build_answer_generator
from agromech_api.rag.langchain.adapters import (
    build_answer_chain,
)
from agromech_api.rag.retrieval.evidence_check import check_evidence_sufficiency
from agromech_api.rag.retrieval.hybrid import hybrid_retrieve_with_trace
from agromech_api.rag.retrieval.indexing import visual_page_search
from agromech_api.rag.retrieval.query_understanding import parse_query
from agromech_api.sessions.history import append_text_session_exchange, ensure_session_belongs_to_user
from agromech_api.qa.text_citations import (
    build_citations,
    build_evidence_window,
    build_visual_citations,
    clipped_text,
    table_evidence_window,
    text_evidence_window,
    trim_retrieval_final_evidence,
)
from agromech_api.qa.text_records import record_qa
from agromech_api.qa.text_response import (
    applicability_section,
    citation_section,
    compose_answer,
    conclusion_from_citation,
    evidence_is_safety_sensitive,
    inspection_steps_section,
    possible_causes_section,
    uncertainty_payload,
)


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


def answer_text_question(
    engine: Engine,
    *,
    question: str,
    trace_id: str,
    filters: dict[str, str | None] | None = None,
    settings: Settings | None = None,
    username: str | None = None,
    viewer_user_id: str | None = None,
    session_id: str | None = None,
    image_context: dict[str, object] | None = None,
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

    settings = settings or get_settings()
    controller = build_text_agent_controller(settings, viewer_user_id=viewer_user_id)
    payload = controller.answer_text(
        engine=engine,
        question=normalized_question,
        trace_id=trace_id,
        filters=normalized_filters,
        image_context=image_context,
    )
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


def build_text_agent_controller(
    settings: Settings, *, viewer_user_id: str | None = None
) -> AgentController:
    return AgentController(
        parse_query_fn=lambda question, engine=None: parse_query(
            query_with_filters(question, {}),
            engine=engine,
        ),
        retrieve_fn=lambda **kwargs: retrieve_for_text_agent(
            settings=settings, viewer_user_id=viewer_user_id, **kwargs
        ),
        planner_fn=lambda **kwargs: planner_for_text_agent(settings=settings, **kwargs),
        visual_retrieve_fn=lambda **kwargs: retrieve_visual_for_text_agent(
            settings=settings, viewer_user_id=viewer_user_id, **kwargs
        ),
        answer_fn=lambda **kwargs: answer_for_text_agent(settings=settings, **kwargs),
        multimodal_answer_fn=lambda **kwargs: answer_for_text_agent(settings=settings, **kwargs),
    )


def retrieve_for_text_agent(
    *,
    settings: Settings,
    engine: Engine,
    question: str,
    filters: dict[str, str | None],
    trace_id: str,
    viewer_user_id: str | None = None,
    **_kwargs,
) -> dict[str, object]:
    search_query = query_with_filters(question, filters)
    vector_store = None
    embedding_provider = None
    vector_collection = None
    graph_service = None
    rerank_provider = None
    # Graph RAG is currently out of scope for the main QA path, even when
    # experimental graph settings are present.
    if settings.rerank_enabled:
        from agromech_api.rag.retrieval.rerank import build_rerank_provider

        rerank_provider = build_rerank_provider(settings)
    retrieval = hybrid_retrieve_with_trace(
        engine,
        search_query,
        trace_id=trace_id,
        logged_query=question,
        filters={key: value for key, value in filters.items() if value is not None},
        vector_store=vector_store,
        vector_collection=vector_collection,
        embedding_provider=embedding_provider,
        graph_service=graph_service,
        rerank_provider=rerank_provider,
        rerank_top_k=settings.rerank_top_k,
        viewer_user_id=viewer_user_id,
        settings=settings,
    )
    if retrieval.get("status") == "ok":
        final_evidence = list(retrieval.get("final_evidence", []))[: settings.final_evidence_limit]
        retrieval["final_evidence"] = final_evidence
        retrieval["citations"] = build_citations(engine, final_evidence)
    return retrieval


def retrieve_visual_for_text_agent(
    *,
    settings: Settings,
    engine: Engine,
    question: str,
    filters: dict[str, str | None],
    trace_id: str,
    viewer_user_id: str | None = None,
    **_kwargs,
) -> dict[str, object]:
    _ = trace_id
    query = query_with_filters(question, filters)
    embedding_provider = None
    vector_store = None
    vector_collection = None
    retrieval = visual_page_search(
        engine,
        query,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        collection=vector_collection,
        active_embedding_version=settings.visual_embedding_version,
        viewer_user_id=viewer_user_id,
    )
    final_evidence = retrieval[: settings.final_evidence_limit]
    return {
        "status": "ok",
        "trace_id": trace_id,
        "final_evidence": final_evidence,
        "citations": build_visual_citations(engine, final_evidence),
    }


def planner_for_text_agent(
    *,
    settings: Settings,
    engine: Engine,
    question: str,
    filters: dict[str, str | None],
    route: dict[str, object],
    retrieval: dict[str, object],
    final_evidence: list[dict[str, object]],
    citations: list[dict[str, object]],
    image_context: dict[str, object] | None = None,
    **_kwargs,
) -> dict[str, object]:
    _ = settings, engine, filters, route, image_context
    check = check_evidence_sufficiency(
        question=question,
        final_evidence=final_evidence,
        citations=citations,
    )
    need_visual = route.get("route") == "text_visual" or any(
        marker in question for marker in ("图", "页面", "位置", "示意")
    )
    return {
        "evidence_sufficient": check["status"] == "sufficient" and not need_visual,
        "need_visual": need_visual and check["status"] != "sufficient",
        "need_query_rewrite": check["status"] != "sufficient",
        "next_action": "VISUAL_PAGE_RETRIEVAL" if need_visual else ("QUERY_REWRITE" if check["status"] != "sufficient" else "ANSWER"),
        "missing_slots": check["missing"],
        "reason": check["reason"],
    }


def answer_for_text_agent(
    *,
    settings: Settings,
    engine: Engine,
    question: str,
    trace_id: str,
    filters: dict[str, str | None],
    retrieval: dict[str, object],
    final_evidence: list[dict[str, object]],
    planner: dict[str, object] | None = None,
    domain_context: dict[str, object] | None = None,
    **_kwargs,
) -> dict[str, object]:
    if retrieval["status"] == "evidence_insufficient":
        return evidence_insufficient_answer(trace_id)

    final_evidence = final_evidence[: settings.final_evidence_limit]
    retrieval["final_evidence"] = final_evidence
    trim_retrieval_final_evidence(engine, trace_id=trace_id, final_evidence=final_evidence)
    citations = build_citations(engine, final_evidence)
    if not citations:
        return evidence_insufficient_answer(trace_id)

    search_query = query_with_filters(question, filters)
    parsed = parse_query(search_query, engine=engine)
    safety_warnings = [SAFETY_WARNING] if parsed.safety_sensitive or evidence_is_safety_sensitive(citations) else []
    uncertainty = uncertainty_payload(parsed.scope_uncertain, citations)
    visual_evidence_present = any(item.get("evidence_type") == "visual_page" for item in final_evidence)
    if visual_evidence_present:
        answer_generator = build_answer_generator(settings, model_override=settings.visual_answer_model)
    else:
        answer_generator = build_answer_generator(settings)
    if answer_generator is not None:
        try:
            answer_chain = build_answer_chain(answer_generator)
            generated = answer_chain.invoke(
                {
                    "question": question,
                    "citations": citations,
                    "safety_warnings": safety_warnings,
                    "uncertainty": uncertainty,
                    "filters": parsed.filters,
                }
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
    apply_domain_strategy(sections, domain_context or {})
    payload = {
        "answer": answer,
        "sections": sections,
        "citations": citations,
        "trace_id": trace_id,
        "uncertainty": uncertainty,
        "safety_warnings": safety_warnings,
    }
    return payload


def apply_domain_strategy(sections: dict[str, object], domain_context: dict[str, object]) -> None:
    if not domain_context:
        return
    sections["domain_strategy"] = {
        "question_type": domain_context.get("question_type"),
        "domain_agent": domain_context.get("domain_agent"),
        "required_sections": domain_context.get("required_sections", []),
        "answer_focus": domain_context.get("answer_focus"),
    }
    for section_name in domain_context.get("required_sections", []):
        sections.setdefault(str(section_name), [])


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
