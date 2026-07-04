from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import RunnableLambda
from langchain_core.vectorstores import VectorStore
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


class ZvecLangChainVectorStore(VectorStore):
    """LangChain VectorStore wrapper over the existing Zvec adapter."""

    def __init__(
        self,
        zvec_store: Any,
        *,
        collection: str,
        embedding: Embeddings,
        document_lookup: Callable[[str], Document | None] | None = None,
    ) -> None:
        self.zvec_store = zvec_store
        self.collection = collection
        self.embedding = embedding
        self.document_lookup = document_lookup
        self.name = getattr(zvec_store, "name", "zvec")

    def add_texts(
        self,
        texts: Iterable[str],
        metadatas: list[dict[str, Any]] | None = None,
        *,
        ids: list[str] | None = None,
        **_kwargs: Any,
    ) -> list[str]:
        text_list = list(texts)
        metadatas = metadatas or [{} for _ in text_list]
        ids = ids or [
            str(metadata.get("id") or metadata.get("chunk_id") or index)
            for index, metadata in enumerate(metadatas)
        ]
        vectors = self.embedding.embed_documents(text_list)
        vector_refs = []
        for item_id, vector in zip(ids, vectors, strict=True):
            vector_refs.append(
                self.zvec_store.upsert(
                    collection=self.collection,
                    chunk_id=str(item_id),
                    embedding=vector,
                )
            )
        return vector_refs

    def similarity_search(self, query: str, k: int = 4, **kwargs: Any) -> list[Document]:
        collection = str(kwargs.get("collection") or self.collection)
        query_embedding = self.embedding.embed_query(query)
        results = self.zvec_store.query(collection=collection, embedding=query_embedding, limit=k)
        return [self._document_from_result(result) for result in results]

    @classmethod
    def from_texts(
        cls,
        texts: list[str],
        embedding: Embeddings,
        metadatas: list[dict[str, Any]] | None = None,
        *,
        ids: list[str] | None = None,
        **kwargs: Any,
    ) -> "ZvecLangChainVectorStore":
        zvec_store = kwargs["zvec_store"]
        collection = kwargs["collection"]
        document_lookup = kwargs.get("document_lookup")
        vector_store = cls(
            zvec_store,
            collection=collection,
            embedding=embedding,
            document_lookup=document_lookup,
        )
        vector_store.add_texts(texts, metadatas=metadatas, ids=ids)
        return vector_store

    def upsert(self, *, collection: str, chunk_id: str, embedding: list[float]) -> str:
        return self.zvec_store.upsert(collection=collection, chunk_id=chunk_id, embedding=embedding)

    def query(self, *, collection: str, embedding: list[float], limit: int = 10) -> list[dict[str, object]]:
        return self.zvec_store.query(collection=collection, embedding=embedding, limit=limit)

    def delete(self, *, collection: str, chunk_ids: list[str]) -> None:
        self.zvec_store.delete(collection=collection, chunk_ids=chunk_ids)

    def _document_from_result(self, result: dict[str, Any]) -> Document:
        chunk_id = str(result["chunk_id"])
        if self.document_lookup is not None:
            document = self.document_lookup(chunk_id)
            if document is not None:
                return document
        return Document(
            page_content="",
            metadata={
                "chunk_id": chunk_id,
                "score": result.get("score"),
                "vector_ref": result.get("vector_ref"),
            },
        )


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


def build_text_vector_components(settings: Any) -> tuple[Embeddings | None, Any | None, str | None]:
    """Build LangChain-wrapped text embedding/vector components for configured Zvec."""

    if settings.vector_backend != "zvec":
        return None, None, None
    from agromech_api.integrations.embeddings.text import build_embedding_provider
    from agromech_api.integrations.vectorstores.zvec import build_vector_store

    embeddings = ProviderEmbeddings(build_embedding_provider(settings))
    native_store = build_vector_store(settings, expected_dimension=settings.embedding_dimension)
    collection = active_text_collection(settings)
    return (
        embeddings,
        ZvecLangChainVectorStore(
            native_store,
            collection=collection,
            embedding=embeddings,
        ),
        collection,
    )


def build_visual_vector_components(settings: Any) -> tuple[Embeddings | None, Any | None, str | None]:
    """Build LangChain-wrapped visual query embedding/vector components."""

    if settings.vector_backend != "zvec":
        return None, None, None
    from agromech_api.integrations.embeddings.visual import build_visual_embedding_provider
    from agromech_api.integrations.vectorstores.zvec import build_vector_store

    embeddings = VisualProviderEmbeddings(build_visual_embedding_provider(settings))
    native_store = build_vector_store(settings, expected_dimension=settings.visual_embedding_dimension)
    return (
        embeddings,
        ZvecLangChainVectorStore(
            native_store,
            collection=settings.zvec_visual_collection,
            embedding=embeddings,
        ),
        settings.zvec_visual_collection,
    )


def active_text_collection(settings: Any) -> str:
    if settings.zvec_collection != "agromech_chunks":
        return settings.zvec_collection
    return settings.zvec_text_collection
