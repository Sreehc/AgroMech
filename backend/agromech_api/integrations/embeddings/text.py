from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Callable, Protocol

from agromech_api.core.config import Settings
from agromech_api.rag.retrieval.indexing import DeterministicEmbeddingProvider


# A client maps a batch of input texts to one embedding vector each, in order.
EmbeddingClient = Callable[[list[str]], list[list[float]]]


class EmbeddingProvider(Protocol):
    provider: str
    model: str

    def embed(self, text: str) -> list[float]: ...


class EmbeddingError(RuntimeError):
    """Raised when the embedding service cannot produce a usable vector.

    Indexing translates this into an ``embedding_failed`` ingest failure so the
    document never reaches ``indexed`` (spec 8.3).
    """


class EmbeddingDimensionError(EmbeddingError):
    """Raised when a returned vector does not match the configured dimension.

    This is a configuration error (model/dimension mismatch), not a transient
    failure, so it is never retried (spec 3.5, R03).
    """


class BailianEmbeddingProvider:
    """Aliyun Bailian embedding adapter (OpenAI-compatible ``/embeddings``).

    Calls ``text-embedding-v4`` by default, requesting the configured dimension
    (1024) and batching inputs (8 per request). Transient failures are retried
    with the configured backoff; an exhausted retry budget raises
    ``EmbeddingError`` and a dimension mismatch raises ``EmbeddingDimensionError``
    without retrying. A ``client`` can be injected for offline testing so the
    network/SDK is only touched when no client is supplied.
    """

    provider = "bailian"

    def __init__(self, settings: Settings, *, client: EmbeddingClient | None = None) -> None:
        self.model = settings.embedding_model
        self.dimension = settings.embedding_dimension
        self.batch_size = max(1, settings.embedding_batch_size)
        self.max_retries = max(0, settings.embedding_max_retries)
        self.backoff = settings.embedding_retry_backoff or [1.0]
        self.timeout = settings.embedding_timeout_seconds
        self._api_key = settings.bailian_api_key
        self._base_url = settings.bailian_base_url.rstrip("/")
        self._client = client

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            batch_vectors = self._embed_with_retry(batch)
            if len(batch_vectors) != len(batch):
                raise EmbeddingError(
                    f"Embedding service returned {len(batch_vectors)} vectors for {len(batch)} inputs"
                )
            for vector in batch_vectors:
                if len(vector) != self.dimension:
                    raise EmbeddingDimensionError(
                        f"Embedding dimension {len(vector)} does not match configured {self.dimension}"
                    )
            vectors.extend(batch_vectors)
        return vectors

    def _embed_with_retry(self, batch: list[str]) -> list[list[float]]:
        last_error: Exception | None = None
        # One initial attempt plus up to max_retries retries.
        for attempt in range(self.max_retries + 1):
            try:
                return self._client_call(batch)
            except EmbeddingDimensionError:
                # Configuration error: surfacing immediately beats retrying.
                raise
            except Exception as exc:  # noqa: BLE001 - normalized into EmbeddingError below
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(self._backoff_for(attempt))
        raise EmbeddingError(f"Embedding request failed after {self.max_retries + 1} attempts: {last_error}")

    def _backoff_for(self, attempt: int) -> float:
        if not self.backoff:
            return 0.0
        return self.backoff[min(attempt, len(self.backoff) - 1)]

    def _client_call(self, batch: list[str]) -> list[list[float]]:
        client = self._client or self._default_client
        return client(batch)

    def _default_client(self, batch: list[str]) -> list[list[float]]:
        if not self._base_url:
            raise EmbeddingError(
                "embedding_provider=bailian requires BAILIAN_BASE_URL to be configured"
            )
        payload = json.dumps(
            {
                "model": self.model,
                "input": batch,
                "dimensions": self.dimension,
                "encoding_format": "float",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}/embeddings",
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Status only; the response body may echo the request and is not logged
            # to avoid leaking the API key or document text (spec 3.10).
            raise EmbeddingError(f"Embedding request failed with HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise EmbeddingError(f"Embedding request failed: {exc.reason}") from exc
        return _parse_embeddings(body)


def _parse_embeddings(body: dict) -> list[list[float]]:
    data = body.get("data")
    if not isinstance(data, list):
        raise EmbeddingError("Embedding response missing 'data' array")
    ordered = sorted(data, key=lambda item: item.get("index", 0))
    vectors: list[list[float]] = []
    for item in ordered:
        embedding = item.get("embedding")
        if not isinstance(embedding, list):
            raise EmbeddingError("Embedding response item missing 'embedding'")
        vectors.append([float(value) for value in embedding])
    return vectors


def build_embedding_provider(
    settings: Settings, *, client: EmbeddingClient | None = None
) -> EmbeddingProvider:
    """Select the embedding provider for the configured backend.

    Returns the Bailian adapter when ``embedding_provider=bailian`` and the
    deterministic local provider otherwise, mirroring ``build_file_storage`` so
    local development keeps its offline fallback (requirements 7.3).
    """
    if settings.embedding_provider == "bailian":
        return BailianEmbeddingProvider(settings, client=client)
    return DeterministicEmbeddingProvider()
