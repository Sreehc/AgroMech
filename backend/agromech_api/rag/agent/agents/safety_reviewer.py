from __future__ import annotations

from copy import deepcopy

from agromech_api.rag.agent.agents.base import AgentResult, agent_trace
from agromech_api.rag.agent.state import AgentState


class SafetyReviewerAgent:
    name = "SafetyReviewerAgent"
    DEFAULT_SAFETY_WARNING = "涉及液压、电气、发动机、制动或旋转部件时，维修前请停机、断电、释放压力，并按厂家安全规程操作。"
    HIGH_RISK_TERMS = (
        "液压",
        "电气",
        "发动机",
        "制动",
        "旋转",
        "hydraulic",
        "electrical",
        "engine",
        "brake",
        "rotating",
    )

    def run(self, state: AgentState) -> AgentResult:
        payload = deepcopy(state.get("answer_payload") or {})
        warnings = list(payload.get("safety_warnings") or [])
        high_risk = self._is_high_risk(state)
        decision = "passed"
        if high_risk and not warnings:
            warnings = [self.DEFAULT_SAFETY_WARNING]
            payload["safety_warnings"] = warnings
            sections = dict(payload.get("sections") or {})
            sections["safety_reminder"] = warnings
            payload["sections"] = sections
            decision = "warning_added"

        trace = agent_trace(
            agent=self.name,
            step="safety_review",
            status="ok",
            decision=decision,
            reason="high-risk terms require safety warning" if decision == "warning_added" else "safety review passed",
        )
        payload["agent_trace"] = [*payload.get("agent_trace", state.get("agent_trace", [])), trace]
        return {
            "status": "ok",
            "output": {"answer_payload": payload},
            "trace": trace,
        }

    def _is_high_risk(self, state: AgentState) -> bool:
        question = str(state.get("question") or "").lower()
        evidence_text = " ".join(
            str(item.get("evidence_snippet") or item.get("content") or "").lower()
            for item in state.get("final_evidence", [])
        )
        combined = f"{question} {evidence_text}"
        return any(term.lower() in combined for term in self.HIGH_RISK_TERMS)
