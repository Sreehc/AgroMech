from __future__ import annotations

from collections.abc import Callable
from typing import Any


VISUAL_TERMS = (
    "图片",
    "图纸",
    "图",
    "页面",
    "页",
    "位置",
    "标注",
    "外观",
    "这张",
    "这个部件",
    "表格",
    "image",
    "diagram",
    "figure",
    "page",
    "location",
    "visual",
    "table",
)
TEXT_ONLY_TERMS = (
    "多久",
    "周期",
    "参数",
    "规格",
    "故障码",
    "保养",
    "更换",
    "interval",
    "spec",
    "fault code",
    "maintenance",
)


RouteDecision = dict[str, Any]
LlmRouter = Callable[[str, Any, dict[str, Any] | None], RouteDecision | None]


def route_question(
    question: str,
    *,
    parsed_query: Any,
    image_context: dict[str, Any] | None,
    llm_router: LlmRouter | None = None,
) -> RouteDecision:
    if image_context:
        return {
            "route": "text_visual",
            "source": "rule",
            "reason": "visual input is present",
            "confidence": 1.0,
        }

    normalized = question.lower()
    if any(term.lower() in normalized for term in VISUAL_TERMS):
        return {
            "route": "text_visual",
            "source": "rule",
            "reason": "visual wording matched",
            "confidence": 0.9,
        }
    if any(term.lower() in normalized for term in TEXT_ONLY_TERMS):
        return {
            "route": "text_only",
            "source": "rule",
            "reason": "text maintenance or parameter wording matched",
            "confidence": 0.85,
        }

    if llm_router is not None:
        decision = llm_router(question, parsed_query, image_context)
        if decision:
            return {**decision, "source": decision.get("source", "llm")}

    return {
        "route": "text_only",
        "source": "rule",
        "reason": "default text-first route",
        "confidence": 0.55,
    }
