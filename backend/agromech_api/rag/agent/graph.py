from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agromech_api.rag.agent.agents import (
    AnswerWriterAgent,
    DomainSpecialistAgent,
    EvidenceReviewerAgent,
    PlanningAgent,
    QueryAnalystAgent,
    QueryRewriteAgent,
    RetrievalAgent,
    RouterAgent,
    SafetyReviewerAgent,
)
from agromech_api.rag.agent.state import AgentState, append_agent_trace
from agromech_api.rag.langchain.adapters import AgroMechVisualPageRetriever


MAX_RETRIEVAL_ROUNDS = 2


def build_agent_graph(
    *,
    parse_query_fn: Callable[..., Any],
    rewrite_fn: Callable[..., dict[str, object]],
    retrieve_fn: Callable[..., dict[str, Any]],
    planner_fn: Callable[..., dict[str, Any]] | None = None,
    visual_retrieve_fn: Callable[..., dict[str, Any]] | None = None,
    answer_fn: Callable[..., dict[str, Any]],
    multimodal_answer_fn: Callable[..., dict[str, Any]] | None = None,
):
    from langgraph.graph import END, StateGraph

    query_analyst_agent = QueryAnalystAgent(parse_query_fn)
    router_agent = RouterAgent()
    retrieval_agent = RetrievalAgent(retrieve_fn)
    planning_agent = PlanningAgent(planner_fn)
    evidence_reviewer_agent = EvidenceReviewerAgent()
    domain_specialist_agent = DomainSpecialistAgent()
    query_rewrite_agent = QueryRewriteAgent(rewrite_fn)
    answer_writer_agent = AnswerWriterAgent(
        answer_fn=answer_fn,
        multimodal_answer_fn=multimodal_answer_fn,
    )
    safety_reviewer_agent = SafetyReviewerAgent()

    def parse_node(state: AgentState) -> AgentState:
        result = query_analyst_agent.run(state)
        return {
            **state,
            **result["output"],
            "pending_agent_traces": [*state.get("pending_agent_traces", []), result["trace"]],
        }

    def route_node(state: AgentState) -> AgentState:
        result = router_agent.run(state)
        updated = {**state, **result["output"]}
        traced = append_agent_trace(updated, **result["trace"])
        for pending_trace in state.get("pending_agent_traces", []):
            traced = append_agent_trace(traced, **pending_trace)
        return {**traced, "pending_agent_traces": []}

    def retrieve_node(state: AgentState) -> AgentState:
        result = retrieval_agent.run(state)
        return append_agent_trace({**state, **result["output"]}, **result["trace"])

    def planner_node(state: AgentState) -> AgentState:
        result = planning_agent.run(state)
        planner = result["output"]["planner"]
        updated = {**state, **result["output"]}
        if planner.get("need_visual") and visual_retrieve_fn is not None:
            retriever = AgroMechVisualPageRetriever(
                engine=state.get("engine"),
                retrieve_payload_fn=visual_retrieve_fn,
                filters=state.get("filters") or {},
                trace_id=state.get("trace_id"),
                route=state.get("route") or {},
                image_context=state.get("image_context"),
                planner=planner,
            )
            retrieval = retriever.retrieve_payload(state.get("rewritten_query") or state["question"])
            updated = {
                **updated,
                "visual_retrieval": retrieval,
                "final_evidence": [*(state.get("final_evidence") or []), *(retrieval.get("final_evidence") or [])],
                "citations": [*(state.get("citations") or []), *(retrieval.get("citations") or [])],
                "retrieval": {
                    **(state.get("retrieval") or {}),
                    "visual_retrieval": retrieval,
                    "final_evidence": [*(state.get("final_evidence") or []), *(retrieval.get("final_evidence") or [])],
                    "citations": [*(state.get("citations") or []), *(retrieval.get("citations") or [])],
                },
            }
            updated = append_agent_trace(
                updated,
                agent="VisualRetrievalAgent",
                step="visual_retrieve",
                status=str(retrieval.get("status") or "ok"),
                decision=retrieval.get("status"),
                reason="visual evidence requested",
            )
        return append_agent_trace(updated, **result["trace"])

    def evidence_check_node(state: AgentState) -> AgentState:
        result = evidence_reviewer_agent.run(state)
        return append_agent_trace({**state, **result["output"]}, **result["trace"])

    def rewrite_node(state: AgentState) -> AgentState:
        result = query_rewrite_agent.run(state)
        return append_agent_trace({**state, **result["output"]}, **result["trace"])

    def domain_node(state: AgentState) -> AgentState:
        result = domain_specialist_agent.run(state)
        return append_agent_trace({**state, **result["output"]}, **result["trace"])

    def after_evidence_check(state: AgentState) -> str:
        check = state.get("evidence_check") or {}
        if check.get("status") == "sufficient":
            return "domain"
        if int(state.get("retrieval_round", 0)) < MAX_RETRIEVAL_ROUNDS:
            return "rewrite"
        return "domain"

    def answer_node(state: AgentState) -> AgentState:
        answer_result = answer_writer_agent.run(state)
        answer_payload = answer_result["output"]["answer_payload"]
        reviewed_state = {
            **state,
            **answer_result["output"],
            "agent_trace": answer_payload.get("agent_trace", [*state.get("agent_trace", []), answer_result["trace"]]),
        }
        safety_result = safety_reviewer_agent.run(reviewed_state)
        payload = safety_result["output"]["answer_payload"]
        return {
            **reviewed_state,
            **safety_result["output"],
            "agent_trace": payload.get("agent_trace", [*reviewed_state.get("agent_trace", []), safety_result["trace"]]),
        }

    graph = StateGraph(AgentState)
    graph.add_node("parse", parse_node)
    graph.add_node("route", route_node)
    graph.add_node("rewrite", rewrite_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("planner", planner_node)
    graph.add_node("evidence_check", evidence_check_node)
    graph.add_node("domain", domain_node)
    graph.add_node("answer", answer_node)
    graph.set_entry_point("parse")
    graph.add_edge("parse", "route")
    graph.add_edge("route", "rewrite")
    graph.add_edge("rewrite", "retrieve")
    graph.add_edge("retrieve", "planner")
    graph.add_edge("planner", "evidence_check")
    graph.add_conditional_edges(
        "evidence_check",
        after_evidence_check,
        {"domain": "domain", "rewrite": "rewrite"},
    )
    graph.add_edge("domain", "answer")
    graph.add_edge("answer", END)
    return graph.compile()
