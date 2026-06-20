from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import Engine, delete, insert, select

from agromech_api.config import get_settings
from agromech_api.db.models import chunk_search_index, document_chunks, embedding_references
from agromech_api.ingestion import IngestFailure


TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+|[\u4e00-\u9fff]+")


@dataclass(frozen=True)
class IndexResult:
    chunk_count: int


class DeterministicEmbeddingProvider:
    provider = "local"
    model = "deterministic-token-hash"

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * 256
        for token in tokenize(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = digest[0] % len(vector)
            vector[index] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


class FailingEmbeddingProvider:
    provider = "local"
    model = "failing"

    def embed(self, text: str) -> list[float]:
        raise RuntimeError("Embedding service unavailable")


class LocalVectorStore:
    name = "milvus"

    def upsert(self, *, collection: str, chunk_id: str, embedding: list[float]) -> str:
        digest = hashlib.sha256(f"{collection}:{chunk_id}:{embedding}".encode("utf-8")).hexdigest()
        return f"{collection}:{digest[:24]}"


class SearchIndexer:
    def __init__(
        self,
        engine: Engine,
        *,
        embedding_provider=None,
        vector_store=None,
        collection: str | None = None,
    ) -> None:
        settings = get_settings()
        self.engine = engine
        self.embedding_provider = embedding_provider or DeterministicEmbeddingProvider()
        self.vector_store = vector_store or LocalVectorStore()
        self.collection = collection or settings.milvus_collection

    def index_document(self, document_id: str) -> IndexResult:
        with self.engine.connect() as connection:
            chunks = connection.execute(
                select(document_chunks).where(document_chunks.c.document_id == document_id)
            ).mappings().all()

        search_rows = []
        embedding_rows = []
        for chunk in chunks:
            search_text = searchable_text(chunk)
            if not search_text.strip():
                continue
            try:
                embedding = self.embedding_provider.embed(search_text)
            except Exception as exc:
                raise IngestFailure("embedding_failed", str(exc), stage="index") from exc
            try:
                vector_id = self.vector_store.upsert(
                    collection=self.collection,
                    chunk_id=chunk["id"],
                    embedding=embedding,
                )
            except Exception as exc:
                raise IngestFailure("vector_index_failed", str(exc), stage="index") from exc

            search_rows.append(
                {
                    "id": str(uuid4()),
                    "chunk_id": chunk["id"],
                    "document_id": document_id,
                    "chunk_type": chunk["chunk_type"],
                    "search_text": search_text,
                    "embedding": embedding,
                }
            )
            embedding_rows.append(
                {
                    "id": str(uuid4()),
                    "chunk_id": chunk["id"],
                    "provider": self.embedding_provider.provider,
                    "model": self.embedding_provider.model,
                    "vector_store": self.vector_store.name,
                    "collection": self.collection,
                    "vector_id": vector_id,
                    "status": "ready",
                }
            )

        if chunks and not search_rows:
            raise IngestFailure("full_text_index_failed", "No searchable chunk content", stage="index")

        with self.engine.begin() as connection:
            chunk_ids = [chunk["id"] for chunk in chunks]
            if chunk_ids:
                connection.execute(
                    delete(chunk_search_index).where(chunk_search_index.c.chunk_id.in_(chunk_ids))
                )
                connection.execute(
                    delete(embedding_references).where(embedding_references.c.chunk_id.in_(chunk_ids))
                )
            if search_rows:
                connection.execute(insert(chunk_search_index), search_rows)
            if embedding_rows:
                connection.execute(insert(embedding_references), embedding_rows)
        return IndexResult(chunk_count=len(search_rows))


def searchable_text(chunk) -> str:
    parts = [
        chunk["content"] or "",
        chunk["summary"] or "",
        chunk["section_title"] or "",
        chunk["worksheet_name"] or "",
    ]
    metadata = chunk["metadata"] or {}
    if isinstance(metadata, dict):
        parts.append(str(metadata))
    source_locator = chunk["source_locator"] or {}
    if isinstance(source_locator, dict):
        parts.append(str(source_locator))
    return "\n".join(part for part in parts if part)


def keyword_search(engine: Engine, query: str, *, limit: int = 10) -> list[dict[str, object]]:
    query_tokens = tokenize(query)
    with engine.connect() as connection:
        rows = connection.execute(select(chunk_search_index)).mappings().all()
    scored = []
    for row in rows:
        score = token_score(query_tokens, row["search_text"])
        if score > 0:
            scored.append({"chunk_id": row["chunk_id"], "score": score, "chunk_type": row["chunk_type"]})
    return sorted(scored, key=lambda item: item["score"], reverse=True)[:limit]


def vector_search(engine: Engine, query: str, *, limit: int = 10) -> list[dict[str, object]]:
    provider = DeterministicEmbeddingProvider()
    query_embedding = provider.embed(query)
    with engine.connect() as connection:
        rows = connection.execute(select(chunk_search_index)).mappings().all()
    scored = []
    for row in rows:
        score = cosine_similarity(query_embedding, row["embedding"])
        if score > 0:
            scored.append({"chunk_id": row["chunk_id"], "score": score, "chunk_type": row["chunk_type"]})
    return sorted(scored, key=lambda item: item["score"], reverse=True)[:limit]


def tokenize(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text or "")]


def token_score(query_tokens: list[str], text: str) -> int:
    text_tokens = set(tokenize(text))
    return sum(1 for token in query_tokens if token in text_tokens)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    return sum(a * b for a, b in zip(left, right))
