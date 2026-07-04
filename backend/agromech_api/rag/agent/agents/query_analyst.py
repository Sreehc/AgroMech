from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agromech_api.rag.agent.agents.base import AgentResult, agent_trace
from agromech_api.rag.agent.state import AgentState


class QueryAnalystAgent:
    name = "QueryAnalystAgent"

    def __init__(self, parse_query_fn: Callable[..., Any]) -> None:
        self.parse_query_fn = parse_query_fn

    def run(self, state: AgentState) -> AgentResult:
        parsed = self.parse_query_fn(state["question"], engine=state.get("engine"))
        return {
            "status": "ok",
            "output": {"parsed_query": parsed},
            "trace": agent_trace(
                agent=self.name,
                step="parse",
                status="ok",
                decision="parsed",
                reason="query parsed",
            ),
        }
