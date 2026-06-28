from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agromech_api.agent_router import route_question
from agromech_api.agent_state import AgentState, append_agent_trace
from agromech_api.agent_tools import build_text_retrieval_tool
from agromech_api.evidence_check import check_evidence_sufficiency
from agromech_api.query_rewrite import rewrite_query_for_evidence


MAX_SUPPLEMENTAL_ROUNDS = 2


def evidence_insufficient_payload(trace_id: str | None) -> dict[str, Any]:
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


def build_agent_graph(
    *,
    parse_query_fn: Callable[..., Any],
    retrieve_fn: Callable[..., dict[str, Any]],
    answer_fn: Callable[..., dict[str, Any]],
):
    from langgraph.graph import END, StateGraph

    text_retrieval_tool = build_text_retrieval_tool(retrieve_fn)

    def parse_node(state: AgentState) -> AgentState:
        parsed = parse_query_fn(state["question"], engine=state.get("engine"))
        return {**state, "parsed_query": parsed}

    def route_node(state: AgentState) -> AgentState:
        decision = route_question(
            state["question"],
            parsed_query=state.get("parsed_query"),
            image_context=state.get("image_context"),
        )
        updated = {**state, "route": decision}
        return append_agent_trace(
            updated,
            step="route",
            decision=decision["route"],
            reason=decision["reason"],
            source=decision["source"],
        )

    def retrieve_node(state: AgentState) -> AgentState:
        retrieval = text_retrieval_tool.invoke(
            {
                "payload": {
                    "engine": state.get("engine"),
                    "question": state.get("rewritten_query") or state["question"],
                    "filters": state.get("filters") or {},
                    "trace_id": state.get("trace_id"),
                    "route": state.get("route") or {},
                    "image_context": state.get("image_context"),
                }
            }
        )
        return {
            **state,
            "retrieval": retrieval,
            "final_evidence": retrieval.get("final_evidence", []),
            "citations": retrieval.get("citations", state.get("citations", [])),
        }

    def evidence_check_node(state: AgentState) -> AgentState:
        check = check_evidence_sufficiency(
            question=state.get("rewritten_query") or state["question"],
            final_evidence=state.get("final_evidence") or [],
            citations=state.get("citations") or [],
        )
        updated = {**state, "evidence_check": check}
        return append_agent_trace(
            updated,
            step="evidence_check",
            decision=check["status"],
            reason=check["reason"],
            missing=check["missing"],
        )

    def rewrite_node(state: AgentState) -> AgentState:
        rewritten = rewrite_query_for_evidence(
            question=state.get("rewritten_query") or state["question"],
            filters=state.get("filters") or {},
            missing=state.get("evidence_check", {}).get("missing", []),
        )
        round_number = int(state.get("retrieval_round", 0)) + 1
        updated = {
            **state,
            "rewritten_query": rewritten["query"],
            "retrieval_round": round_number,
        }
        return append_agent_trace(
            updated,
            step="rewrite",
            decision="retry",
            reason=rewritten["reason"],
            round=round_number,
        )

    def after_evidence_check(state: AgentState) -> str:
        check = state.get("evidence_check") or {}
        if check.get("status") == "sufficient":
            return "answer"
        if not state.get("final_evidence"):
            return "answer"
        if int(state.get("retrieval_round", 0)) < MAX_SUPPLEMENTAL_ROUNDS:
            return "rewrite"
        return "answer"

    def answer_node(state: AgentState) -> AgentState:
        check = state.get("evidence_check") or {}
        if check.get("status") != "sufficient":
            guarded = append_agent_trace(
                state,
                step="generation_guard",
                decision="insufficient",
                reason="evidence check did not pass",
            )
            payload = {
                **evidence_insufficient_payload(state.get("trace_id")),
                "agent_trace": guarded.get("agent_trace", []),
            }
            return {**guarded, "answer_payload": payload}

        payload = answer_fn(
            engine=state.get("engine"),
            question=state["question"],
            filters=state.get("filters") or {},
            trace_id=state.get("trace_id"),
            route=state.get("route") or {},
            retrieval=state.get("retrieval") or {},
            final_evidence=state.get("final_evidence") or [],
            image_context=state.get("image_context"),
        )
        payload = {**payload, "agent_trace": state.get("agent_trace", [])}
        return {**state, "answer_payload": payload}

    graph = StateGraph(AgentState)
    graph.add_node("parse", parse_node)
    graph.add_node("route", route_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("evidence_check", evidence_check_node)
    graph.add_node("rewrite", rewrite_node)
    graph.add_node("answer", answer_node)
    graph.set_entry_point("parse")
    graph.add_edge("parse", "route")
    graph.add_edge("route", "retrieve")
    graph.add_edge("retrieve", "evidence_check")
    graph.add_conditional_edges(
        "evidence_check",
        after_evidence_check,
        {"answer": "answer", "rewrite": "rewrite"},
    )
    graph.add_edge("rewrite", "retrieve")
    graph.add_edge("answer", END)
    return graph.compile()
