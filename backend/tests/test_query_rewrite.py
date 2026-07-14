import pytest

from agromech_api.core.config import Settings
from agromech_api.rag.retrieval.query_rewrite import (
    BailianQueryRewriteProvider,
    build_query_rewrite_provider,
    rewrite_query,
    rewrite_query_for_evidence,
)
from agromech_api.rag.retrieval.query_understanding import parse_query


def rewrite_settings() -> Settings:
    return Settings(
        _env_file=None,
        model_provider="bailian",
        embedding_provider="local",
        bailian_api_key="test-key",
        bailian_base_url="https://bailian.example",
    )


def test_query_rewrite_expands_domain_synonyms_and_preserves_filters() -> None:
    result = rewrite_query_for_evidence(
        question="液压泵异响怎么检查？",
        filters={"model": "M7040"},
        missing=["part"],
    )

    assert "液压泵异响怎么检查？" in result["query"]
    assert "hydraulic pump" in result["query"]
    assert "abnormal noise" in result["query"]
    assert result["filters"] == {"model": "M7040"}
    assert result["reason"] == "expanded domain synonyms for missing evidence"


def test_llm_rewrite_preserves_protected_identifiers() -> None:
    provider = BailianQueryRewriteProvider(
        rewrite_settings(),
        transport=lambda _request, _timeout: {
            "choices": [{"message": {"content": '{"query":"M7040 E01 液压泵 hydraulic pump 检查"}'}}]
        },
    )

    result = rewrite_query(
        question="M7040 的 E01 液压泵怎么检查？",
        parsed=parse_query("M7040 的 E01 液压泵怎么检查？"),
        request_filters={},
        provider=provider,
        supplemental=False,
    )

    assert result.query == "M7040 E01 液压泵 hydraulic pump 检查"
    assert result.original_query == "M7040 的 E01 液压泵怎么检查？"
    assert result.fallback is False
    assert result.protected_identifiers == ["M7040", "E01"]


def test_llm_rewrite_losing_model_uses_rule_fallback() -> None:
    provider = BailianQueryRewriteProvider(
        rewrite_settings(),
        transport=lambda _request, _timeout: {
            "choices": [{"message": {"content": '{"query":"E01 hydraulic pump 检查"}'}}]
        },
    )

    result = rewrite_query(
        question="M7040 的 E01 液压泵怎么检查？",
        parsed=parse_query("M7040 的 E01 液压泵怎么检查？"),
        request_filters={},
        provider=provider,
        supplemental=False,
    )

    assert result.fallback is True
    assert result.reason == "protected_identifier_missing:M7040"
    assert result.provider == "rule"
    assert result.model is None
    trace = result.to_trace()
    assert trace["attempted_provider"] == "bailian"
    assert trace["attempted_model"] == "qwen3.6-flash"
    assert "M7040" in result.query
    assert "hydraulic pump" in result.query


def test_rewrite_protects_part_number_version_language_and_document_type() -> None:
    provider = BailianQueryRewriteProvider(
        rewrite_settings(),
        transport=lambda _request, _timeout: {
            "choices": [{"message": {"content": '{"query":"RE-12345 repair_manual zh-CN 2024 查询"}'}}]
        },
    )
    question = "RE-12345 repair_manual zh-CN 2024 查询"
    result = rewrite_query(
        question=question,
        parsed=parse_query(question),
        request_filters={},
        provider=provider,
        supplemental=False,
    )
    assert result.fallback is False
    assert set(result.protected_identifiers) == {"RE-12345", "2024", "zh-CN", "repair_manual"}


def test_rewrite_merges_query_and_request_metadata_identifiers() -> None:
    provider = BailianQueryRewriteProvider(
        rewrite_settings(),
        transport=lambda _request, _timeout: {
            "choices": [
                {
                    "message": {
                        "content": '{"query":"repair_manual operator_manual zh-CN en-US 2024 2023 查询"}'
                    }
                }
            ]
        },
    )
    question = "repair_manual zh-CN 2024 查询"

    result = rewrite_query(
        question=question,
        parsed=parse_query(question),
        request_filters={
            "document_version": "2023",
            "language": "en-US",
            "document_type": "operator_manual",
        },
        provider=provider,
        supplemental=False,
    )

    assert result.fallback is False
    assert set(result.protected_identifiers) == {
        "2024",
        "2023",
        "zh-CN",
        "en-US",
        "repair_manual",
        "operator_manual",
    }


def test_blank_request_metadata_does_not_replace_query_identifiers() -> None:
    provider = BailianQueryRewriteProvider(
        rewrite_settings(),
        transport=lambda _request, _timeout: {
            "choices": [{"message": {"content": '{"query":"repair_manual zh-CN 2024 查询"}'}}]
        },
    )
    question = "repair_manual zh-CN 2024 查询"

    result = rewrite_query(
        question=question,
        parsed=parse_query(question),
        request_filters={"document_version": "  ", "language": "\t", "document_type": ""},
        provider=provider,
        supplemental=False,
    )

    assert result.fallback is False
    assert set(result.protected_identifiers) == {"2024", "zh-CN", "repair_manual"}


def test_rewrite_protects_query_and_explicit_filter_models() -> None:
    provider = BailianQueryRewriteProvider(
        rewrite_settings(),
        transport=lambda _request, _timeout: {
            "choices": [{"message": {"content": '{"query":"M7040 M7060 hydraulic pump 检查"}'}}]
        },
    )

    result = rewrite_query(
        question="M7040 的液压泵怎么检查？",
        parsed=parse_query("M7040 的液压泵怎么检查？"),
        request_filters={"model": "M7060"},
        provider=provider,
        supplemental=False,
    )

    assert result.fallback is False
    assert result.protected_identifiers == ["M7040", "M7060"]


def test_rewrite_protects_models_only_found_in_parsed_entities() -> None:
    for model in ("6M", "8R410"):
        provider = BailianQueryRewriteProvider(
            rewrite_settings(),
            transport=lambda _request, _timeout, model=model: {
                "choices": [{"message": {"content": f'{{"query":"{model} hydraulic pump check"}}'}}]
            },
        )

        result = rewrite_query(
            question=f"{model} hydraulic pump check",
            parsed=parse_query(f"{model} hydraulic pump check"),
            request_filters={},
            provider=provider,
            supplemental=False,
        )

        assert result.fallback is False
        assert result.protected_identifiers == [model]


@pytest.mark.parametrize(
    ("rewritten", "missing"),
    [
        ("M7040X E01 RE-12345 repair_manual zh-CN 2024 查询", "M7040"),
        ("M7040 E010 RE-12345 repair_manual zh-CN 2024 查询", "E01"),
        ("M7040 E01 RE-12345 repair_manual_extra zh-CN 2024 查询", "repair_manual"),
        ("M7040 E01 RE-12345 repair_manual zh-CN-x 2024 查询", "zh-CN"),
        ("M7040 E01 RE-12345 repair_manual zh-CN 20240 查询", "2024"),
    ],
)
def test_rewrite_rejects_substrings_that_impersonate_complete_identifiers(
    rewritten: str,
    missing: str,
) -> None:
    provider = BailianQueryRewriteProvider(
        rewrite_settings(),
        transport=lambda _request, _timeout: {
            "choices": [{"message": {"content": f'{{"query":"{rewritten}"}}'}}]
        },
    )
    question = "M7040 E01 RE-12345 repair_manual zh-CN 2024 查询"

    result = rewrite_query(
        question=question,
        parsed=parse_query(question),
        request_filters={},
        provider=provider,
        supplemental=False,
    )

    assert result.fallback is True
    assert result.reason == f"protected_identifier_missing:{missing}"


@pytest.mark.parametrize(
    "added",
    ["M7060", "E02", "AB-987", "operator_manual", "en-US", "2023"],
)
def test_rewrite_rejects_new_exact_identifier_facts(added: str) -> None:
    rewritten = f"M7040 E01 RE-12345 repair_manual zh-CN 2024 {added} 查询"
    provider = BailianQueryRewriteProvider(
        rewrite_settings(),
        transport=lambda _request, _timeout: {
            "choices": [{"message": {"content": f'{{"query":"{rewritten}"}}'}}]
        },
    )
    question = "M7040 E01 RE-12345 repair_manual zh-CN 2024 查询"

    result = rewrite_query(
        question=question,
        parsed=parse_query(question),
        request_filters={},
        provider=provider,
        supplemental=False,
    )

    assert result.fallback is True
    assert result.reason == f"protected_identifier_added:{added}"


def test_rewrite_accepts_normalized_complete_identifiers() -> None:
    provider = BailianQueryRewriteProvider(
        rewrite_settings(),
        transport=lambda _request, _timeout: {
            "choices": [
                {
                    "message": {
                        "content": '{"query":"m-7040 e01 re-12345 REPAIR_MANUAL ZH-cn 2024 查询"}'
                    }
                }
            ]
        },
    )
    question = "M7040 E01 RE-12345 repair_manual zh-CN 2024 查询"

    result = rewrite_query(
        question=question,
        parsed=parse_query(question),
        request_filters={},
        provider=provider,
        supplemental=False,
    )

    assert result.fallback is False


def test_supplemental_rewrite_never_calls_provider() -> None:
    calls = []

    class ExplodingProvider:
        provider = "test"
        model = "test"

        def rewrite(self, question: str, protected_identifiers: list[str]) -> str:
            calls.append(question)
            raise AssertionError("provider must not be called")

    result = rewrite_query(
        question="液压泵异响怎么检查？",
        parsed=parse_query("液压泵异响怎么检查？"),
        request_filters={},
        provider=ExplodingProvider(),
        supplemental=True,
    )

    assert calls == []
    assert result.fallback is True
    assert "hydraulic pump" in result.query


def test_rewrite_result_trace_keeps_original_and_rewritten_queries() -> None:
    provider = BailianQueryRewriteProvider(
        rewrite_settings(),
        transport=lambda _request, _timeout: {
            "choices": [{"message": {"content": '{"query":"M7040 E01 hydraulic pump 检查"}'}}]
        },
    )

    result = rewrite_query(
        question="M7040 的 E01 液压泵怎么检查？",
        parsed=parse_query("M7040 的 E01 液压泵怎么检查？"),
        request_filters={},
        provider=provider,
        supplemental=False,
    )

    trace = result.to_trace()
    assert trace["original_query"] == "M7040 的 E01 液压泵怎么检查？"
    assert trace["query"] == "M7040 E01 hydraulic pump 检查"
    assert trace["provider"] == "bailian"
    assert trace["fallback"] is False
    assert trace["protected_identifiers"] == ["M7040", "E01"]


def test_provider_error_uses_rule_fallback() -> None:
    class ExplodingProvider:
        provider = "test"
        model = "failed-model"

        def rewrite(self, question: str, protected_identifiers: list[str]) -> str:
            raise RuntimeError("provider failed with test-key")

    result = rewrite_query(
        question="M7040 的液压泵怎么检查？",
        parsed=parse_query("M7040 的液压泵怎么检查？"),
        request_filters={},
        provider=ExplodingProvider(),
        supplemental=False,
    )

    assert result.fallback is True
    assert result.reason == "provider_error"
    assert result.provider == "rule"
    assert result.model is None
    assert "M7040" in result.query
    assert "hydraulic pump" in result.query
    trace = result.to_trace()
    assert trace["attempted_provider"] == "test"
    assert trace["attempted_model"] == "failed-model"
    trace_text = repr(trace)
    assert "provider failed" not in trace_text
    assert "test-key" not in trace_text


def test_build_query_rewrite_provider_for_enabled_bailian() -> None:
    provider = build_query_rewrite_provider(rewrite_settings())

    assert isinstance(provider, BailianQueryRewriteProvider)
    assert provider.model == "qwen3.6-flash"
    assert provider.timeout == 10.0


def test_build_query_rewrite_provider_returns_none_when_disabled_or_local() -> None:
    disabled_settings = rewrite_settings()
    disabled_settings.query_rewrite_enabled = False
    local_settings = Settings(_env_file=None, model_provider="local", embedding_provider="local")

    assert build_query_rewrite_provider(disabled_settings) is None
    assert build_query_rewrite_provider(local_settings) is None
