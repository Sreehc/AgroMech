from agromech_api.agent_controller import AgentController


def test_agent_controller_runs_text_only_minimum_loop() -> None:
    calls: list[str] = []
    controller = AgentController(
        parse_query_fn=lambda question, engine=None: calls.append("parse") or object(),
        retrieve_fn=lambda **kwargs: calls.append("retrieve")
        or {
            "status": "ok",
            "final_evidence": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
            "citations": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
        },
        answer_fn=lambda **kwargs: calls.append("answer")
        or {"answer": "ok", "citations": [], "trace_id": kwargs["trace_id"]},
    )

    payload = controller.answer_text(
        engine=None,
        question="MG2004 液压油多久换一次？",
        trace_id="trace-1",
        filters={},
    )

    assert payload["answer"] == "ok"
    assert calls == ["parse", "retrieve", "answer"]
    assert payload["agent_trace"][0]["step"] == "route"


def test_agent_controller_rewrites_and_retries_when_evidence_is_insufficient() -> None:
    calls: list[str] = []

    def retrieve_fn(**kwargs):
        calls.append(str(kwargs["question"]))
        if len(calls) == 1:
            return {
                "status": "ok",
                "final_evidence": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
                "citations": [],
            }
        return {
            "status": "ok",
            "final_evidence": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
            "citations": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
        }

    controller = AgentController(
        parse_query_fn=lambda question, engine=None: object(),
        retrieve_fn=retrieve_fn,
        answer_fn=lambda **kwargs: {
            "answer": "ok",
            "citations": [{"chunk_id": "chunk-1"}],
            "trace_id": kwargs["trace_id"],
        },
    )

    payload = controller.answer_text(
        engine=None,
        question="液压泵异响怎么检查？",
        trace_id="trace-1",
        filters={"model": "M7040"},
    )

    assert payload["answer"] == "ok"
    assert len(calls) == 2
    assert "hydraulic pump" in calls[1]
    assert any(entry["step"] == "rewrite" for entry in payload["agent_trace"])


def test_agent_controller_generation_guard_skips_answer_when_evidence_remains_insufficient() -> None:
    calls: list[str] = []
    controller = AgentController(
        parse_query_fn=lambda question, engine=None: object(),
        retrieve_fn=lambda **kwargs: {
            "status": "ok",
            "final_evidence": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
            "citations": [],
        },
        answer_fn=lambda **kwargs: calls.append("answer") or {"answer": "should not happen"},
    )

    payload = controller.answer_text(
        engine=None,
        question="液压泵异响怎么检查？",
        trace_id="trace-guard",
        filters={},
    )

    assert calls == []
    assert payload["answer"] == "未找到足够来源证据，无法给出确定性结论。"
    assert payload["citations"] == []
    assert payload["agent_trace"][-1]["decision"] == "insufficient"
