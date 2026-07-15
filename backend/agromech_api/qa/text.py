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
from agromech_api.rag.retrieval.filters import RetrievalFilters, build_retrieval_filters
from agromech_api.rag.retrieval.hybrid import (
    RetrievalTraceConflictError,
    hybrid_retrieve_with_trace,
)
from agromech_api.rag.retrieval.indexing import (
    enforce_visual_page_filters,
    visual_page_search,
)
from agromech_api.rag.retrieval.query_rewrite import (
    build_query_rewrite_provider,
    rewrite_query,
)
from agromech_api.rag.retrieval.query_understanding import parse_query
from agromech_api.rag.traces import record_citation_trace
from agromech_api.integrations.embeddings.text import build_embedding_provider
from agromech_api.integrations.embeddings.visual import build_visual_embedding_provider
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
    try:
        payload = controller.answer_text(
            engine=engine,
            question=normalized_question,
            trace_id=trace_id,
            filters=normalized_filters,
            image_context=image_context,
        )
    except RetrievalTraceConflictError as exc:
        raise AppError(
            ErrorCode.TRACE_ID_CONFLICT,
            "Trace ID is already in use",
            status_code=status.HTTP_409_CONFLICT,
        ) from exc
    record_citation_trace(engine, trace_id, list(payload.get("citations") or []))
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
    rewrite_provider = build_query_rewrite_provider(settings)
    return AgentController(
        parse_query_fn=lambda question, engine=None: parse_query(question, engine=engine),
        rewrite_fn=lambda **kwargs: rewrite_for_text_agent(
            settings=settings,
            provider=rewrite_provider,
            **kwargs,
        ),
        retrieve_fn=lambda **kwargs: retrieve_for_text_agent(
            settings=settings, viewer_user_id=viewer_user_id, **kwargs
        ),
        planner_fn=lambda **kwargs: planner_for_text_agent(settings=settings, **kwargs),
        visual_retrieve_fn=lambda **kwargs: retrieve_visual_for_text_agent(
            settings=settings, viewer_user_id=viewer_user_id, **kwargs
        ),
        answer_fn=lambda **kwargs: answer_for_text_agent(
            settings=settings,
            viewer_user_id=viewer_user_id,
            **kwargs,
        ),
        multimodal_answer_fn=lambda **kwargs: answer_for_text_agent(
            settings=settings,
            viewer_user_id=viewer_user_id,
            **kwargs,
        ),
    )


def rewrite_for_text_agent(
    *,
    settings: Settings,
    provider,
    question: str,
    parsed_query,
    filters: dict[str, str | None],
    supplemental: bool,
    **_kwargs,
) -> dict[str, object]:
    _ = settings
    result = rewrite_query(
        question=question,
        parsed=parsed_query,
        request_filters=filters,
        provider=provider,
        supplemental=supplemental,
    )
    return {"query": result.query, "trace": result.to_trace()}


def retrieve_for_text_agent(
    *,
    settings: Settings,
    engine: Engine,
    question: str,
    original_question: str,
    filters: dict[str, str | None],
    query_rewrite: dict[str, object],
    retrieval_round: int,
    trace_id: str,
    viewer_user_id: str | None = None,
    **_kwargs,
) -> dict[str, object]:
    retrieval_filters = build_retrieval_filters(
        request_filters=filters,
        viewer_user_id=viewer_user_id,
    )
    rerank_provider = None
    # Graph RAG is currently out of scope for the main QA path, even when
    # experimental graph settings are present.
    if settings.rerank_enabled:
        from agromech_api.rag.retrieval.rerank import build_rerank_provider

        rerank_provider = build_rerank_provider(settings)
    retrieval = hybrid_retrieve_with_trace(
        engine,
        query_with_filters(question, filters),
        trace_id=trace_id,
        logged_query=original_question,
        filters=retrieval_filters,
        query_rewrite=query_rewrite,
        retrieval_round=retrieval_round,
        embedding_provider=build_embedding_provider(settings),
        rerank_provider=rerank_provider,
        rerank_top_k=settings.rerank_top_k,
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
    retrieval_filters = build_retrieval_filters(
        request_filters=filters,
        viewer_user_id=viewer_user_id,
    )
    embedding_provider = build_visual_embedding_provider(settings)
    retrieval = visual_page_search(
        engine,
        query,
        filters=retrieval_filters,
        embedding_provider=embedding_provider,
        active_embedding_version=settings.visual_embedding_version,
    )
    final_evidence = enforce_visual_page_filters(
        engine,
        retrieval,
        filters=retrieval_filters,
    )[: settings.final_evidence_limit]
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
    _ = settings, engine, filters
    check = check_evidence_sufficiency(
        question=question,
        final_evidence=final_evidence,
        citations=citations,
    )
    uploaded_visual_available = bool(
        image_context
        and (
            image_context.get("ocr_text")
            or image_context.get("description")
            or image_context.get("detected_entities")
        )
    )
    need_visual = not uploaded_visual_available and (
        route.get("route") == "text_visual" or any(
            marker in question for marker in ("图", "页面", "位置", "示意")
        )
    )
    return {
        "evidence_sufficient": check["status"] == "sufficient" and not need_visual,
        "need_visual": need_visual,
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
    viewer_user_id: str | None = None,
    **_kwargs,
) -> dict[str, object]:
    retrieval_filters = build_retrieval_filters(
        request_filters=filters,
        viewer_user_id=viewer_user_id,
    )
    final_evidence = filter_merged_visual_evidence(
        engine,
        [item for item in final_evidence if not item.get("not_applicable")],
        filters=retrieval_filters,
    )
    require_visual = bool((planner or {}).get("need_visual"))
    final_evidence = select_final_evidence(
        final_evidence,
        limit=settings.final_evidence_limit,
        require_visual=require_visual,
    )
    if require_visual and not any(item.get("asset_id") for item in final_evidence):
        final_evidence = []
    retrieval["final_evidence"] = final_evidence
    trim_retrieval_final_evidence(engine, trace_id=trace_id, final_evidence=final_evidence)
    if not final_evidence:
        return evidence_insufficient_answer(trace_id)

    text_citations = {
        str(item["chunk_id"]): item
        for item in build_citations(
            engine,
            [item for item in final_evidence if item.get("chunk_id")],
        )
    }
    visual_citations = {
        str(item["asset_id"]): item
        for item in build_visual_citations(
            engine,
            [item for item in final_evidence if item.get("asset_id")],
        )
    }
    citations = []
    for evidence in final_evidence:
        citation = (
            text_citations.get(str(evidence["chunk_id"]))
            if evidence.get("chunk_id")
            else visual_citations.get(str(evidence.get("asset_id")))
        )
        if citation is not None:
            citations.append(citation)
    if not citations or len(citations) != len(final_evidence):
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


def filter_merged_visual_evidence(
    engine: Engine,
    evidence: list[dict[str, object]],
    *,
    filters: RetrievalFilters,
) -> list[dict[str, object]]:
    visual_evidence = [item for item in evidence if item.get("asset_id")]
    allowed_visual = {
        (str(item["asset_id"]), str(item["document_id"]))
        for item in enforce_visual_page_filters(
            engine,
            visual_evidence,
            filters=filters,
        )
    }
    return [
        item
        for item in evidence
        if not item.get("asset_id")
        or (str(item.get("asset_id")), str(item.get("document_id")))
        in allowed_visual
    ]


def select_final_evidence(
    evidence: list[dict[str, object]],
    *,
    limit: int,
    require_visual: bool,
) -> list[dict[str, object]]:
    selected = evidence[:limit]
    if not require_visual or any(item.get("asset_id") for item in selected):
        return selected
    first_visual = next((item for item in evidence if item.get("asset_id")), None)
    if first_visual is None or limit <= 0:
        return selected
    return [*selected[: limit - 1], first_visual]


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
