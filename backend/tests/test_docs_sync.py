from pathlib import Path


def test_api_doc_references_current_runtime_routes() -> None:
    api_doc = Path("docs/API.md").read_text(encoding="utf-8")

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
