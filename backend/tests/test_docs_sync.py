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


def test_deployment_release_gate_rebuilds_and_smoke_tests_before_static_cutover() -> None:
    root = Path(__file__).parents[2]
    workflow = (root / ".github/workflows/deploy.yml").read_text(encoding="utf-8")

    assert "scripts/rebuild-vector-index.py" in workflow
    assert "scripts/evaluate-retrieval.py" in workflow
    assert 'RETRIEVAL_BASELINE_PATH:?RETRIEVAL_BASELINE_PATH must be configured' in workflow
    assert 'test -r "$RETRIEVAL_BASELINE_PATH"' in workflow
    assert '--baseline "$RETRIEVAL_BASELINE_PATH"' in workflow
    assert "curl --fail --retry" in workflow
    assert "/health/ready" in workflow
    assert "/qa/text" in workflow
    assert 'payload.get("citations")' in workflow
    assert 'docker compose exec -T -e TRACE_ID="$trace_id" api' in workflow
    assert workflow.index("scripts/rebuild-vector-index.py") < workflow.index("Upload frontend static files")
    assert workflow.index("scripts/evaluate-retrieval.py") < workflow.index("Upload frontend static files")
    assert workflow.index("/health/ready") < workflow.index("sudo systemctl reload nginx")


def test_production_baseline_is_required_and_documented() -> None:
    root = Path(__file__).parents[2]
    deployment = (root / "docs/deployment.md").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    production_env = (root / "deploy/env.prod.example").read_text(encoding="utf-8")

    assert "RETRIEVAL_BASELINE_PATH=" in production_env
    assert "RETRIEVAL_BASELINE_PATH" in deployment
    assert "--baseline \"$RETRIEVAL_BASELINE_PATH\"" in deployment
    assert "RETRIEVAL_BASELINE_PATH" in readme


def test_first_deployment_checks_extensions_before_migration_and_bm25_after_rebuild() -> None:
    deployment = Path("docs/deployment.md").read_text(encoding="utf-8")

    preflight_start = deployment.index("升级前数据库检查")
    migration_start = deployment.index("启动与迁移")
    rebuild_start = deployment.index("迁移完成后")
    preflight = deployment[preflight_start:migration_start]

    assert "pg_extension" in preflight
    assert "FROM pg_indexes" not in preflight
    assert deployment.index("ix_chunk_search_index_bm25", rebuild_start) > rebuild_start
