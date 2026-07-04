from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Protocol

from agromech_api.core.config import Settings


RerankTransport = Callable[[urllib.request.Request, float], dict[str, object]]


class RerankProvider(Protocol):
    provider: str
    model: str

    def rerank(self, query: str, documents: list[str]) -> list[float]: ...


class RerankError(RuntimeError):
    """Raised when the rerank service cannot produce a usable ranking."""


class BailianRerankProvider:
    provider = "bailian"

    def __init__(
        self,
        settings: Settings,
        *,
        transport: RerankTransport | None = None,
    ) -> None:
        self.model = settings.rerank_model
        self.timeout = settings.rerank_timeout_seconds
        self.top_k = max(1, settings.rerank_top_k)
        self._api_key = settings.bailian_api_key
        self._base_url = settings.bailian_base_url.rstrip("/")
        self._transport = transport or self._default_transport

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        request = self._request(query, documents)
        try:
            body = self._transport(request, self.timeout)
        except RerankError:
            raise
        except urllib.error.HTTPError as exc:
            raise RerankError(f"Rerank request failed with HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RerankError(f"Rerank request failed: {exc.reason}") from exc
        except Exception as exc:  # noqa: BLE001 - normalized for retrieval degradation
            raise RerankError(f"Rerank request failed: {exc}") from exc
        return parse_bailian_rerank_response(body, expected_count=len(documents))

    def _request(self, query: str, documents: list[str]) -> urllib.request.Request:
        if not self._base_url:
            raise RerankError("model_provider=bailian requires BAILIAN_BASE_URL to be configured")
        payload = json.dumps(
            {
                "model": self.model,
                "query": query,
                "documents": documents,
                "top_n": min(len(documents), self.top_k),
            }
        ).encode("utf-8")
        return urllib.request.Request(
            f"{self._base_url}/rerank",
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

    def _default_transport(
        self,
        request: urllib.request.Request,
        timeout: float,
    ) -> dict[str, object]:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))


def parse_bailian_rerank_response(body: dict[str, object], *, expected_count: int) -> list[float]:
    results = body.get("results")
    if not isinstance(results, list):
        raise RerankError("Rerank response missing results")

    scores = [0.0] * expected_count
    for result in results:
        if not isinstance(result, dict):
            raise RerankError("Rerank response result is invalid")
        index = result.get("index")
        relevance_score = result.get("relevance_score")
        if not isinstance(index, int) or index < 0 or index >= expected_count:
            raise RerankError("Rerank response result has invalid index")
        if relevance_score is None:
            raise RerankError("Rerank response result missing relevance_score")
        scores[index] = float(relevance_score)
    return scores


def build_rerank_provider(
    settings: Settings,
    *,
    transport: RerankTransport | None = None,
) -> RerankProvider | None:
    if settings.model_provider == "bailian" and settings.rerank_enabled:
        return BailianRerankProvider(settings, transport=transport)
    return None
