from __future__ import annotations

from agromech_api.rag.agent.agents.base import AgentResult, agent_trace
from agromech_api.rag.agent.state import AgentState
from agromech_api.rag.retrieval.query_rewrite import rewrite_query_for_evidence


class QueryRewriteAgent:
    name = "QueryRewriteAgent"

    def run(self, state: AgentState) -> AgentResult:
        rewritten = rewrite_query_for_evidence(
            question=state.get("rewritten_query") or state["question"],
            filters=state.get("filters") or {},
            missing=state.get("evidence_check", {}).get("missing", []),
        )
        round_number = int(state.get("retrieval_round", 0)) + 1
        return {
            "status": "ok",
            "output": {
                "rewritten_query": rewritten["query"],
                "retrieval_round": round_number,
            },
            "trace": agent_trace(
                agent=self.name,
                step="rewrite",
                status="ok",
                decision="retry",
                reason=rewritten["reason"],
                round=round_number,
            ),
        }
