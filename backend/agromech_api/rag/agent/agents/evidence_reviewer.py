from __future__ import annotations

from agromech_api.rag.agent.agents.base import AgentResult, agent_trace
from agromech_api.rag.agent.state import AgentState
from agromech_api.rag.retrieval.evidence_check import check_evidence_sufficiency


class EvidenceReviewerAgent:
    name = "EvidenceReviewerAgent"

    def run(self, state: AgentState) -> AgentResult:
        check = check_evidence_sufficiency(
            question=state.get("rewritten_query") or state["question"],
            final_evidence=state.get("final_evidence") or [],
            citations=state.get("citations") or [],
        )
        return {
            "status": check["status"],
            "output": {"evidence_check": check},
            "trace": agent_trace(
                agent=self.name,
                step="evidence_review",
                status=check["status"],
                decision=check["status"],
                reason=check["reason"],
                missing=check["missing"],
                confidence=check.get("confidence"),
            ),
        }
