from __future__ import annotations

from agromech_api.rag.agent.agents.base import AgentResult, agent_trace
from agromech_api.rag.agent.router import LlmRouter, route_question
from agromech_api.rag.agent.state import AgentState


QUESTION_TYPE_TERMS = {
    "visual_inspection": (
        "图片",
        "图纸",
        "图",
        "页面",
        "位置",
        "故障灯",
        "仪表",
        "image",
        "diagram",
        "figure",
        "page",
        "visual",
    ),
    "parts": (
        "配件",
        "配件号",
        "零件",
        "部件号",
        "滤芯",
        "part number",
        "parts",
        "replacement",
    ),
    "fault_diagnosis": (
        "故障",
        "故障码",
        "异响",
        "报警",
        "告警",
        "原因",
        "排查",
        "检查",
        "fault",
        "repair",
        "error",
        "diagnosis",
        "symptom",
        "troubleshoot",
    ),
    "maintenance": (
        "保养",
        "多久",
        "周期",
        "更换",
        "油液",
        "maintenance",
        "interval",
        "service",
        "change",
    ),
}


class RouterAgent:
    name = "RouterAgent"

    def __init__(self, llm_router: LlmRouter | None = None) -> None:
        self.llm_router = llm_router

    def run(self, state: AgentState) -> AgentResult:
        decision = route_question(
            state["question"],
            parsed_query=state.get("parsed_query"),
            image_context=state.get("image_context"),
            llm_router=self.llm_router,
        )
        question_type = classify_question_type(state["question"], image_context=state.get("image_context"))
        decision = {**decision, "question_type": question_type}
        return {
            "status": "ok",
            "output": {"route": decision},
            "trace": agent_trace(
                agent=self.name,
                step="route",
                status="ok",
                decision=decision["route"],
                reason=decision["reason"],
                source=decision["source"],
                question_type=question_type,
            ),
        }


def classify_question_type(question: str, *, image_context: dict | None = None) -> str:
    if image_context:
        return "visual_inspection"
    normalized = question.lower()
    for question_type, terms in QUESTION_TYPE_TERMS.items():
        if any(term.lower() in normalized for term in terms):
            return question_type
    return "general"
