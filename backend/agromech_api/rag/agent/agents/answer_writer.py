from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agromech_api.rag.agent.agents.base import AgentResult, agent_trace
from agromech_api.rag.agent.state import AgentState


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


class AnswerWriterAgent:
    name = "AnswerWriterAgent"

    def __init__(
        self,
        *,
        answer_fn: Callable[..., dict[str, Any]],
        multimodal_answer_fn: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.answer_fn = answer_fn
        self.multimodal_answer_fn = multimodal_answer_fn

    def run(self, state: AgentState) -> AgentResult:
        check = state.get("evidence_check") or {}
        visual_evidence_present = any(
            evidence.get("evidence_type") == "visual_page" for evidence in (state.get("final_evidence") or [])
        )
        if check.get("status") != "sufficient":
            trace = agent_trace(
                agent=self.name,
                step="generation_guard",
                status="blocked",
                decision="insufficient",
                reason="evidence check did not pass",
            )
            payload = {
                **evidence_insufficient_payload(state.get("trace_id")),
                "agent_trace": [*state.get("agent_trace", []), trace],
            }
            return {
                "status": "blocked",
                "output": {"answer_payload": payload},
                "trace": trace,
            }

        selected_answer_fn = (
            self.multimodal_answer_fn
            if visual_evidence_present and self.multimodal_answer_fn is not None
            else self.answer_fn
        )
        retrieval_payload = state.get("retrieval") or {}
        if visual_evidence_present:
            retrieval_payload = {
                **retrieval_payload,
                "visual_retrieval": state.get("visual_retrieval") or {},
            }
        payload = selected_answer_fn(
            engine=state.get("engine"),
            question=state["question"],
            filters=state.get("filters") or {},
            trace_id=state.get("trace_id"),
            route=state.get("route") or {},
            retrieval=retrieval_payload,
            final_evidence=state.get("final_evidence") or [],
            image_context=state.get("image_context"),
            planner=state.get("planner") or {},
            domain_context=state.get("domain_context") or {},
        )
        trace = agent_trace(
            agent=self.name,
            step="answer",
            status="ok",
            decision="answered",
            reason="answer generated from reviewed evidence",
        )
        payload = {**payload, "agent_trace": [*state.get("agent_trace", []), trace]}
        return {
            "status": "ok",
            "output": {"answer_payload": payload},
            "trace": trace,
        }
