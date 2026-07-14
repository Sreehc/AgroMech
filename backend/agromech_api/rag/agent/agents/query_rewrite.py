from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agromech_api.rag.agent.agents.base import AgentResult, agent_trace
from agromech_api.rag.agent.state import AgentState


class QueryRewriteAgent:
    name = "QueryRewriteAgent"

    def __init__(self, rewrite_fn: Callable[..., dict[str, Any]]) -> None:
        self.rewrite_fn = rewrite_fn

    def run(self, state: AgentState) -> AgentResult:
        supplemental = int(state.get("retrieval_round", 0)) > 0
        rewritten = self.rewrite_fn(
            question=state["question"],
            parsed_query=state.get("parsed_query"),
            filters=state.get("filters") or {},
            supplemental=supplemental,
        )
        round_number = int(state.get("retrieval_round", 0)) + 1
        trace = rewritten["trace"]
        return {
            "status": "ok",
            "output": {
                "rewritten_query": rewritten["query"],
                "query_rewrite": trace,
                "retrieval_round": round_number,
            },
            "trace": agent_trace(
                agent=self.name,
                step="rewrite",
                status="ok",
                decision="fallback" if trace.get("fallback") else "rewritten",
                reason=trace.get("reason"),
                round=round_number,
            ),
        }
