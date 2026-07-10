import json
import urllib.error

import pytest

from agromech_api.core.config import Settings
from agromech_api.rag.retrieval.rerank import (
    BailianRerankProvider,
    RerankError,
    build_rerank_provider,
)


def bailian_settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "file_storage_backend": "local",
        "graph_backend": "local",
        "model_provider": "bailian",
        "embedding_provider": "local",
        "bailian_api_key": "key",
        "bailian_base_url": "https://bailian.example/compatible-mode/v1",
        "rerank_model": "qwen3-rerank",
        "rerank_timeout_seconds": 8,
        "rerank_top_k": 30,
    }
    base.update(overrides)
    return Settings(**base)


def test_bailian_rerank_provider_sends_query_documents_and_returns_scores() -> None:
    captured = {}

    def transport(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return {
            "results": [
                {"index": 1, "relevance_score": 0.92},
                {"index": 0, "relevance_score": 0.31},
            ]
        }

    provider = BailianRerankProvider(bailian_settings(), transport=transport)

    scores = provider.rerank("hydraulic warning", ["first chunk", "second chunk"])

    assert scores == [0.31, 0.92]
    assert captured["url"] == "https://bailian.example/compatible-mode/v1/rerank"
    assert captured["timeout"] == 8
    assert captured["headers"]["Authorization"] == "Bearer key"
    assert captured["body"] == {
        "model": "qwen3-rerank",
        "query": "hydraulic warning",
        "documents": ["first chunk", "second chunk"],
        "top_n": 2,
    }


def test_bailian_rerank_provider_empty_documents_returns_empty() -> None:
    provider = BailianRerankProvider(bailian_settings(), transport=lambda _request, _timeout: {})

    assert provider.rerank("query", []) == []


def test_bailian_rerank_provider_rejects_malformed_response() -> None:
    provider = BailianRerankProvider(
        bailian_settings(),
        transport=lambda _request, _timeout: {"results": [{"index": 0}]},
    )

    with pytest.raises(RerankError, match="missing relevance_score"):
        provider.rerank("query", ["document"])


def test_bailian_rerank_provider_wraps_transport_errors_without_leaking_request_body() -> None:
    def transport(_request, _timeout):
        raise urllib.error.URLError("timeout while posting secret document")

    provider = BailianRerankProvider(bailian_settings(), transport=transport)

    with pytest.raises(RerankError) as exc_info:
        provider.rerank("query", ["secret document text"])

    message = str(exc_info.value)
    assert "secret document text" not in message
    assert "key" not in message


def test_build_rerank_provider_selects_bailian_when_enabled() -> None:
    provider = build_rerank_provider(bailian_settings())

    assert isinstance(provider, BailianRerankProvider)


def test_build_rerank_provider_returns_none_when_disabled_or_local() -> None:
    disabled = build_rerank_provider(bailian_settings(rerank_enabled=False))
    local = build_rerank_provider(
        Settings(
            _env_file=None,
            file_storage_backend="local",
            graph_backend="local",
            model_provider="local",
            embedding_provider="local",
        )
    )

    assert disabled is None
    assert local is None
