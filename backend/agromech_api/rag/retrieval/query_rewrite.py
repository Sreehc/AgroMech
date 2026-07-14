from __future__ import annotations

import json
import re
import time
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any
from typing import Protocol

from agromech_api.core.config import Settings
from agromech_api.rag.retrieval.query_understanding import (
    DOCUMENT_TYPE_TERMS,
    PART_NUMBER_RE,
    YEAR_RE,
    ParsedQuery,
    parse_query,
)


RewriteTransport = Callable[[urllib.request.Request, float], dict[str, object]]
LANGUAGE_TAG_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]{2,3}-[A-Za-z]{2}(?:-[A-Za-z0-9]{1,8})*)(?![A-Za-z0-9-])",
    re.IGNORECASE,
)
IDENTIFIER_CATEGORIES = (
    "model",
    "fault_code",
    "part_number",
    "document_version",
    "language",
    "document_type",
)


class _IdentifierValidationError(ValueError):
    pass


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
    attempted_provider: str | None = None
    attempted_model: str | None = None

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
    groups = _source_identifier_groups(parsed, request_filters)
    return _flatten_identifier_groups(groups)


def _flatten_identifier_groups(groups: dict[str, list[str]]) -> list[str]:
    return list(
        dict.fromkeys(
            value
            for category in IDENTIFIER_CATEGORIES
            for value in groups[category]
            if value
        )
    )


def _source_identifier_groups(
    parsed: ParsedQuery,
    request_filters: dict[str, str | None],
) -> dict[str, list[str]]:
    groups = {
        "model": _parsed_models(parsed),
        "fault_code": [str(value).strip() for value in parsed.entities.get("fault_code", [])],
        "part_number": [str(value).strip() for value in parsed.entities.get("part_number", [])],
        "document_version": [],
        "language": [],
        "document_type": [],
    }
    request_model = str(request_filters.get("model") or "").strip()
    if request_model:
        groups["model"].append(request_model)
    for category in ("document_version", "language", "document_type"):
        for value in (parsed.filters.get(category), request_filters.get(category)):
            normalized = str(value).strip() if value is not None else ""
            if normalized:
                groups[category].append(normalized)
    return {category: list(dict.fromkeys(values)) for category, values in groups.items()}


def _parsed_models(parsed: ParsedQuery) -> list[str]:
    candidates: list[str] = []
    model = parsed.filters.get("model")
    models = parsed.filters.get("models")
    if model:
        candidates.append(str(model).strip())
    if isinstance(models, list):
        candidates.extend(str(value).strip() for value in models)
    candidates.extend(str(value).strip() for value in parsed.entities.get("model", []))

    excluded = {
        _normalize_identifier("model", str(value))
        for category in ("fault_code", "part_number")
        for value in parsed.entities.get(category, [])
    }
    language_regions = [
        match.group(0).rsplit("-", 1)[-1]
        for match in LANGUAGE_TAG_RE.finditer(parsed.original_query)
    ]
    versions = [match.group(0) for match in YEAR_RE.finditer(parsed.original_query)]
    excluded.update(_normalize_identifier("model", version) for version in versions)
    excluded.update(
        _normalize_identifier("model", f"{region}{version}")
        for region in language_regions
        for version in versions
    )

    values: list[str] = []
    for value in candidates:
        if _normalize_identifier("model", value) not in excluded:
            values.append(value)
    return list(dict.fromkeys(value for value in values if value))


def _extract_identifier_groups(query: str) -> dict[str, list[str]]:
    parsed = parse_query(query)
    part_numbers = [str(value).strip() for value in parsed.entities.get("part_number", [])]
    part_numbers.extend(match.group(0) for match in PART_NUMBER_RE.finditer(query))
    document_types = []
    for value in DOCUMENT_TYPE_TERMS:
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_]){re.escape(value)}(?![A-Za-z0-9_])",
            re.IGNORECASE,
        )
        document_types.extend(match.group(0) for match in pattern.finditer(query))
    return {
        "model": _parsed_models(parsed),
        "fault_code": [str(value).strip() for value in parsed.entities.get("fault_code", [])],
        "part_number": list(dict.fromkeys(part_numbers)),
        "document_version": [match.group(0) for match in YEAR_RE.finditer(query)],
        "language": [match.group(0) for match in LANGUAGE_TAG_RE.finditer(query)],
        "document_type": list(dict.fromkeys(document_types)),
    }


def _normalize_identifier(category: str, value: str) -> str:
    normalized = value.strip()
    if category == "model":
        return re.sub(r"[-\s]+", "", normalized).upper()
    if category in {"fault_code", "part_number"}:
        return re.sub(r"\s+", "", normalized).upper()
    return normalized.lower()


def _validate_rewrite_identifiers(
    expected: dict[str, list[str]],
    rewritten: str,
) -> None:
    actual = _extract_identifier_groups(rewritten)
    normalized_actual = {
        category: {_normalize_identifier(category, value) for value in actual[category]}
        for category in IDENTIFIER_CATEGORIES
    }
    for category in IDENTIFIER_CATEGORIES:
        for value in expected[category]:
            if _normalize_identifier(category, value) not in normalized_actual[category]:
                raise _IdentifierValidationError(f"protected_identifier_missing:{value}")

    for category in IDENTIFIER_CATEGORIES:
        normalized_expected = {
            _normalize_identifier(category, value) for value in expected[category]
        }
        for value in actual[category]:
            if _normalize_identifier(category, value) not in normalized_expected:
                raise _IdentifierValidationError(f"protected_identifier_added:{value}")


def rewrite_query(
    *,
    question: str,
    parsed: ParsedQuery,
    request_filters: dict[str, str | None],
    provider: QueryRewriteProvider | None,
    supplemental: bool,
) -> QueryRewriteResult:
    started = time.perf_counter()
    expected_identifiers = _source_identifier_groups(parsed, request_filters)
    protected = _flatten_identifier_groups(expected_identifiers)
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
    attempted_provider = None
    attempted_model = None
    try:
        attempted_provider = provider.provider
        attempted_model = provider.model
        rewritten = provider.rewrite(question, protected)
        _validate_rewrite_identifiers(expected_identifiers, rewritten)
        return QueryRewriteResult(
            question,
            rewritten,
            attempted_provider,
            attempted_model,
            False,
            "model_rewrite",
            protected,
            (time.perf_counter() - started) * 1000,
            attempted_provider,
            attempted_model,
        )
    except Exception as exc:  # noqa: BLE001 - rewrite degrades to deterministic rules
        fallback = rewrite_query_for_evidence(question=question, filters=request_filters, missing=[])
        reason = str(exc) if isinstance(exc, _IdentifierValidationError) else "provider_error"
        return QueryRewriteResult(
            question,
            fallback["query"],
            "rule",
            None,
            True,
            reason,
            protected,
            (time.perf_counter() - started) * 1000,
            attempted_provider,
            attempted_model,
        )


def build_query_rewrite_provider(settings: Settings) -> QueryRewriteProvider | None:
    if settings.query_rewrite_enabled and settings.model_provider == "bailian":
        return BailianQueryRewriteProvider(settings)
    return None
