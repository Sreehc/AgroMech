from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agromech_api.rag.agent.graph import build_agent_graph
from agromech_api.rag.agent.state import initial_agent_state


class AgentController:
    def __init__(
        self,
        *,
        parse_query_fn: Callable[..., Any],
        rewrite_fn: Callable[..., dict[str, object]],
        retrieve_fn: Callable[..., dict[str, Any]],
        planner_fn: Callable[..., dict[str, Any]] | None = None,
        visual_retrieve_fn: Callable[..., dict[str, Any]] | None = None,
        answer_fn: Callable[..., dict[str, Any]],
        multimodal_answer_fn: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.graph = build_agent_graph(
            parse_query_fn=parse_query_fn,
            rewrite_fn=rewrite_fn,
            retrieve_fn=retrieve_fn,
            planner_fn=planner_fn,
            visual_retrieve_fn=visual_retrieve_fn,
            answer_fn=answer_fn,
            multimodal_answer_fn=multimodal_answer_fn,
        )

    def answer_text(
        self,
        *,
        engine,
        question: str,
        trace_id: str,
        filters: dict[str, str | None] | None,
        image_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = initial_agent_state(
            question=question,
            filters=filters,
            image_context=image_context,
        )
        state["engine"] = engine
        state["trace_id"] = trace_id
        result = self.graph.invoke(state)
        return result["answer_payload"]
