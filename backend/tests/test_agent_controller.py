from agromech_api.rag.agent.controller import AgentController
from agromech_api.rag.retrieval.query_understanding import parse_query


def passthrough_rewrite(**kwargs):
    return {
        "query": kwargs["question"],
        "trace": {"provider": "test", "fallback": kwargs["supplemental"]},
    }


def test_agent_controller_rewrites_before_first_retrieval() -> None:
    calls: list[str] = []
    controller = AgentController(
        parse_query_fn=lambda question, engine=None: calls.append("parse") or parse_query(question),
        rewrite_fn=lambda **kwargs: calls.append("rewrite")
        or {
            "query": "M7040 E01 hydraulic pump",
            "trace": {"provider": "test", "fallback": False},
        },
        retrieve_fn=lambda **kwargs: calls.append(f"retrieve:{kwargs['question']}")
        or {
            "status": "ok",
            "final_evidence": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
            "citations": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
        },
        answer_fn=lambda **kwargs: calls.append("answer")
        or {"answer": "ok", "citations": [], "trace_id": kwargs["trace_id"]},
    )

    controller.answer_text(engine=None, question="M7040 E01 怎么修？", trace_id="trace-1", filters={})

    assert calls[:3] == ["parse", "rewrite", "retrieve:M7040 E01 hydraulic pump"]


def test_agent_controller_uses_llm_once_then_rule_supplemental_rewrite() -> None:
    rewrite_modes: list[bool] = []
    retrieval_calls = 0

    def rewrite_fn(**kwargs):
        rewrite_modes.append(kwargs["supplemental"])
        return {
            "query": "first" if not kwargs["supplemental"] else "fallback",
            "trace": {"fallback": kwargs["supplemental"]},
        }

    def retrieve_fn(**_kwargs):
        nonlocal retrieval_calls
        retrieval_calls += 1
        return {"status": "evidence_insufficient", "final_evidence": [], "citations": []}

    controller = AgentController(
        parse_query_fn=lambda question, engine=None: parse_query(question),
        rewrite_fn=rewrite_fn,
        retrieve_fn=retrieve_fn,
        answer_fn=lambda **_kwargs: {"answer": "must not run"},
    )
    payload = controller.answer_text(engine=None, question="液压泵异响", trace_id="trace-2", filters={})

    assert rewrite_modes == [False, True]
    assert retrieval_calls == 2
    assert payload["citations"] == []


def test_agent_controller_uses_rewritten_query_only_for_retrieval() -> None:
    seen: dict[str, str] = {}

    controller = AgentController(
        parse_query_fn=lambda question, engine=None: parse_query(question),
        rewrite_fn=lambda **_kwargs: {
            "query": "M7040 E01 hydraulic pump",
            "trace": {"provider": "test", "fallback": False},
        },
        retrieve_fn=lambda **kwargs: seen.setdefault("retrieve", kwargs["question"])
        and {
            "status": "ok",
            "final_evidence": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
            "citations": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
        },
        planner_fn=lambda **kwargs: seen.setdefault("planner", kwargs["question"])
        and {
            "evidence_sufficient": True,
            "need_visual": False,
            "need_query_rewrite": False,
            "next_action": "ANSWER",
            "missing_slots": [],
            "reason": "enough evidence",
        },
        answer_fn=lambda **kwargs: seen.setdefault("answer", kwargs["question"])
        and {"answer": "ok", "citations": [], "trace_id": kwargs["trace_id"]},
    )

    controller.answer_text(
        engine=None,
        question="M7040 的 E01 怎么修？",
        trace_id="trace-original-question",
        filters={},
    )

    assert seen == {
        "retrieve": "M7040 E01 hydraulic pump",
        "planner": "M7040 的 E01 怎么修？",
        "answer": "M7040 的 E01 怎么修？",
    }


def test_agent_controller_runs_text_only_minimum_loop() -> None:
    calls: list[str] = []
    controller = AgentController(
        parse_query_fn=lambda question, engine=None: calls.append("parse") or object(),
        rewrite_fn=lambda **kwargs: calls.append("rewrite") or passthrough_rewrite(**kwargs),
        retrieve_fn=lambda **kwargs: calls.append("retrieve")
        or {
            "status": "ok",
            "final_evidence": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
            "citations": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
        },
        answer_fn=lambda **kwargs: calls.append("answer")
        or {"answer": "ok", "citations": [], "trace_id": kwargs["trace_id"]},
    )

    payload = controller.answer_text(
        engine=None,
        question="MG2004 液压油多久换一次？",
        trace_id="trace-1",
        filters={},
    )

    assert payload["answer"] == "ok"
    assert calls == ["parse", "rewrite", "retrieve", "answer"]
    assert payload["agent_trace"][0]["step"] == "route"
    trace_agents = [entry.get("agent") for entry in payload["agent_trace"]]
    assert "QueryAnalystAgent" in trace_agents
    assert "RouterAgent" in trace_agents
    assert "RetrievalAgent" in trace_agents
    assert "PlanningAgent" in trace_agents
    assert "EvidenceReviewerAgent" in trace_agents
    assert any(agent in trace_agents for agent in ["MaintenanceAgent", "FaultDiagnosisAgent", "PartsAgent", "VisualInspectionAgent"])
    assert "AnswerWriterAgent" in trace_agents
    assert "SafetyReviewerAgent" in trace_agents


def test_agent_controller_passes_domain_context_to_answer_function() -> None:
    seen_domain_contexts: list[dict[str, object]] = []
    controller = AgentController(
        parse_query_fn=lambda question, engine=None: object(),
        rewrite_fn=passthrough_rewrite,
        retrieve_fn=lambda **_kwargs: {
            "status": "ok",
            "final_evidence": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
            "citations": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
        },
        answer_fn=lambda **kwargs: seen_domain_contexts.append(kwargs["domain_context"])
        or {"answer": "ok", "citations": [], "trace_id": kwargs["trace_id"], "sections": {}},
    )

    payload = controller.answer_text(
        engine=None,
        question="滤芯配件号是多少？",
        trace_id="trace-domain",
        filters={},
    )

    assert payload["answer"] == "ok"
    assert seen_domain_contexts[0]["question_type"] == "parts"
    assert seen_domain_contexts[0]["domain_agent"] == "PartsAgent"


def test_agent_controller_rewrites_and_retries_when_evidence_is_insufficient() -> None:
    calls: list[str] = []

    def retrieve_fn(**kwargs):
        calls.append(str(kwargs["question"]))
        if len(calls) == 1:
            return {
                "status": "ok",
                "final_evidence": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
                "citations": [],
            }
        return {
            "status": "ok",
            "final_evidence": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
            "citations": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
        }

    controller = AgentController(
        parse_query_fn=lambda question, engine=None: object(),
        rewrite_fn=lambda **kwargs: {
            "query": f"{kwargs['question']} hydraulic pump",
            "trace": {"provider": "test", "fallback": kwargs["supplemental"]},
        },
        retrieve_fn=retrieve_fn,
        answer_fn=lambda **kwargs: {
            "answer": "ok",
            "citations": [{"chunk_id": "chunk-1"}],
            "trace_id": kwargs["trace_id"],
        },
    )

    payload = controller.answer_text(
        engine=None,
        question="液压泵异响怎么检查？",
        trace_id="trace-1",
        filters={"model": "M7040"},
    )

    assert payload["answer"] == "ok"
    assert len(calls) == 2
    assert "hydraulic pump" in calls[1]
    assert any(entry["step"] == "rewrite" for entry in payload["agent_trace"])


def test_agent_controller_calls_visual_retrieval_when_planner_requests_visual_evidence() -> None:
    visual_calls: list[str] = []

    def planner_fn(**_kwargs):
        return {
            "evidence_sufficient": False,
            "need_visual": True,
            "need_query_rewrite": False,
            "next_action": "VISUAL_PAGE_RETRIEVAL",
            "missing_slots": ["图示位置"],
            "reason": "文本证据缺少页面图示。",
        }

    controller = AgentController(
        parse_query_fn=lambda question, engine=None: object(),
        rewrite_fn=passthrough_rewrite,
        retrieve_fn=lambda **_kwargs: {
            "status": "ok",
            "final_evidence": [{"chunk_id": "chunk-1", "document_id": "doc-1", "evidence_type": "text"}],
            "citations": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
        },
        visual_retrieve_fn=lambda **kwargs: visual_calls.append(kwargs["question"])
        or {
            "status": "ok",
            "final_evidence": [
                {
                    "asset_id": "asset-page-1",
                    "document_id": "doc-1",
                    "page_number": 3,
                    "evidence_type": "visual_page",
                }
            ],
            "citations": [
                {
                    "asset_id": "asset-page-1",
                    "document_id": "doc-1",
                    "page_number": 3,
                    "evidence_type": "visual_page",
                }
            ],
        },
        planner_fn=planner_fn,
        answer_fn=lambda **kwargs: {
            "answer": "ok",
            "citations": kwargs["retrieval"]["citations"],
            "trace_id": kwargs["trace_id"],
        },
    )

    payload = controller.answer_text(
        engine=None,
        question="液压泵在图中哪里？",
        trace_id="trace-visual",
        filters={},
    )

    assert visual_calls == ["液压泵在图中哪里？"]
    assert any(citation.get("evidence_type") == "visual_page" for citation in payload["citations"])
    assert any(entry["step"] == "planner" for entry in payload["agent_trace"])
    assert any(entry["step"] == "visual_retrieve" for entry in payload["agent_trace"])


def test_agent_controller_rejects_text_only_evidence_when_visual_is_required() -> None:
    visual_calls: list[str] = []
    answer_calls: list[str] = []
    controller = AgentController(
        parse_query_fn=lambda question, engine=None: object(),
        rewrite_fn=passthrough_rewrite,
        retrieve_fn=lambda **_kwargs: {
            "status": "ok",
            "final_evidence": [
                {
                    "chunk_id": "chunk-1",
                    "document_id": "doc-1",
                    "evidence_type": "text",
                }
            ],
            "citations": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
        },
        visual_retrieve_fn=lambda **kwargs: visual_calls.append(kwargs["question"])
        or {"status": "ok", "final_evidence": [], "citations": []},
        planner_fn=lambda **_kwargs: {
            "evidence_sufficient": False,
            "need_visual": True,
            "need_query_rewrite": False,
            "next_action": "VISUAL_PAGE_RETRIEVAL",
            "missing_slots": ["visual_evidence"],
            "reason": "visual evidence is required",
        },
        answer_fn=lambda **_kwargs: answer_calls.append("answer")
        or {"answer": "must not run"},
    )

    payload = controller.answer_text(
        engine=None,
        question="液压泵在图中哪里？",
        trace_id="trace-visual-empty",
        filters={},
    )

    assert visual_calls == ["液压泵在图中哪里？", "液压泵在图中哪里？"]
    assert answer_calls == []
    assert payload["citations"] == []
    assert payload["uncertainty"] == {
        "level": "high",
        "reasons": ["evidence_insufficient"],
    }


def test_agent_controller_uses_multimodal_answer_when_visual_evidence_is_present() -> None:
    answer_modes: list[str] = []

    controller = AgentController(
        parse_query_fn=lambda question, engine=None: object(),
        rewrite_fn=passthrough_rewrite,
        retrieve_fn=lambda **_kwargs: {
            "status": "ok",
            "final_evidence": [{"chunk_id": "chunk-1", "document_id": "doc-1", "evidence_type": "text"}],
            "citations": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
        },
        visual_retrieve_fn=lambda **_kwargs: {
            "status": "ok",
            "final_evidence": [{"asset_id": "asset-page-1", "document_id": "doc-1", "evidence_type": "visual_page"}],
            "citations": [{"asset_id": "asset-page-1", "document_id": "doc-1", "evidence_type": "visual_page"}],
        },
        planner_fn=lambda **_kwargs: {
            "evidence_sufficient": False,
            "need_visual": True,
            "need_query_rewrite": False,
            "next_action": "VISUAL_PAGE_RETRIEVAL",
            "missing_slots": [],
            "reason": "needs page image",
        },
        answer_fn=lambda **kwargs: answer_modes.append("text") or {"answer": "text"},
        multimodal_answer_fn=lambda **kwargs: answer_modes.append("visual")
        or {"answer": "visual", "citations": kwargs["retrieval"]["citations"], "trace_id": kwargs["trace_id"]},
    )

    payload = controller.answer_text(
        engine=None,
        question="图中液压泵位置在哪里？",
        trace_id="trace-mm",
        filters={},
    )

    assert payload["answer"] == "visual"
    assert answer_modes == ["visual"]


def test_agent_controller_generation_guard_skips_answer_when_evidence_remains_insufficient() -> None:
    calls: list[str] = []
    controller = AgentController(
        parse_query_fn=lambda question, engine=None: object(),
        rewrite_fn=passthrough_rewrite,
        retrieve_fn=lambda **kwargs: {
            "status": "ok",
            "final_evidence": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
            "citations": [],
        },
        answer_fn=lambda **kwargs: calls.append("answer") or {"answer": "should not happen"},
    )

    payload = controller.answer_text(
        engine=None,
        question="液压泵异响怎么检查？",
        trace_id="trace-guard",
        filters={},
    )

    assert calls == []
    assert payload["answer"] == "未找到足够来源证据，无法给出确定性结论。"
    assert payload["citations"] == []
    assert any(
        entry["step"] == "generation_guard" and entry["decision"] == "insufficient"
        for entry in payload["agent_trace"]
    )
    assert payload["agent_trace"][-1]["agent"] == "SafetyReviewerAgent"
