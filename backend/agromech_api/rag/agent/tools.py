from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.tools import tool

from agromech_api.rag.langchain.adapters import AgroMechTextRetriever


def build_text_retrieval_tool(retrieve_fn: Callable[..., dict[str, Any]]):
    @tool("text_retrieval")
    def text_retrieval(payload: dict[str, Any]) -> dict[str, Any]:
        """Run AgroMech text retrieval with the provided agent state payload."""

        retriever = AgroMechTextRetriever(
            engine=payload.get("engine"),
            retrieve_payload_fn=retrieve_fn,
            filters=payload.get("filters") or {},
            trace_id=payload.get("trace_id"),
            route=payload.get("route") or {},
            image_context=payload.get("image_context"),
        )
        return retriever.retrieve_payload(str(payload.get("question") or ""))

    return text_retrieval
