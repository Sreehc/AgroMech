from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agromech_api.rag.agent.agents.base import AgentResult, agent_trace
from agromech_api.rag.agent.state import AgentState
from agromech_api.rag.agent.tools import build_text_retrieval_tool


class RetrievalAgent:
    name = "RetrievalAgent"

    def __init__(self, retrieve_fn: Callable[..., dict[str, Any]]) -> None:
        self.text_retrieval_tool = build_text_retrieval_tool(retrieve_fn)

    def run(self, state: AgentState) -> AgentResult:
        retrieval = self.text_retrieval_tool.invoke(
            {
                "payload": {
                    "engine": state.get("engine"),
                    "question": state.get("rewritten_query") or state["question"],
                    "original_question": state["question"],
                    "query_rewrite": state.get("query_rewrite") or {},
                    "retrieval_round": int(state.get("retrieval_round", 0)),
                    "filters": state.get("filters") or {},
                    "trace_id": state.get("trace_id"),
                    "route": state.get("route") or {},
                    "image_context": state.get("image_context"),
                }
            }
        )
        return {
            "status": str(retrieval.get("status") or "ok"),
            "output": {
                "retrieval": retrieval,
                "final_evidence": retrieval.get("final_evidence", []),
                "citations": retrieval.get("citations", state.get("citations", [])),
            },
            "trace": agent_trace(
                agent=self.name,
                step="retrieve",
                status=str(retrieval.get("status") or "ok"),
                decision=str(retrieval.get("status") or "ok"),
                reason="text retrieval completed",
            ),
        }
