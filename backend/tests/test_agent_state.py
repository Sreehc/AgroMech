from agromech_api.rag.agent.state import append_agent_trace, initial_agent_state


def test_initial_agent_state_contains_question_filters_and_trace() -> None:
    state = initial_agent_state(question="How often change oil?", filters={"model": "M7040"})

    assert state["question"] == "How often change oil?"
    assert state["filters"] == {"model": "M7040"}
    assert state["retrieval_round"] == 0
    assert state["agent_trace"] == []


def test_append_agent_trace_returns_new_state_entry() -> None:
    state = initial_agent_state(question="Where is this part?", filters={})

    updated = append_agent_trace(state, step="route", decision="text_visual", reason="visual wording")

    assert updated["agent_trace"] == [
        {"step": "route", "decision": "text_visual", "reason": "visual wording"}
    ]
