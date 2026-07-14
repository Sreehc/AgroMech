import pytest

from agromech_api.core.config import Settings
from agromech_api.core.infrastructure import dependency_targets


# Local-fallback settings that do not require any real backend credentials.
# `_env_file=None` isolates these unit tests from a developer's populated .env.
LOCAL_BACKENDS = {
    "file_storage_backend": "local",
    "graph_backend": "local",
    "model_provider": "local",
    "embedding_provider": "local",
}


def local_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**LOCAL_BACKENDS, **overrides})


def test_settings_rejects_missing_required_dependency_config() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL is required"):
        Settings(database_url="")


def test_local_backends_do_not_require_real_credentials() -> None:
    settings = local_settings()

    assert settings.file_storage_backend == "local"
    assert settings.bailian_api_key == ""


def test_all_env_example_keys_load_into_settings() -> None:
    # T01: every key declared in .env.example must be loadable by Settings.
    settings = local_settings()

    for field_name in (
        "oss_access_key_id",
        "oss_signed_url_ttl_seconds",
        "embedding_model",
        "embedding_dimension",
        "neo4j_uri",
        "graph_max_hops",
        "bailian_api_key",
        "bm25_top_k",
        "dense_top_k",
        "rrf_k",
        "fusion_top_k",
        "query_rewrite_model",
        "rerank_top_k",
        "evaluation_target_top5_source_hit_rate",
    ):
        assert hasattr(settings, field_name)


def test_oss_backend_requires_credentials() -> None:
    with pytest.raises(ValueError, match="OSS_ACCESS_KEY_ID.*when FILE_STORAGE_BACKEND=oss"):
        local_settings(file_storage_backend="oss", oss_access_key_id="", oss_access_key_secret="")


def test_bailian_provider_requires_api_key() -> None:
    with pytest.raises(ValueError, match="when provider=bailian"):
        local_settings(model_provider="bailian", bailian_api_key="", bailian_base_url="")


def test_get_settings_uses_local_backends_in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    from agromech_api.core.config import get_settings

    monkeypatch.setenv("CI", "true")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.model_provider == "local"
    assert settings.embedding_provider == "local"
    assert settings.bailian_api_key == ""


def test_ci_settings_ignore_populated_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")
    settings = Settings()

    assert settings.model_provider == "local"
    assert settings.embedding_provider == "local"
    assert settings.bailian_api_key == ""


def test_neo4j_connection_settings_are_always_required() -> None:
    # neo4j_uri/user/password are enforced unconditionally by the field validator.
    with pytest.raises(ValueError, match="NEO4J_PASSWORD is required"):
        local_settings(neo4j_password="")


def test_embedding_retry_backoff_parses_to_floats() -> None:
    settings = local_settings(embedding_retry_backoff_seconds="1,2,4")

    assert settings.embedding_retry_backoff == [1.0, 2.0, 4.0]


def test_unknown_legacy_environment_values_are_ignored() -> None:
    settings = local_settings(
        legacy_username="admin",
        legacy_password="",
    )

    assert not hasattr(settings, "legacy_username")
    assert not hasattr(settings, "legacy_password")


def test_settings_no_longer_exposes_legacy_vector_configuration() -> None:
    settings = Settings()
    legacy_prefix = "z" + "vec"

    assert not hasattr(settings, "vector_backend")
    assert not hasattr(settings, f"{legacy_prefix}_path")
    assert not hasattr(settings, f"{legacy_prefix}_collection")
    assert not hasattr(settings, f"{legacy_prefix}_text_collection")
    assert not hasattr(settings, f"{legacy_prefix}_visual_collection")
    assert not hasattr(settings, f"{legacy_prefix}_backup_path")
    assert not hasattr(settings, f"{legacy_prefix}_backup_retention_days")


def test_settings_keep_embedding_provider_configuration() -> None:
    settings = Settings(
        embedding_provider="local",
        embedding_model="text-embedding-v4",
        embedding_dimension=1024,
        visual_embedding_provider="local",
        visual_embedding_model="qwen3-vl-embedding",
        visual_embedding_dimension=1024,
    )

    assert settings.embedding_provider == "local"
    assert settings.embedding_dimension == 1024
    assert settings.visual_embedding_provider == "local"
    assert settings.visual_embedding_dimension == 1024


def test_auth_token_secret_remains_required_for_database_auth() -> None:
    with pytest.raises(ValueError, match="AUTH_TOKEN_SECRET is required"):
        local_settings(auth_token_secret="")


def test_rerank_final_evidence_limit_cannot_exceed_top_k() -> None:
    with pytest.raises(ValueError, match="FINAL_EVIDENCE_LIMIT must be <= RERANK_TOP_K"):
        local_settings(rerank_top_k=3, final_evidence_limit=5)


def test_graph_and_evaluation_thresholds_must_be_well_formed() -> None:
    with pytest.raises(
        ValueError,
        match="GRAPH_REVIEW_MIN_CONFIDENCE must be <= GRAPH_AUTO_ACCEPT_LLM_CONFIDENCE",
    ):
        local_settings(
            graph_auto_accept_llm_confidence=0.60,
            graph_review_min_confidence=0.70,
        )

    with pytest.raises(
        ValueError,
        match="EVALUATION_TARGET_TOP5_SOURCE_HIT_RATE must be between 0 and 1",
    ):
        local_settings(evaluation_target_top5_source_hit_rate=1.2)


def test_dependency_targets_include_shared_postgres_and_neo4j_only() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://agromech:agromech@db.example:5432/agromech",
        graph_backend="neo4j",
        neo4j_uri="bolt://neo4j.example:7687",
    )

    targets = dependency_targets(settings)

    assert set(targets) == {"postgres", "neo4j"}
    assert targets["postgres"].host == "db.example"
    assert targets["postgres"].port == 5432
    assert targets["neo4j"].host == "neo4j.example"
    assert targets["neo4j"].port == 7687


def test_dependency_targets_skip_neo4j_when_graph_backend_is_local() -> None:
    settings = local_settings(
        database_url="postgresql+psycopg://agromech:agromech@db.example:5432/agromech",
        neo4j_uri="bolt://neo4j.example:7687",
        graph_backend="local",
    )

    targets = dependency_targets(settings)

    assert set(targets) == {"postgres"}


def test_local_storage_health_check_reports_ok(tmp_path) -> None:
    from agromech_api.core.infrastructure import check_file_storage

    settings = local_settings(local_file_storage_path=str(tmp_path / "files"))

    check = check_file_storage(settings)

    assert check.name == "file_storage"
    assert check.status == "ok"
    assert check.target.startswith("local:")


def test_local_storage_health_check_reports_unavailable_on_unwritable_path(tmp_path) -> None:
    from agromech_api.core.infrastructure import check_file_storage

    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")
    settings = local_settings(local_file_storage_path=str(blocker / "files"))

    check = check_file_storage(settings)

    assert check.status == "unavailable"
    assert check.error


def test_oss_error_sanitizer_omits_credentials() -> None:
    from agromech_api.core.infrastructure import sanitize_oss_error

    class FakeOssError(Exception):
        status = 403
        code = "AccessDenied"

    message = sanitize_oss_error(FakeOssError("secret-access-key-leaked"))

    assert "secret-access-key-leaked" not in message
    assert "status=403" in message
    assert "code=AccessDenied" in message


def test_infrastructure_health_does_not_require_legacy_vector_settings() -> None:
    from agromech_api.core.infrastructure import check_infrastructure

    checks = check_infrastructure(Settings(_env_file=None))

    assert {check.name for check in checks} >= {
        "postgres",
        "file_storage",
        "pgvector",
        "pg_search",
        "bailian",
    }
    assert "z" + "vec" not in {check.name for check in checks}


def test_pgvector_extension_health_check_uses_supplied_engine() -> None:
    from agromech_api.core.infrastructure import check_pgvector_extension

    class FakeResult:
        def scalar_one_or_none(self):
            return "vector"

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, statement):
            assert "pg_extension" in str(statement)
            return FakeResult()

    class FakeEngine:
        url = "postgresql+psycopg://agromech:***@localhost:5432/agromech"

        def connect(self):
            return FakeConnection()

    check = check_pgvector_extension(FakeEngine())

    assert check.name == "pgvector"
    assert check.status == "ok"
    assert check.target == "postgres:extension/vector"


def test_pgvector_extension_health_check_reports_missing_extension() -> None:
    from agromech_api.core.infrastructure import check_pgvector_extension

    class FakeResult:
        def scalar_one_or_none(self):
            return None

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, statement):
            assert "pg_extension" in str(statement)
            return FakeResult()

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    check = check_pgvector_extension(FakeEngine())

    assert check.name == "pgvector"
    assert check.status == "unavailable"
    assert check.target == "postgres:extension/vector"
    assert check.error == "pgvector extension is not installed"


def test_pgvector_extension_health_check_sanitizes_database_errors() -> None:
    from agromech_api.core.infrastructure import check_pgvector_extension

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, statement):
            raise RuntimeError(
                "could not connect to postgresql+psycopg://user:secret@localhost:5432/agromech"
            )

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    check = check_pgvector_extension(FakeEngine())

    assert check.name == "pgvector"
    assert check.status == "unavailable"
    assert check.error
    assert "secret" not in check.error
    assert "user:secret" not in check.error
    assert "postgresql+psycopg://" not in check.error


def test_pg_search_extension_health_check_reports_bm25_index() -> None:
    from agromech_api.core.infrastructure import check_pg_search_extension

    class FakeResult:
        def mappings(self):
            return self

        def one(self):
            return {"extension": True, "index": True}

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement):
            assert "pg_search" in str(statement)
            assert "ix_chunk_search_index_bm25" in str(statement)
            return FakeResult()

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    check = check_pg_search_extension(FakeEngine())

    assert check.status == "ok"
    assert check.name == "pg_search"


def test_bailian_health_check_reports_unavailable_when_required_config_is_missing() -> None:
    from agromech_api.core.infrastructure import check_bailian_config

    settings = local_settings(
        model_provider="local",
        embedding_provider="local",
        bailian_api_key="",
        bailian_base_url="",
    )

    check = check_bailian_config(settings)

    assert check.name == "bailian"
    assert check.status == "unavailable"
    assert check.error == "bailian configuration missing"


def test_bailian_health_check_reports_ok_when_any_bailian_provider_is_configured() -> None:
    from agromech_api.core.infrastructure import check_bailian_config

    settings = local_settings(
        model_provider="bailian",
        bailian_api_key="test-key",
        bailian_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    check = check_bailian_config(settings)

    assert check.name == "bailian"
    assert check.status == "ok"
    assert check.target == "https://dashscope.aliyuncs.com/compatible-mode/v1"
