import pytest

from agromech_api.config import Settings
from agromech_api.embedding import (
    BailianEmbeddingProvider,
    EmbeddingDimensionError,
    EmbeddingError,
    build_embedding_provider,
)
from agromech_api.search_indexing import DeterministicEmbeddingProvider


def bailian_settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "file_storage_backend": "local",
        "graph_backend": "local",
        "vector_backend": "local",
        "model_provider": "bailian",
        "embedding_provider": "bailian",
        "bailian_api_key": "key",
        "bailian_base_url": "https://bailian.example/compatible-mode/v1",
        "embedding_model": "text-embedding-v4",
        "embedding_dimension": 4,
        "embedding_batch_size": 2,
        "embedding_max_retries": 2,
        "embedding_retry_backoff_seconds": "0,0,0",
    }
    base.update(overrides)
    return Settings(**base)


def make_client(dimension: int):
    """Return a client that yields deterministic vectors and records call sizes."""
    calls: list[int] = []

    def client(batch: list[str]) -> list[list[float]]:
        calls.append(len(batch))
        return [[float(len(text))] * dimension for text in batch]

    client.calls = calls  # type: ignore[attr-defined]
    return client


def test_embed_returns_vector_of_configured_dimension() -> None:
    client = make_client(dimension=4)
    provider = BailianEmbeddingProvider(bailian_settings(), client=client)

    vector = provider.embed("hydraulic")

    assert len(vector) == 4
    assert provider.provider == "bailian"
    assert provider.model == "text-embedding-v4"


def test_embed_batch_respects_configured_batch_size() -> None:
    client = make_client(dimension=4)
    provider = BailianEmbeddingProvider(bailian_settings(), client=client)

    vectors = provider.embed_batch(["a", "bb", "ccc", "dddd", "eeeee"])

    assert len(vectors) == 5
    # batch_size=2 over 5 inputs -> 2 + 2 + 1.
    assert client.calls == [2, 2, 1]


def test_embed_batch_empty_input_returns_empty() -> None:
    provider = BailianEmbeddingProvider(bailian_settings(), client=make_client(dimension=4))

    assert provider.embed_batch([]) == []


def test_dimension_mismatch_raises_and_does_not_retry() -> None:
    attempts: list[int] = []

    def client(batch: list[str]) -> list[list[float]]:
        attempts.append(1)
        return [[0.0, 0.0] for _ in batch]  # wrong dimension (2 != 4)

    provider = BailianEmbeddingProvider(bailian_settings(), client=client)

    with pytest.raises(EmbeddingDimensionError):
        provider.embed("x")
    assert len(attempts) == 1


def test_transient_failure_is_retried_then_succeeds() -> None:
    attempts: list[int] = []

    def client(batch: list[str]) -> list[list[float]]:
        attempts.append(1)
        if len(attempts) < 2:
            raise ConnectionError("temporary")
        return [[1.0, 1.0, 1.0, 1.0] for _ in batch]

    provider = BailianEmbeddingProvider(bailian_settings(), client=client)

    vector = provider.embed("x")

    assert vector == [1.0, 1.0, 1.0, 1.0]
    assert len(attempts) == 2


def test_exhausted_retries_raises_embedding_error() -> None:
    attempts: list[int] = []

    def client(batch: list[str]) -> list[list[float]]:
        attempts.append(1)
        raise TimeoutError("slow")

    provider = BailianEmbeddingProvider(bailian_settings(), client=client)

    with pytest.raises(EmbeddingError):
        provider.embed("x")
    # max_retries=2 -> 1 initial + 2 retries = 3 attempts.
    assert len(attempts) == 3


def test_mismatched_vector_count_raises_embedding_error() -> None:
    def client(batch: list[str]) -> list[list[float]]:
        return [[1.0, 1.0, 1.0, 1.0]]  # only one vector for two inputs

    provider = BailianEmbeddingProvider(
        bailian_settings(embedding_batch_size=8), client=client
    )

    with pytest.raises(EmbeddingError):
        provider.embed_batch(["a", "b"])


def test_build_embedding_provider_selects_bailian() -> None:
    provider = build_embedding_provider(bailian_settings(), client=make_client(dimension=4))

    assert isinstance(provider, BailianEmbeddingProvider)


def test_build_embedding_provider_falls_back_to_local() -> None:
    settings = Settings(
        _env_file=None,
        file_storage_backend="local",
        graph_backend="local",
        vector_backend="local",
        model_provider="local",
        embedding_provider="local",
    )

    provider = build_embedding_provider(settings)

    assert isinstance(provider, DeterministicEmbeddingProvider)
