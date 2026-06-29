import pytest

from agromech_api.config import Settings
from agromech_api.infrastructure import dependency_targets


# Local-fallback settings that do not require any real backend credentials.
# `_env_file=None` isolates these unit tests from a developer's populated .env.
LOCAL_BACKENDS = {
    "file_storage_backend": "local",
    "vector_backend": "local",
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
        "zvec_path",
        "zvec_backup_retention_days",
        "embedding_model",
        "embedding_dimension",
        "neo4j_uri",
        "graph_max_hops",
        "bailian_api_key",
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
    from agromech_api.config import get_settings

    monkeypatch.setenv("CI", "true")
    get_settings.cache_clear()

    settings = get_settings()

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
    from agromech_api.infrastructure import check_file_storage

    settings = local_settings(local_file_storage_path=str(tmp_path / "files"))

    check = check_file_storage(settings)

    assert check.name == "file_storage"
    assert check.status == "ok"
    assert check.target.startswith("local:")


def test_local_storage_health_check_reports_unavailable_on_unwritable_path(tmp_path) -> None:
    from agromech_api.infrastructure import check_file_storage

    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")
    settings = local_settings(local_file_storage_path=str(blocker / "files"))

    check = check_file_storage(settings)

    assert check.status == "unavailable"
    assert check.error


def test_oss_error_sanitizer_omits_credentials() -> None:
    from agromech_api.infrastructure import sanitize_oss_error

    class FakeOssError(Exception):
        status = 403
        code = "AccessDenied"

    message = sanitize_oss_error(FakeOssError("secret-access-key-leaked"))

    assert "secret-access-key-leaked" not in message
    assert "status=403" in message
    assert "code=AccessDenied" in message


def test_zvec_health_check_reports_ok_when_storage_paths_are_available(tmp_path) -> None:
    from agromech_api.infrastructure import check_zvec_storage

    settings = local_settings(
        vector_backend="zvec",
        zvec_path=str(tmp_path / "zvec"),
        zvec_backup_path=str(tmp_path / "backups"),
    )

    check = check_zvec_storage(settings)

    assert check.name == "zvec"
    assert check.status == "ok"
    assert check.target == str(tmp_path / "zvec")


def test_zvec_health_check_reports_unavailable_on_unwritable_storage_path(tmp_path) -> None:
    from agromech_api.infrastructure import check_zvec_storage

    blocker = tmp_path / "zvec-blocker"
    blocker.write_bytes(b"not a directory")
    settings = local_settings(
        vector_backend="zvec",
        zvec_path=str(blocker / "store"),
        zvec_backup_path=str(tmp_path / "backups"),
    )

    check = check_zvec_storage(settings)

    assert check.status == "unavailable"
    assert "secret" not in (check.error or "")


def test_bailian_health_check_reports_unavailable_when_required_config_is_missing() -> None:
    from agromech_api.infrastructure import check_bailian_config

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
    from agromech_api.infrastructure import check_bailian_config

    settings = local_settings(
        model_provider="bailian",
        bailian_api_key="test-key",
        bailian_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    check = check_bailian_config(settings)

    assert check.name == "bailian"
    assert check.status == "ok"
    assert check.target == "https://dashscope.aliyuncs.com/compatible-mode/v1"
