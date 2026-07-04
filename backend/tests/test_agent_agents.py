from agromech_api.rag.agent.agents import AgentResult
from agromech_api.rag.agent.agents.answer_writer import AnswerWriterAgent
from agromech_api.rag.agent.agents.base import agent_trace
from agromech_api.rag.agent.agents.domain import DomainSpecialistAgent
from agromech_api.rag.agent.agents.evidence_reviewer import EvidenceReviewerAgent
from agromech_api.rag.agent.agents.planner import PlanningAgent
from agromech_api.rag.agent.agents.query_analyst import QueryAnalystAgent
from agromech_api.rag.agent.agents.query_rewrite import QueryRewriteAgent
from agromech_api.rag.agent.agents.retrieval import RetrievalAgent
from agromech_api.rag.agent.agents.router import RouterAgent
from agromech_api.rag.agent.agents.safety_reviewer import SafetyReviewerAgent
from agromech_api.rag.agent.state import initial_agent_state


def test_agent_trace_includes_agent_step_status_and_reason() -> None:
    trace = agent_trace(
        agent="RouterAgent",
        step="route",
        status="ok",
        reason="visual input is present",
        decision="text_visual",
    )

    assert trace == {
        "agent": "RouterAgent",
        "step": "route",
        "status": "ok",
        "reason": "visual input is present",
        "decision": "text_visual",
    }


def test_agent_result_shape_accepts_status_output_and_trace() -> None:
    result: AgentResult = {
        "status": "ok",
        "output": {"route": "text_only"},
        "trace": {"agent": "RouterAgent", "step": "route"},
    }

    assert result["status"] == "ok"
    assert result["output"] == {"route": "text_only"}
    assert result["trace"]["agent"] == "RouterAgent"


def test_query_analyst_agent_parses_question() -> None:
    agent = QueryAnalystAgent(lambda question, engine=None: {"parsed": question, "engine": engine})
    state = initial_agent_state(question="M7040 E01 怎么处理？", filters={})
    state["engine"] = "db"

    result = agent.run(state)

    assert result["status"] == "ok"
    assert result["output"]["parsed_query"] == {"parsed": "M7040 E01 怎么处理？", "engine": "db"}
    assert result["trace"]["agent"] == "QueryAnalystAgent"
    assert result["trace"]["step"] == "parse"


def test_router_agent_routes_with_existing_rule() -> None:
    state = initial_agent_state(question="这张图纸里液压泵在哪里？", filters={})

    result = RouterAgent().run(state)

    assert result["output"]["route"]["route"] == "text_visual"
    assert result["output"]["route"]["question_type"] == "visual_inspection"
    assert result["trace"]["agent"] == "RouterAgent"
    assert result["trace"]["decision"] == "text_visual"


def test_router_agent_classifies_maintenance_fault_parts_and_visual_questions() -> None:
    assert RouterAgent().run(initial_agent_state(question="M7040 液压油多久换一次？", filters={}))["output"]["route"]["question_type"] == "maintenance"
    assert RouterAgent().run(initial_agent_state(question="E01 故障码是什么原因？", filters={}))["output"]["route"]["question_type"] == "fault_diagnosis"
    assert RouterAgent().run(initial_agent_state(question="滤芯配件号是多少？", filters={}))["output"]["route"]["question_type"] == "parts"
    assert RouterAgent().run(initial_agent_state(question="图中故障灯代表什么？", filters={}))["output"]["route"]["question_type"] == "visual_inspection"


def test_retrieval_agent_wraps_existing_retrieval_tool_payload() -> None:
    calls: list[dict[str, object]] = []
    agent = RetrievalAgent(
        lambda **kwargs: calls.append(kwargs)
        or {
            "status": "ok",
            "final_evidence": [{"chunk_id": "chunk-1"}],
            "citations": [{"chunk_id": "chunk-1"}],
        }
    )
    state = initial_agent_state(question="M7040 E01", filters={"model": "M7040"})
    state["trace_id"] = "trace-1"
    state["route"] = {"route": "text_only"}

    result = agent.run(state)

    assert calls[0]["question"] == "M7040 E01"
    assert result["output"]["final_evidence"] == [{"chunk_id": "chunk-1"}]
    assert result["output"]["citations"] == [{"chunk_id": "chunk-1"}]
    assert result["trace"]["agent"] == "RetrievalAgent"


def test_planning_agent_uses_custom_planner_when_provided() -> None:
    agent = PlanningAgent(
        lambda **kwargs: {
            "evidence_sufficient": False,
            "need_visual": True,
            "need_query_rewrite": False,
            "next_action": "VISUAL_PAGE_RETRIEVAL",
            "missing_slots": ["page image"],
            "reason": kwargs["question"],
        }
    )
    state = initial_agent_state(question="图中液压泵在哪里？", filters={})
    state["retrieval"] = {"status": "ok"}
    state["final_evidence"] = []
    state["citations"] = []
    state["route"] = {"route": "text_visual"}

    result = agent.run(state)

    assert result["output"]["planner"]["need_visual"] is True
    assert result["trace"]["agent"] == "PlanningAgent"
    assert result["trace"]["decision"] == "VISUAL_PAGE_RETRIEVAL"


def test_query_rewrite_agent_rewrites_and_increments_round() -> None:
    state = initial_agent_state(question="液压泵异响怎么检查？", filters={"model": "M7040"})
    state["evidence_check"] = {"missing": ["citations"]}

    result = QueryRewriteAgent().run(state)

    assert "hydraulic pump" in result["output"]["rewritten_query"]
    assert result["output"]["retrieval_round"] == 1
    assert result["trace"]["agent"] == "QueryRewriteAgent"


def test_answer_writer_agent_calls_answer_function_when_evidence_passes() -> None:
    agent = AnswerWriterAgent(
        answer_fn=lambda **kwargs: {
            "answer": "ok",
            "citations": kwargs["retrieval"]["citations"],
            "trace_id": kwargs["trace_id"],
        }
    )
    state = initial_agent_state(question="M7040 E01", filters={})
    state["trace_id"] = "trace-1"
    state["retrieval"] = {"citations": [{"chunk_id": "chunk-1"}]}
    state["final_evidence"] = [{"chunk_id": "chunk-1"}]
    state["citations"] = [{"chunk_id": "chunk-1"}]
    state["planner"] = {"evidence_sufficient": True}
    state["evidence_check"] = {"status": "sufficient"}
    state["domain_context"] = {"question_type": "fault_diagnosis", "domain_agent": "FaultDiagnosisAgent"}

    result = agent.run(state)

    assert result["output"]["answer_payload"]["answer"] == "ok"
    assert result["trace"]["agent"] == "AnswerWriterAgent"
    assert result["trace"]["decision"] == "answered"


def test_domain_specialist_agent_selects_fault_agent_requirements() -> None:
    state = initial_agent_state(question="E01 故障码怎么排查？", filters={})
    state["route"] = {"route": "text_only", "question_type": "fault_diagnosis"}

    result = DomainSpecialistAgent().run(state)

    assert result["output"]["domain_context"]["domain_agent"] == "FaultDiagnosisAgent"
    assert "possible_causes" in result["output"]["domain_context"]["required_sections"]
    assert result["trace"]["agent"] == "FaultDiagnosisAgent"
    assert result["trace"]["step"] == "domain_strategy"


def test_evidence_reviewer_agent_marks_missing_citations_insufficient() -> None:
    state = initial_agent_state(question="液压泵异响怎么检查？", filters={})
    state["final_evidence"] = [{"chunk_id": "chunk-1"}]
    state["citations"] = []

    result = EvidenceReviewerAgent().run(state)

    assert result["output"]["evidence_check"]["status"] == "insufficient"
    assert result["output"]["evidence_check"]["missing"] == ["citation"]
    assert result["trace"]["agent"] == "EvidenceReviewerAgent"
    assert result["trace"]["step"] == "evidence_review"
    assert result["trace"]["decision"] == "insufficient"


def test_safety_reviewer_agent_adds_warning_for_high_risk_answer() -> None:
    state = initial_agent_state(question="液压泵异响怎么检查？", filters={})
    state["answer_payload"] = {
        "answer": "检查液压泵。",
        "sections": {},
        "citations": [{"chunk_id": "chunk-1"}],
        "trace_id": "trace-1",
        "uncertainty": {"level": "low", "reasons": []},
        "safety_warnings": [],
        "agent_trace": [],
    }

    result = SafetyReviewerAgent().run(state)

    payload = result["output"]["answer_payload"]
    assert payload["safety_warnings"] == [SafetyReviewerAgent.DEFAULT_SAFETY_WARNING]
    assert payload["sections"]["safety_reminder"] == [SafetyReviewerAgent.DEFAULT_SAFETY_WARNING]
    assert result["trace"]["agent"] == "SafetyReviewerAgent"
    assert result["trace"]["decision"] == "warning_added"
