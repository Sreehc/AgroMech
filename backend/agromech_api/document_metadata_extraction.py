from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import Engine, select, update

from agromech_api.config import Settings, get_settings
from agromech_api.db.models import document_chunks, documents


LOGGER = logging.getLogger("agromech.metadata_extraction")
METADATA_FIELDS = ("brand", "model", "document_type", "language", "source")
MAX_CONTEXT_CHARS = 12000
MIN_CONFIDENCE = 0.60

MetadataTransport = Callable[[urllib.request.Request, float], dict[str, object]]


@dataclass(frozen=True)
class DocumentMetadataContext:
    document_id: str
    title: str
    original_file_name: str
    mime_type: str
    existing_metadata: dict[str, str | None]
    context_text: str


@dataclass(frozen=True)
class MetadataBackfillResult:
    updated_fields: dict[str, str]
    skipped: bool
    error: str | None = None


class DocumentMetadataExtractor(Protocol):
    def extract(self, context: DocumentMetadataContext) -> dict[str, object]: ...


class MetadataExtractionError(RuntimeError):
    """Raised when the metadata model cannot return usable strict JSON."""


class BailianDocumentMetadataExtractor:
    def __init__(self, settings: Settings, *, transport: MetadataTransport | None = None) -> None:
        self.model = settings.llm_model
        self.timeout = settings.llm_request_timeout_seconds
        self._api_key = settings.bailian_api_key
        self._base_url = settings.bailian_base_url.rstrip("/")
        self._transport = transport or self._default_transport

    def extract(self, context: DocumentMetadataContext) -> dict[str, object]:
        request = self._request(context)
        try:
            body = self._transport(request, self.timeout)
        except MetadataExtractionError:
            raise
        except urllib.error.HTTPError as exc:
            raise MetadataExtractionError(f"Metadata LLM request failed with HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise MetadataExtractionError("Metadata LLM request failed: upstream unavailable") from exc
        except Exception as exc:  # noqa: BLE001 - normalize external model errors.
            raise MetadataExtractionError(f"Metadata LLM request failed: {exc}") from exc
        return parse_metadata_response(body)

    def _request(self, context: DocumentMetadataContext) -> urllib.request.Request:
        if not self._base_url:
            raise MetadataExtractionError("model_provider=bailian requires BAILIAN_BASE_URL to be configured")

        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You extract document-level metadata for agricultural machinery manuals. "
                            "Use only the supplied filename, title, OCR/text chunks, and visual observations. "
                            "Return strict JSON. Do not invent values. If evidence is insufficient, use null. "
                            "The language field must be a BCP-47-like code such as zh-CN or en."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "Return JSON with keys: brand, model, document_type, language, source, "
                            "confidence, evidence.\n"
                            f"Document id: {context.document_id}\n"
                            f"Title: {context.title}\n"
                            f"Original filename: {context.original_file_name}\n"
                            f"MIME type: {context.mime_type}\n"
                            f"Existing metadata: {json.dumps(context.existing_metadata, ensure_ascii=False)}\n"
                            "Context:\n"
                            f"{context.context_text}"
                        ),
                    },
                ],
                "response_format": {"type": "json_object"},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        return urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

    def _default_transport(self, request: urllib.request.Request, timeout: float) -> dict[str, object]:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))


def build_metadata_extractor(settings: Settings) -> DocumentMetadataExtractor | None:
    if settings.model_provider == "bailian":
        return BailianDocumentMetadataExtractor(settings)
    return None


def backfill_document_metadata(
    engine: Engine,
    document_id: str,
    *,
    extractor: DocumentMetadataExtractor | None = None,
) -> MetadataBackfillResult:
    context = build_metadata_context(engine, document_id)
    missing_fields = [field for field in METADATA_FIELDS if not context.existing_metadata.get(field)]
    if not missing_fields:
        return MetadataBackfillResult(updated_fields={}, skipped=True)

    active_extractor = extractor or build_metadata_extractor(get_settings())
    if active_extractor is None:
        return MetadataBackfillResult(updated_fields={}, skipped=True, error="metadata_extractor_unavailable")

    try:
        extracted = active_extractor.extract(context)
    except MetadataExtractionError as exc:
        LOGGER.warning("Document metadata extraction failed: document_id=%s error=%s", document_id, exc)
        return MetadataBackfillResult(updated_fields={}, skipped=True, error=str(exc))

    values = validated_metadata(extracted, missing_fields)
    if not values:
        return MetadataBackfillResult(updated_fields={}, skipped=True)

    with engine.begin() as connection:
        connection.execute(
            update(documents)
            .where(documents.c.id == document_id)
            .values(**values)
        )
    return MetadataBackfillResult(updated_fields=values, skipped=False)


def build_metadata_context(engine: Engine, document_id: str) -> DocumentMetadataContext:
    with engine.connect() as connection:
        document = connection.execute(
            select(
                documents.c.id,
                documents.c.title,
                documents.c.original_file_name,
                documents.c.mime_type,
                documents.c.brand,
                documents.c.model,
                documents.c.document_type,
                documents.c.language,
                documents.c.source,
            ).where(documents.c.id == document_id)
        ).mappings().one()
        chunks = connection.execute(
            select(
                document_chunks.c.content,
                document_chunks.c.summary,
                document_chunks.c.section_title,
                document_chunks.c.page_number,
                document_chunks.c.metadata,
            )
            .where(document_chunks.c.document_id == document_id)
            .order_by(document_chunks.c.page_number, document_chunks.c.created_at)
            .limit(20)
        ).mappings().all()

    parts = []
    for index, chunk in enumerate(chunks, start=1):
        parts.append(
            "\n".join(
                part
                for part in [
                    f"[chunk {index}]",
                    f"page: {chunk['page_number']}" if chunk["page_number"] is not None else "",
                    f"section: {chunk['section_title']}" if chunk["section_title"] else "",
                    str(chunk["content"] or ""),
                    f"summary: {chunk['summary']}" if chunk["summary"] else "",
                    f"metadata: {json.dumps(chunk['metadata'], ensure_ascii=False)}"
                    if isinstance(chunk["metadata"], dict)
                    else "",
                ]
                if part
            )
        )
    context_text = "\n\n".join(parts)[:MAX_CONTEXT_CHARS]
    return DocumentMetadataContext(
        document_id=document["id"],
        title=document["title"],
        original_file_name=document["original_file_name"],
        mime_type=document["mime_type"],
        existing_metadata={field: document[field] for field in METADATA_FIELDS},
        context_text=context_text,
    )


def parse_metadata_response(body: dict[str, object]) -> dict[str, object]:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise MetadataExtractionError("Metadata LLM response missing choices")
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise MetadataExtractionError("Metadata LLM response choice is invalid")
    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise MetadataExtractionError("Metadata LLM response missing message")
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    if not isinstance(content, str) or not content.strip():
        raise MetadataExtractionError("Metadata LLM response missing content")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise MetadataExtractionError("Metadata LLM response content is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise MetadataExtractionError("Metadata LLM response JSON must be an object")
    return parsed


def validated_metadata(payload: dict[str, object], allowed_fields: list[str]) -> dict[str, str]:
    confidence = payload.get("confidence")
    if isinstance(confidence, int | float) and float(confidence) < MIN_CONFIDENCE:
        return {}

    values: dict[str, str] = {}
    for field in allowed_fields:
        value = payload.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if normalized:
            values[field] = normalized[:255]
    return values
