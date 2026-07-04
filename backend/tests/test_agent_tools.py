from agromech_api.rag.agent.tools import build_text_retrieval_tool


def test_build_text_retrieval_tool_wraps_retrieve_callable() -> None:
    calls: list[dict[str, object]] = []

    tool = build_text_retrieval_tool(
        lambda **kwargs: calls.append(kwargs) or {"status": "ok", "final_evidence": []}
    )

    result = tool.invoke(
        {
            "payload": {
            "engine": None,
            "question": "M7040 E01",
            "filters": {"model": "M7040"},
            "trace_id": "trace-1",
            "route": {"route": "text_only"},
            "image_context": None,
            }
        }
    )

    assert result == {"status": "ok", "final_evidence": []}
    assert calls[0]["question"] == "M7040 E01"
    assert calls[0]["filters"] == {"model": "M7040"}
