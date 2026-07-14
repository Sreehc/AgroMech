from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import Engine, bindparam, delete, insert, literal, or_, select

from agromech_api.core.config import get_settings
from agromech_api.db.enums import AssetType
from agromech_api.db.models import (
    chunk_vector_embeddings,
    chunk_search_index,
    document_assets,
    document_chunks,
    documents,
    visual_page_vector_embeddings,
)
from agromech_api.ingestion.runner import IngestFailure
from agromech_api.integrations.embeddings.visual import DeterministicVisualEmbeddingProvider
from agromech_api.rag.retrieval.filters import (
    RetrievalFilters,
    chunk_filter_conditions,
    document_filter_conditions,
)


TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+|[\u4e00-\u9fff]+")
PGVECTOR_DIMENSION = 1024


@dataclass(frozen=True)
class IndexResult:
    chunk_count: int


class DeterministicEmbeddingProvider:
    provider = "local"
    model = "deterministic-token-hash"

    def __init__(self, *, dimension: int = 1024) -> None:
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
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


class SearchIndexer:
    def __init__(
        self,
        engine: Engine,
        *,
        embedding_provider=None,
        embedding_version: str | None = None,
        chunk_profile: str | None = None,
        embedding_dimension: int | None = None,
    ) -> None:
        settings = get_settings()
        self.engine = engine
        self.embedding_provider = embedding_provider or DeterministicEmbeddingProvider(
            dimension=embedding_dimension or settings.embedding_dimension
        )
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

            search_rows.append(
                {
                    "id": str(uuid4()),
                    "chunk_id": chunk["id"],
                    "document_id": document_id,
                    "chunk_type": chunk["chunk_type"],
                    "search_text": search_text,
                    "embedding_version": self.embedding_version,
                    "chunk_profile": self.chunk_profile,
                    "embedding_dimension": len(embedding),
                }
            )
            embedding_rows.append(
                {
                    "id": str(uuid4()),
                    "chunk_id": chunk["id"],
                    "document_id": document_id,
                    "provider": self.embedding_provider.provider,
                    "model": self.embedding_provider.model,
                    "embedding_version": self.embedding_version,
                    "chunk_profile": self.chunk_profile,
                    "embedding_dimension": len(embedding),
                    "embedding": pgvector_storage_embedding(embedding),
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
                    delete(chunk_vector_embeddings).where(
                        chunk_vector_embeddings.c.chunk_id.in_(chunk_ids),
                        chunk_vector_embeddings.c.embedding_version == self.embedding_version,
                    )
                )
            if search_rows:
                connection.execute(insert(chunk_search_index), search_rows)
            if embedding_rows:
                connection.execute(insert(chunk_vector_embeddings), embedding_rows)
        return IndexResult(chunk_count=len(search_rows))


class VisualPageIndexer:
    def __init__(
        self,
        engine: Engine,
        *,
        embedding_provider=None,
        embedding_version: str | None = None,
        embedding_dimension: int | None = None,
    ) -> None:
        settings = get_settings()
        self.engine = engine
        self.embedding_provider = embedding_provider or DeterministicVisualEmbeddingProvider(
            dimension=settings.visual_embedding_dimension
        )
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
                    "embedding": pgvector_storage_embedding(embedding),
                    "status": "ready",
                }
            )

        with self.engine.begin() as connection:
            asset_ids = [asset["id"] for asset in assets]
            if asset_ids:
                connection.execute(
                    delete(visual_page_vector_embeddings).where(
                        visual_page_vector_embeddings.c.asset_id.in_(asset_ids),
                        visual_page_vector_embeddings.c.embedding_version == self.embedding_version,
                    )
                )
            if rows:
                connection.execute(insert(visual_page_vector_embeddings), rows)
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
    filters: RetrievalFilters,
    limit: int = 10,
    active_embedding_version: str | None = None,
    embedding_provider=None,
) -> list[dict[str, object]]:
    settings = get_settings()
    provider = embedding_provider or DeterministicEmbeddingProvider(dimension=settings.embedding_dimension)
    embedding_version = active_embedding_version or settings.embedding_version
    query_embedding = provider.embed(query)
    if engine.dialect.name == "postgresql":
        return postgres_vector_search(
            engine,
            pgvector_storage_embedding(query_embedding),
            embedding_version=embedding_version,
            filters=filters,
            limit=limit,
        )
    statement = (
        select(
            chunk_vector_embeddings.c.id.label("embedding_id"),
            chunk_vector_embeddings.c.chunk_id,
            chunk_vector_embeddings.c.embedding_version,
            chunk_vector_embeddings.c.embedding,
            document_chunks.c.chunk_type,
        )
        .select_from(
            chunk_vector_embeddings.join(
                document_chunks,
                chunk_vector_embeddings.c.chunk_id == document_chunks.c.id,
            ).join(
                documents,
                chunk_vector_embeddings.c.document_id == documents.c.id,
            )
        )
        .where(chunk_vector_embeddings.c.embedding_version == embedding_version)
        .where(chunk_vector_embeddings.c.status == "ready")
        .where(*document_filter_conditions(filters))
        .where(*chunk_filter_conditions(chunk_vector_embeddings.c.chunk_id, filters))
    )
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()

    scored = []
    for row in rows:
        score = cosine_similarity(query_embedding, vector_values(row["embedding"]))
        if score > 0:
            embedding_id = str(row["embedding_id"])
            scored.append(
                {
                    "chunk_id": row["chunk_id"],
                    "score": score,
                    "chunk_type": row["chunk_type"],
                    "embedding_version": row["embedding_version"],
                    "embedding_id": embedding_id,
                    "vector_ref": f"pgvector://chunk_vector_embeddings/{embedding_id}",
                }
            )
    return sorted(
        scored,
        key=lambda item: (-float(item["score"]), str(item["chunk_id"])),
    )[:limit]


def visual_page_search(
    engine: Engine,
    query: str,
    *,
    limit: int = 5,
    active_embedding_version: str | None = None,
    embedding_provider=None,
    viewer_user_id: str | None = None,
) -> list[dict[str, object]]:
    settings = get_settings()
    provider = embedding_provider or DeterministicVisualEmbeddingProvider(
        dimension=settings.visual_embedding_dimension
    )
    embedding_version = active_embedding_version or settings.visual_embedding_version
    query_embedding = provider.embed_query(query)
    if engine.dialect.name == "postgresql":
        return postgres_visual_page_search(
            engine,
            pgvector_storage_embedding(query_embedding),
            embedding_version=embedding_version,
            limit=limit,
            viewer_user_id=viewer_user_id,
        )
    statement = (
        select(
            visual_page_vector_embeddings.c.id.label("embedding_id"),
            visual_page_vector_embeddings.c.asset_id,
            visual_page_vector_embeddings.c.document_id,
            visual_page_vector_embeddings.c.page_number,
            visual_page_vector_embeddings.c.embedding_version,
            visual_page_vector_embeddings.c.embedding,
            document_assets.c.storage_uri,
            document_assets.c.source_locator,
            document_assets.c.ocr_text,
            document_assets.c.visual_observation,
        )
        .select_from(
            visual_page_vector_embeddings.join(
                document_assets,
                visual_page_vector_embeddings.c.asset_id == document_assets.c.id,
            ).join(
                documents,
                visual_page_vector_embeddings.c.document_id == documents.c.id,
            )
        )
        .where(visual_page_vector_embeddings.c.embedding_version == embedding_version)
        .where(visual_page_vector_embeddings.c.status == "ready")
        .where(visible_document_condition(viewer_user_id))
    )
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()

    scored = []
    for row in rows:
        score = cosine_similarity(query_embedding, vector_values(row["embedding"]))
        if score > 0:
            embedding_id = str(row["embedding_id"])
            scored.append(
                {
                    "asset_id": row["asset_id"],
                    "document_id": row["document_id"],
                    "page_number": row["page_number"],
                    "score": score,
                    "source_locator": row["source_locator"],
                    "ocr_text": row["ocr_text"],
                    "visual_observation": row["visual_observation"],
                    "image_uri": row["storage_uri"],
                    "evidence_type": "visual_page",
                    "embedding_version": row["embedding_version"],
                    "embedding_id": embedding_id,
                    "vector_ref": f"pgvector://visual_page_vector_embeddings/{embedding_id}",
                }
            )
    return sorted(scored, key=lambda item: item["score"], reverse=True)[:limit]


def tokenize(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text or "")]


def token_score(query_tokens: list[str], text: str) -> int:
    text_tokens = set(tokenize(text))
    return sum(1 for token in query_tokens if token in text_tokens)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) == 0 or len(right) == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def postgres_vector_search(
    engine: Engine,
    query_embedding: list[float],
    *,
    embedding_version: str,
    filters: RetrievalFilters,
    limit: int,
) -> list[dict[str, object]]:
    distance = chunk_vector_embeddings.c.embedding.op("<=>")(
        bindparam("query_embedding", query_embedding, type_=Vector(PGVECTOR_DIMENSION))
    )
    score = (literal(1.0) - distance).label("score")
    statement = (
        select(
            chunk_vector_embeddings.c.id.label("embedding_id"),
            chunk_vector_embeddings.c.chunk_id,
            chunk_vector_embeddings.c.embedding_version,
            document_chunks.c.chunk_type,
            score,
        )
        .select_from(
            chunk_vector_embeddings.join(
                document_chunks,
                chunk_vector_embeddings.c.chunk_id == document_chunks.c.id,
            ).join(
                documents,
                chunk_vector_embeddings.c.document_id == documents.c.id,
            )
        )
        .where(chunk_vector_embeddings.c.embedding_version == embedding_version)
        .where(chunk_vector_embeddings.c.status == "ready")
        .where(*document_filter_conditions(filters))
        .where(*chunk_filter_conditions(chunk_vector_embeddings.c.chunk_id, filters))
        .order_by(distance, chunk_vector_embeddings.c.chunk_id)
        .limit(limit)
    )
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()
    results = []
    for row in rows:
        score_value = float(row["score"])
        if score_value <= 0:
            continue
        embedding_id = str(row["embedding_id"])
        results.append(
            {
                "chunk_id": row["chunk_id"],
                "score": score_value,
                "chunk_type": row["chunk_type"],
                "embedding_version": row["embedding_version"],
                "embedding_id": embedding_id,
                "vector_ref": f"pgvector://chunk_vector_embeddings/{embedding_id}",
            }
        )
    return results


def postgres_visual_page_search(
    engine: Engine,
    query_embedding: list[float],
    *,
    embedding_version: str,
    limit: int,
    viewer_user_id: str | None,
) -> list[dict[str, object]]:
    distance = visual_page_vector_embeddings.c.embedding.op("<=>")(
        bindparam("query_embedding", query_embedding, type_=Vector(PGVECTOR_DIMENSION))
    )
    score = (literal(1.0) - distance).label("score")
    statement = (
        select(
            visual_page_vector_embeddings.c.id.label("embedding_id"),
            visual_page_vector_embeddings.c.asset_id,
            visual_page_vector_embeddings.c.document_id,
            visual_page_vector_embeddings.c.page_number,
            visual_page_vector_embeddings.c.embedding_version,
            document_assets.c.storage_uri,
            document_assets.c.source_locator,
            document_assets.c.ocr_text,
            document_assets.c.visual_observation,
            score,
        )
        .select_from(
            visual_page_vector_embeddings.join(
                document_assets,
                visual_page_vector_embeddings.c.asset_id == document_assets.c.id,
            ).join(
                documents,
                visual_page_vector_embeddings.c.document_id == documents.c.id,
            )
        )
        .where(visual_page_vector_embeddings.c.embedding_version == embedding_version)
        .where(visual_page_vector_embeddings.c.status == "ready")
        .where(visible_document_condition(viewer_user_id))
        .order_by(distance)
        .limit(limit)
    )
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()
    results = []
    for row in rows:
        score_value = float(row["score"])
        if score_value <= 0:
            continue
        embedding_id = str(row["embedding_id"])
        results.append(
            {
                "asset_id": row["asset_id"],
                "document_id": row["document_id"],
                "page_number": row["page_number"],
                "score": score_value,
                "source_locator": row["source_locator"],
                "ocr_text": row["ocr_text"],
                "visual_observation": row["visual_observation"],
                "image_uri": row["storage_uri"],
                "evidence_type": "visual_page",
                "embedding_version": row["embedding_version"],
                "embedding_id": embedding_id,
                "vector_ref": f"pgvector://visual_page_vector_embeddings/{embedding_id}",
            }
        )
    return results


def visible_document_condition(viewer_user_id: str | None):
    conditions = [documents.c.visibility == "public"]
    if viewer_user_id is not None:
        conditions.append(documents.c.owner_user_id == viewer_user_id)
    return or_(*conditions)


def vector_values(value) -> list[float]:
    if hasattr(value, "to_list"):
        return [float(item) for item in value.to_list()]
    return [float(item) for item in value]


def pgvector_storage_embedding(embedding: list[float]) -> list[float]:
    if len(embedding) > PGVECTOR_DIMENSION:
        raise ValueError(f"Embedding dimension {len(embedding)} exceeds pgvector({PGVECTOR_DIMENSION})")
    if len(embedding) == PGVECTOR_DIMENSION:
        return embedding
    return [*embedding, *([0.0] * (PGVECTOR_DIMENSION - len(embedding)))]


def local_file_path(storage_uri: str) -> Path:
    parsed = urlsplit(storage_uri)
    if parsed.scheme != "file":
        raise ValueError("Only local file page assets can be embedded")
    return Path(parsed.path)
