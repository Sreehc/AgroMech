from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import Engine, delete, insert, or_, select

from agromech_api.core.config import get_settings
from agromech_api.db.enums import AssetType
from agromech_api.db.models import (
    chunk_search_index,
    document_assets,
    document_chunks,
    documents,
    embedding_references,
    visual_page_embeddings,
)
from agromech_api.ingestion.runner import IngestFailure
from agromech_api.integrations.embeddings.visual import DeterministicVisualEmbeddingProvider


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
        embedding_version: str | None = None,
        chunk_profile: str | None = None,
        embedding_dimension: int | None = None,
    ) -> None:
        settings = get_settings()
        self.engine = engine
        self.embedding_provider = embedding_provider or DeterministicEmbeddingProvider()
        self.vector_store = vector_store or LocalVectorStore()
        self.collection = collection or settings.zvec_text_collection
        self.embedding_version = embedding_version or settings.embedding_version
        self.chunk_profile = chunk_profile or settings.chunk_profile
        self.embedding_dimension = embedding_dimension or settings.embedding_dimension

    def index_document(self, document_id: str) -> IndexResult:
        with self.engine.connect() as connection:
            document_title = connection.execute(
                select(documents.c.title).where(documents.c.id == document_id)
            ).scalar_one()
            chunks = connection.execute(
                select(document_chunks).where(document_chunks.c.document_id == document_id)
            ).mappings().all()

        search_rows = []
        embedding_rows = []
        for chunk in chunks:
            search_text = searchable_text(chunk, document_title=document_title)
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
                    "embedding_version": self.embedding_version,
                    "chunk_profile": self.chunk_profile,
                    "embedding_dimension": len(embedding),
                }
            )
            embedding_rows.append(
                {
                    "id": str(uuid4()),
                    "chunk_id": chunk["id"],
                    "provider": self.embedding_provider.provider,
                    "model": self.embedding_provider.model,
                    "embedding_version": self.embedding_version,
                    "chunk_profile": self.chunk_profile,
                    "embedding_dimension": len(embedding),
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
                    delete(chunk_search_index).where(
                        chunk_search_index.c.chunk_id.in_(chunk_ids),
                        chunk_search_index.c.embedding_version == self.embedding_version,
                    )
                )
                connection.execute(
                    delete(embedding_references).where(
                        embedding_references.c.chunk_id.in_(chunk_ids),
                        embedding_references.c.embedding_version == self.embedding_version,
                    )
                )
            if search_rows:
                connection.execute(insert(chunk_search_index), search_rows)
            if embedding_rows:
                connection.execute(insert(embedding_references), embedding_rows)
        return IndexResult(chunk_count=len(search_rows))


class VisualPageIndexer:
    def __init__(
        self,
        engine: Engine,
        *,
        embedding_provider=None,
        vector_store=None,
        collection: str | None = None,
        embedding_version: str | None = None,
        embedding_dimension: int | None = None,
    ) -> None:
        settings = get_settings()
        self.engine = engine
        self.embedding_provider = embedding_provider or DeterministicVisualEmbeddingProvider(
            dimension=settings.visual_embedding_dimension
        )
        self.vector_store = vector_store or LocalVectorStore()
        self.collection = collection or settings.zvec_visual_collection
        self.embedding_version = embedding_version or settings.visual_embedding_version
        self.embedding_dimension = embedding_dimension or settings.visual_embedding_dimension

    def index_document(self, document_id: str) -> IndexResult:
        with self.engine.connect() as connection:
            assets = connection.execute(
                select(document_assets)
                .where(document_assets.c.document_id == document_id)
                .where(document_assets.c.asset_type == AssetType.PAGE_IMAGE.value)
            ).mappings().all()

        rows = []
        for asset in assets:
            try:
                image_path = local_file_path(asset["storage_uri"])
                embedding = self.embedding_provider.embed_image(image_path, text=asset["ocr_text"])
            except Exception as exc:
                raise IngestFailure("visual_embedding_failed", str(exc), stage="visual_embedding") from exc
            try:
                vector_id = self.vector_store.upsert(
                    collection=self.collection,
                    chunk_id=asset["id"],
                    embedding=embedding,
                )
            except Exception as exc:
                raise IngestFailure("visual_vector_index_failed", str(exc), stage="visual_embedding") from exc
            rows.append(
                {
                    "id": str(uuid4()),
                    "asset_id": asset["id"],
                    "document_id": document_id,
                    "page_number": asset["page_number"],
                    "provider": self.embedding_provider.provider,
                    "model": self.embedding_provider.model,
                    "embedding_version": self.embedding_version,
                    "embedding_dimension": len(embedding),
                    "vector_store": self.vector_store.name,
                    "collection": self.collection,
                    "vector_id": vector_id,
                    "status": "ready",
                }
            )

        with self.engine.begin() as connection:
            asset_ids = [asset["id"] for asset in assets]
            if asset_ids:
                connection.execute(
                    delete(visual_page_embeddings).where(
                        visual_page_embeddings.c.asset_id.in_(asset_ids),
                        visual_page_embeddings.c.embedding_version == self.embedding_version,
                    )
                )
            if rows:
                connection.execute(insert(visual_page_embeddings), rows)
        return IndexResult(chunk_count=len(rows))


def searchable_text(chunk, *, document_title: str | None = None) -> str:
    parts = [
        document_title or "",
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


def vector_search(
    engine: Engine,
    query: str,
    *,
    limit: int = 10,
    active_embedding_version: str | None = None,
    embedding_provider=None,
    vector_store=None,
    collection: str | None = None,
) -> list[dict[str, object]]:
    provider = embedding_provider or DeterministicEmbeddingProvider()
    query_embedding = provider.embed(query)
    active_version = active_embedding_version or get_settings().embedding_version
    if vector_store is not None:
        return zvec_vector_search(
            engine,
            vector_store=vector_store,
            collection=collection or get_settings().zvec_collection,
            query_embedding=query_embedding,
            active_embedding_version=active_version,
            limit=limit,
        )
    with engine.connect() as connection:
        rows = connection.execute(
            select(chunk_search_index).where(chunk_search_index.c.embedding_version == active_version)
        ).mappings().all()
    scored = []
    for row in rows:
        score = cosine_similarity(query_embedding, row["embedding"])
        if score > 0:
            scored.append(
                {
                    "chunk_id": row["chunk_id"],
                    "score": score,
                    "chunk_type": row["chunk_type"],
                    "embedding_version": row["embedding_version"],
                }
            )
    return sorted(scored, key=lambda item: item["score"], reverse=True)[:limit]


def _visual_visibility_condition(viewer_user_id: str | None):
    # 可视页检索的可见性过滤（fail-closed）：公用文档对所有人可见，私有文档仅
    # 归属者本人可见，匿名访客（viewer_user_id 为 None）只保留公用文档。
    if viewer_user_id is None:
        return documents.c.visibility == "public"
    return or_(
        documents.c.visibility == "public",
        documents.c.owner_user_id == viewer_user_id,
    )


def visual_page_search(
    engine: Engine,
    query: str,
    *,
    limit: int = 5,
    active_embedding_version: str | None = None,
    embedding_provider=None,
    vector_store=None,
    collection: str | None = None,
    viewer_user_id: str | None = None,
) -> list[dict[str, object]]:
    settings = get_settings()
    provider = embedding_provider or DeterministicVisualEmbeddingProvider(
        dimension=settings.visual_embedding_dimension
    )
    query_embedding = provider.embed_query(query)
    active_version = active_embedding_version or settings.visual_embedding_version
    active_collection = collection or settings.zvec_visual_collection
    if vector_store is None:
        with engine.connect() as connection:
            rows = connection.execute(
                select(visual_page_embeddings)
                .where(visual_page_embeddings.c.embedding_version == active_version)
                .where(visual_page_embeddings.c.collection == active_collection)
                .where(visual_page_embeddings.c.status == "ready")
            ).mappings().all()
        store_results = [
            {
                "chunk_id": row["asset_id"],
                "score": 1.0,
                "vector_ref": row["vector_id"],
            }
            for row in rows
        ][:limit]
    else:
        store_results = vector_store.query(
            collection=active_collection,
            embedding=query_embedding,
            limit=limit,
        )
    if not store_results:
        return []
    asset_ids = [str(result["chunk_id"]) for result in store_results]
    with engine.connect() as connection:
        rows = connection.execute(
            select(
                visual_page_embeddings.c.asset_id,
                visual_page_embeddings.c.document_id,
                visual_page_embeddings.c.page_number,
                visual_page_embeddings.c.vector_id,
                visual_page_embeddings.c.embedding_version,
                document_assets.c.storage_uri,
                document_assets.c.source_locator,
                document_assets.c.ocr_text,
                document_assets.c.visual_observation,
                documents.c.title.label("document_title"),
            )
            .join(document_assets, document_assets.c.id == visual_page_embeddings.c.asset_id)
            .join(documents, documents.c.id == visual_page_embeddings.c.document_id)
            .where(visual_page_embeddings.c.asset_id.in_(asset_ids))
            .where(visual_page_embeddings.c.embedding_version == active_version)
            .where(visual_page_embeddings.c.collection == active_collection)
            .where(visual_page_embeddings.c.status == "ready")
            .where(_visual_visibility_condition(viewer_user_id))
        ).mappings().all()
    metadata_by_asset = {row["asset_id"]: row for row in rows}
    results = []
    for result in store_results:
        asset_id = str(result["chunk_id"])
        metadata = metadata_by_asset.get(asset_id)
        if metadata is None:
            continue
        results.append(
            {
                "evidence_type": "visual_page",
                "asset_id": asset_id,
                "document_id": metadata["document_id"],
                "document_title": metadata["document_title"],
                "page_number": metadata["page_number"],
                "source_locator": metadata["source_locator"],
                "image_uri": metadata["storage_uri"],
                "ocr_text": metadata["ocr_text"],
                "visual_observation": metadata["visual_observation"],
                "score": result["score"],
                "embedding_version": metadata["embedding_version"],
                "vector_ref": result.get("vector_ref") or metadata["vector_id"],
                "accessible": True,
            }
        )
    return results


def zvec_vector_search(
    engine: Engine,
    *,
    vector_store,
    collection: str,
    query_embedding: list[float],
    active_embedding_version: str,
    limit: int,
) -> list[dict[str, object]]:
    store_results = vector_store.query(collection=collection, embedding=query_embedding, limit=limit)
    if not store_results:
        return []
    chunk_ids = [str(result["chunk_id"]) for result in store_results]
    with engine.connect() as connection:
        rows = connection.execute(
            select(
                document_chunks.c.id,
                document_chunks.c.chunk_type,
                embedding_references.c.vector_id,
                embedding_references.c.embedding_version,
            )
            .join(embedding_references, embedding_references.c.chunk_id == document_chunks.c.id)
            .where(document_chunks.c.id.in_(chunk_ids))
            .where(embedding_references.c.embedding_version == active_embedding_version)
            .where(embedding_references.c.collection == collection)
            .where(embedding_references.c.status == "ready")
        ).mappings().all()
    metadata_by_chunk = {row["id"]: row for row in rows}
    results = []
    for result in store_results:
        chunk_id = str(result["chunk_id"])
        metadata = metadata_by_chunk.get(chunk_id)
        if metadata is None:
            continue
        results.append(
            {
                "chunk_id": chunk_id,
                "score": result["score"],
                "chunk_type": metadata["chunk_type"],
                "embedding_version": metadata["embedding_version"],
                "vector_ref": result["vector_ref"],
            }
        )
    return results


def tokenize(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text or "")]


def token_score(query_tokens: list[str], text: str) -> int:
    text_tokens = set(tokenize(text))
    return sum(1 for token in query_tokens if token in text_tokens)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def local_file_path(storage_uri: str) -> Path:
    parsed = urlsplit(storage_uri)
    if parsed.scheme != "file":
        raise ValueError("Only local file page assets can be embedded")
    return Path(parsed.path)
