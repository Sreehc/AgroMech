from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any
from typing import Protocol

from agromech_api.core.config import Settings
from agromech_api.rag.retrieval.query_understanding import ParsedQuery


RewriteTransport = Callable[[urllib.request.Request, float], dict[str, object]]


@dataclass(frozen=True)
class QueryRewriteResult:
    original_query: str
    query: str
    provider: str
    model: str | None
    fallback: bool
    reason: str
    protected_identifiers: list[str]
    duration_ms: float

    def to_trace(self) -> dict[str, object]:
        return asdict(self)


class QueryRewriteProvider(Protocol):
    provider: str
    model: str

    def rewrite(self, question: str, protected_identifiers: list[str]) -> str: ...


class BailianQueryRewriteProvider:
    provider = "bailian"

    def __init__(self, settings: Settings, *, transport: RewriteTransport | None = None) -> None:
        self.model = settings.query_rewrite_model
        self.timeout = settings.query_rewrite_timeout_seconds
        self._api_key = settings.bailian_api_key
        self._base_url = settings.bailian_base_url.rstrip("/")
        self._transport = transport or self._default_transport

    def rewrite(self, question: str, protected_identifiers: list[str]) -> str:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Rewrite one retrieval query as JSON {query:string}. Preserve every protected "
                        "identifier exactly. Do not answer the question.",
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"question": question, "protected_identifiers": protected_identifiers},
                            ensure_ascii=False,
                        ),
                    },
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        body = self._transport(request, self.timeout)
        content = body["choices"][0]["message"]["content"]
        query = json.loads(str(content))["query"]
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query rewrite response missing query")
        return query.strip()

    def _default_transport(self, request: urllib.request.Request, timeout: float) -> dict[str, object]:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))


DOMAIN_SYNONYMS = {
    "液压泵": ["hydraulic pump"],
    "异响": ["abnormal noise"],
    "故障码": ["fault code"],
    "保养": ["maintenance"],
    "更换": ["replace", "change"],
}


def rewrite_query_for_evidence(
    *,
    question: str,
    filters: dict[str, str | None],
    missing: list[str],
) -> dict[str, Any]:
    additions: list[str] = []
    for term, synonyms in DOMAIN_SYNONYMS.items():
        if term in question:
            additions.extend(synonym for synonym in synonyms if synonym not in additions)

    query = " ".join([question, *additions]).strip()
    return {
        "query": query,
        "filters": dict(filters),
        "missing": list(missing),
        "reason": "expanded domain synonyms for missing evidence",
    }


def protected_identifiers(parsed: ParsedQuery, request_filters: dict[str, str | None]) -> list[str]:
    values: list[str] = []
    model = parsed.filters.get("model")
    models = parsed.filters.get("models")
    if model:
        values.append(str(model).strip())
    elif isinstance(models, list):
        values.extend(str(value).strip() for value in models)
    values.extend(str(value) for value in parsed.entities.get("fault_code", []))
    values.extend(str(value) for value in parsed.entities.get("part_number", []))
    for key in ("model", "document_version", "language", "document_type"):
        value = request_filters.get(key) or parsed.filters.get(key)
        if value:
            values.append(str(value).strip())
    return list(dict.fromkeys(value for value in values if value))


def rewrite_query(
    *,
    question: str,
    parsed: ParsedQuery,
    request_filters: dict[str, str | None],
    provider: QueryRewriteProvider | None,
    supplemental: bool,
) -> QueryRewriteResult:
    started = time.perf_counter()
    protected = protected_identifiers(parsed, request_filters)
    if supplemental or provider is None:
        fallback = rewrite_query_for_evidence(question=question, filters=request_filters, missing=[])
        return QueryRewriteResult(
            question,
            fallback["query"],
            "rule",
            None,
            True,
            "supplemental" if supplemental else "provider_unavailable",
            protected,
            (time.perf_counter() - started) * 1000,
        )
    try:
        rewritten = provider.rewrite(question, protected)
        missing = next((value for value in protected if value.lower() not in rewritten.lower()), None)
        if missing:
            raise ValueError(f"protected_identifier_missing:{missing}")
        return QueryRewriteResult(
            question,
            rewritten,
            provider.provider,
            provider.model,
            False,
            "model_rewrite",
            protected,
            (time.perf_counter() - started) * 1000,
        )
    except Exception as exc:  # noqa: BLE001 - rewrite degrades to deterministic rules
        fallback = rewrite_query_for_evidence(question=question, filters=request_filters, missing=[])
        reason = str(exc) if str(exc).startswith("protected_identifier_missing:") else "provider_error"
        return QueryRewriteResult(
            question,
            fallback["query"],
            "rule",
            None,
            True,
            reason,
            protected,
            (time.perf_counter() - started) * 1000,
        )


def build_query_rewrite_provider(settings: Settings) -> QueryRewriteProvider | None:
    if settings.query_rewrite_enabled and settings.model_provider == "bailian":
        return BailianQueryRewriteProvider(settings)
    return None
