from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import RunnableLambda
from pydantic import ConfigDict, Field


class ProviderEmbeddings(Embeddings):
    """LangChain embeddings adapter for AgroMech embedding providers.

    The wrapped provider keeps the existing project ``embed`` contract while
    exposing LangChain's ``embed_query`` and ``embed_documents`` methods.
    """

    def __init__(self, provider: Any) -> None:
        self.provider = getattr(provider, "provider", "unknown")
        self.model = getattr(provider, "model", "unknown")
        self._provider = provider

    def embed_query(self, text: str) -> list[float]:
        return self.embed(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if hasattr(self._provider, "embed_batch"):
            return self._provider.embed_batch(texts)
        return [self.embed(text) for text in texts]

    def embed(self, text: str) -> list[float]:
        return [float(value) for value in self._provider.embed(text)]


class VisualProviderEmbeddings(Embeddings):
    """LangChain embeddings adapter for visual-page query embeddings."""

    def __init__(self, provider: Any) -> None:
        self.provider = getattr(provider, "provider", "unknown")
        self.model = getattr(provider, "model", "unknown")
        self._provider = provider

    def embed_query(self, text: str) -> list[float]:
        return [float(value) for value in self._provider.embed_query(text)]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]


class AgroMechTextRetriever(BaseRetriever):
    """LangChain retriever wrapper for the existing text retrieval payload."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    engine: Any
    retrieve_payload_fn: Callable[..., dict[str, Any]]
    filters: dict[str, str | None] = Field(default_factory=dict)
    trace_id: str | None = None
    route: dict[str, Any] = Field(default_factory=dict)
    image_context: dict[str, Any] | None = None

    def retrieve_payload(self, query: str, **overrides: Any) -> dict[str, Any]:
        return self.retrieve_payload_fn(
            engine=overrides.get("engine", self.engine),
            question=query,
            filters=overrides.get("filters", self.filters),
            trace_id=overrides.get("trace_id", self.trace_id),
            route=overrides.get("route", self.route),
            image_context=overrides.get("image_context", self.image_context),
        )

    def _get_relevant_documents(self, query: str, *, run_manager: Any) -> list[Document]:
        _ = run_manager
        payload = self.retrieve_payload(query)
        return [text_evidence_to_document(item) for item in payload.get("final_evidence", [])]


class AgroMechVisualPageRetriever(BaseRetriever):
    """LangChain retriever wrapper for visual-page retrieval."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    engine: Any
    retrieve_payload_fn: Callable[..., dict[str, Any]]
    filters: dict[str, str | None] = Field(default_factory=dict)
    trace_id: str | None = None
    route: dict[str, Any] = Field(default_factory=dict)
    image_context: dict[str, Any] | None = None
    planner: dict[str, Any] = Field(default_factory=dict)

    def retrieve_payload(self, query: str, **overrides: Any) -> dict[str, Any]:
        return self.retrieve_payload_fn(
            engine=overrides.get("engine", self.engine),
            question=query,
            filters=overrides.get("filters", self.filters),
            trace_id=overrides.get("trace_id", self.trace_id),
            route=overrides.get("route", self.route),
            image_context=overrides.get("image_context", self.image_context),
            planner=overrides.get("planner", self.planner),
        )

    def _get_relevant_documents(self, query: str, *, run_manager: Any) -> list[Document]:
        _ = run_manager
        payload = self.retrieve_payload(query)
        return [visual_evidence_to_document(item) for item in payload.get("final_evidence", [])]


def text_evidence_to_document(item: dict[str, Any]) -> Document:
    return Document(
        page_content=str(item.get("content") or ""),
        metadata=without_none(
            {
                "evidence_type": item.get("evidence_type") or item.get("chunk_type") or "text",
                "chunk_id": item.get("chunk_id"),
                "document_id": item.get("document_id"),
                "chunk_type": item.get("chunk_type"),
                "source_locator": item.get("source_locator"),
                "score": item.get("score"),
                "vector_ref": item.get("vector_ref"),
            }
        ),
    )


def visual_evidence_to_document(item: dict[str, Any]) -> Document:
    return Document(
        page_content=str(item.get("ocr_text") or item.get("visual_observation") or item.get("image_uri") or ""),
        metadata=without_none(
            {
                "evidence_type": item.get("evidence_type", "visual_page"),
                "asset_id": item.get("asset_id"),
                "document_id": item.get("document_id"),
                "page_number": item.get("page_number"),
                "source_locator": item.get("source_locator"),
                "image_uri": item.get("image_uri"),
                "score": item.get("score"),
                "vector_ref": item.get("vector_ref"),
            }
        ),
    )


def without_none(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def build_answer_chain(answer_generator: Any):
    """Build an LCEL-compatible answer chain over the configured generator."""

    def invoke(payload: dict[str, Any]) -> dict[str, Any]:
        return answer_generator.generate(
            question=payload["question"],
            citations=list(payload.get("citations") or []),
            safety_warnings=list(payload.get("safety_warnings") or []),
            uncertainty=dict(payload.get("uncertainty") or {}),
            filters=dict(payload.get("filters") or {}),
        )

    return RunnableLambda(invoke)
