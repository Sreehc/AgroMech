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
        "deployment.md",
        "ux-spec.md",
        "history.md",
    }
    actual = {path.name for path in Path("docs").glob("*.md")}

    assert actual == expected


def test_current_docs_do_not_describe_legacy_vector_store_as_active() -> None:
    paths = [
        "README.md",
        "docs/README.md",
        "docs/tech-design.md",
        "docs/database-design.md",
        "docs/api-spec.md",
        "docs/deployment.md",
        "docs/prd.md",
        "docs/history.md",
    ]

    for path in paths:
        text = Path(path).read_text(encoding="utf-8")
        assert ("Z" + "vec") not in text, path
        assert ("z" + "vec") not in text, path


def test_docs_describe_dense_bm25_rrf_pipeline_and_pg_search() -> None:
    root = Path(__file__).parents[2]
    tech = (root / "docs/tech-design.md").read_text(encoding="utf-8")
    database = (root / "docs/database-design.md").read_text(encoding="utf-8")
    deployment = (root / "docs/deployment.md").read_text(encoding="utf-8")
    prd = (root / "docs/prd.md").read_text(encoding="utf-8")
    api = (root / "docs/api-spec.md").read_text(encoding="utf-8")

    assert "Dense + BM25" in tech
    assert "RRF" in tech
    assert "pg_search" in database
    assert "ix_chunk_search_index_bm25" in database
    assert "FROM pg_extension" in deployment
    assert "/health/ready" in deployment
    assert "Dense + BM25" in prd
    assert "pg_search" in api
    assert "/health/ready" in api
