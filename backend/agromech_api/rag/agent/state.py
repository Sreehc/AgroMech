from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    engine: Any
    trace_id: str
    question: str
    filters: dict[str, str | None]
    image_context: dict[str, Any] | None
    parsed_query: Any
    route: dict[str, Any]
    retrieval_round: int
    retrieval: dict[str, Any]
    visual_retrieval: dict[str, Any]
    final_evidence: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    safety_warnings: list[str]
    uncertainty: dict[str, Any]
    agent_trace: list[dict[str, Any]]
    pending_agent_traces: list[dict[str, Any]]
    answer_payload: dict[str, Any]
    planner: dict[str, Any]
    evidence_check: dict[str, Any]
    domain_context: dict[str, Any]
    rewritten_query: str


def initial_agent_state(
    *,
    question: str,
    filters: dict[str, str | None] | None,
    image_context: dict[str, Any] | None = None,
) -> AgentState:
    return {
        "question": question,
        "filters": filters or {},
        "image_context": image_context,
        "retrieval_round": 0,
        "agent_trace": [],
    }


def append_agent_trace(state: AgentState, **entry: Any) -> AgentState:
    return {**state, "agent_trace": [*state.get("agent_trace", []), entry]}
