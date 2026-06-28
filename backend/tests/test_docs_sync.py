from pathlib import Path


def test_api_doc_references_current_runtime_routes() -> None:
    api_doc = Path("docs/api-spec.md").read_text(encoding="utf-8")

    assert "/auth/login" in api_doc
    assert "/health/dependencies" in api_doc
    assert "/documents" in api_doc
    assert "/qa/text" in api_doc
    assert "/qa/image" in api_doc
    assert "/retrieval-traces/{trace_id}" in api_doc
    assert "/chat-sessions" in api_doc
    assert "基础路径" not in api_doc
    assert "POST /api/v1" not in api_doc
    assert "GET /api/v1" not in api_doc


def test_agentic_runtime_docs_cover_trace_and_rabbitmq_worker() -> None:
    api_doc = Path("docs/api-spec.md").read_text(encoding="utf-8")
    tech_doc = Path("docs/tech-design.md").read_text(encoding="utf-8")
    history_doc = Path("docs/history.md").read_text(encoding="utf-8")

    assert "agent_trace" in api_doc
    assert "RabbitMQ" in tech_doc
    assert "RABBITMQ_PUBLISH_ENABLED" in tech_doc
    assert "consume_forever" in tech_doc
    assert "Agent Controller" in history_doc


def test_docs_directory_only_contains_curated_markdown_set() -> None:
    expected = {
        "README.md",
        "prd.md",
        "tech-design.md",
        "api-spec.md",
        "database-design.md",
        "ux-spec.md",
        "history.md",
    }
    actual = {path.name for path in Path("docs").glob("*.md")}

    assert actual == expected
