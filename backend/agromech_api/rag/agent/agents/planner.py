from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agromech_api.rag.agent.agents.base import AgentResult, agent_trace
from agromech_api.rag.agent.state import AgentState
from agromech_api.rag.retrieval.evidence_check import check_evidence_sufficiency


class PlanningAgent:
    name = "PlanningAgent"

    def __init__(self, planner_fn: Callable[..., dict[str, Any]] | None = None) -> None:
        self.planner_fn = planner_fn

    def run(self, state: AgentState) -> AgentResult:
        if self.planner_fn is None:
            check = check_evidence_sufficiency(
                question=state.get("rewritten_query") or state["question"],
                final_evidence=state.get("final_evidence") or [],
                citations=state.get("citations") or [],
            )
            planner = {
                "evidence_sufficient": check["status"] == "sufficient",
                "need_visual": False,
                "need_query_rewrite": check["status"] != "sufficient",
                "next_action": "TEXT_RETRIEVAL" if check["status"] == "sufficient" else "QUERY_REWRITE",
                "missing_slots": check["missing"],
                "reason": check["reason"],
            }
        else:
            planner = self.planner_fn(
                engine=state.get("engine"),
                question=state.get("rewritten_query") or state["question"],
                filters=state.get("filters") or {},
                route=state.get("route") or {},
                retrieval=state.get("retrieval") or {},
                final_evidence=state.get("final_evidence") or [],
                citations=state.get("citations") or [],
                image_context=state.get("image_context"),
            )

        return {
            "status": "ok",
            "output": {"planner": planner},
            "trace": agent_trace(
                agent=self.name,
                step="planner",
                status="ok",
                decision=planner.get("next_action"),
                reason=planner.get("reason"),
                missing=planner.get("missing_slots", []),
            ),
        }
