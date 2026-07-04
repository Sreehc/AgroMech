from __future__ import annotations

import base64
import hashlib
import json
import math
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Protocol

from agromech_api.core.config import Settings


VisualEmbeddingClient = Callable[[dict[str, object]], list[float]]


class VisualEmbeddingProvider(Protocol):
    provider: str
    model: str

    def embed_image(self, path: Path, *, text: str | None = None) -> list[float]: ...

    def embed_query(self, text: str) -> list[float]: ...


class VisualEmbeddingError(RuntimeError):
    """Raised when the visual embedding service cannot produce a vector."""


class VisualEmbeddingDimensionError(VisualEmbeddingError):
    """Raised when the returned vector dimension does not match configuration."""


class DeterministicVisualEmbeddingProvider:
    provider = "local"
    model = "deterministic-visual-token-hash"

    def __init__(self, *, dimension: int = 256) -> None:
        self.dimension = dimension

    def embed_image(self, path: Path, *, text: str | None = None) -> list[float]:
        payload = f"{path.name}\n{text or ''}\n{hashlib.sha256(path.read_bytes()).hexdigest()}"
        return deterministic_vector(payload, dimension=self.dimension)

    def embed_query(self, text: str) -> list[float]:
        return deterministic_vector(text, dimension=self.dimension)


class BailianVisualEmbeddingProvider:
    """DashScope native adapter for qwen3-vl-embedding multimodal vectors."""

    provider = "bailian"

    def __init__(self, settings: Settings, *, client: VisualEmbeddingClient | None = None) -> None:
        self.model = settings.visual_embedding_model
        self.dimension = settings.visual_embedding_dimension
        self.timeout = settings.embedding_timeout_seconds
        self.max_retries = max(0, settings.embedding_max_retries)
        self.backoff = settings.embedding_retry_backoff or [1.0]
        self._api_key = settings.bailian_api_key
        self._base_url = settings.dashscope_base_url.rstrip("/")
        self._client = client

    def embed_image(self, path: Path, *, text: str | None = None) -> list[float]:
        content: list[dict[str, object]] = [{"image": image_data_url(path)}]
        if text:
            content.append({"text": text})
        return self._embed({"input": {"contents": [{"content": content}]}})

    def embed_query(self, text: str) -> list[float]:
        return self._embed({"input": {"contents": [{"content": [{"text": text}]}]}})

    def _embed(self, payload: dict[str, object]) -> list[float]:
        full_payload = {
            "model": self.model,
            **payload,
            "parameters": {"dimension": self.dimension},
        }
        vector = self._embed_with_retry(full_payload)
        if len(vector) != self.dimension:
            raise VisualEmbeddingDimensionError(
                f"Visual embedding dimension {len(vector)} does not match configured {self.dimension}"
            )
        return vector

    def _embed_with_retry(self, payload: dict[str, object]) -> list[float]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                client = self._client or self._default_client
                return client(payload)
            except VisualEmbeddingDimensionError:
                raise
            except Exception as exc:  # noqa: BLE001 - normalized below
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(self.backoff[min(attempt, len(self.backoff) - 1)] if self.backoff else 0.0)
        raise VisualEmbeddingError(
            f"Visual embedding request failed after {self.max_retries + 1} attempts: {last_error}"
        )

    def _default_client(self, payload: dict[str, object]) -> list[float]:
        request = urllib.request.Request(
            f"{self._base_url}/services/embeddings/multimodal-embedding/multimodal-embedding",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
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
            raise VisualEmbeddingError(f"Visual embedding request failed with HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise VisualEmbeddingError(f"Visual embedding request failed: {exc.reason}") from exc
        return parse_visual_embedding_response(body)


def image_data_url(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    mime_type = "image/jpeg" if suffix in {"jpg", "jpeg"} else f"image/{suffix or 'png'}"
    return f"data:{mime_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def parse_visual_embedding_response(body: dict[str, object]) -> list[float]:
    output = body.get("output")
    embeddings = output.get("embeddings") if isinstance(output, dict) else None
    if isinstance(embeddings, list) and embeddings:
        first = embeddings[0]
        embedding = first.get("embedding") if isinstance(first, dict) else None
        if isinstance(embedding, list):
            return [float(value) for value in embedding]
    data = body.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        embedding = first.get("embedding") if isinstance(first, dict) else None
        if isinstance(embedding, list):
            return [float(value) for value in embedding]
    raise VisualEmbeddingError("Visual embedding response missing embedding vector")


def deterministic_vector(text: str, *, dimension: int) -> list[float]:
    vector = [0.0] * dimension
    for token in text.split():
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        vector[digest[0] % dimension] += 1.0
    if not any(vector):
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        for index, value in enumerate(digest[: min(len(digest), dimension)]):
            vector[index] = float(value) / 255.0
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def build_visual_embedding_provider(
    settings: Settings, *, client: VisualEmbeddingClient | None = None
) -> VisualEmbeddingProvider:
    if settings.visual_embedding_provider == "bailian":
        return BailianVisualEmbeddingProvider(settings, client=client)
    return DeterministicVisualEmbeddingProvider(dimension=settings.visual_embedding_dimension)
