from __future__ import annotations

from typing import Any, Protocol, TypedDict

from agromech_api.rag.agent.state import AgentState


class AgentResult(TypedDict):
    status: str
    output: dict[str, Any]
    trace: dict[str, Any]


class BaseAgent(Protocol):
    name: str

    def run(self, state: AgentState) -> AgentResult:
        ...


def agent_trace(*, agent: str, step: str, status: str, **fields: Any) -> dict[str, Any]:
    return {
        "agent": agent,
        "step": step,
        "status": status,
        **fields,
    }
