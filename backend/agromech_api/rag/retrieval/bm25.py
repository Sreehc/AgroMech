from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict
from typing import Protocol

import jieba
from sqlalchemy import Engine, select, text

from agromech_api.db.models import chunk_search_index, documents
from agromech_api.rag.retrieval.filters import (
    RetrievalFilters,
    chunk_filter_conditions,
    document_filter_conditions,
)
from agromech_api.rag.retrieval.fusion import RankedHit


CODE_OR_CJK_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*|[\u4e00-\u9fff]+")


class Bm25Retriever(Protocol):
    def search(
        self,
        engine: Engine,
        query: str,
        *,
        filters: RetrievalFilters,
        limit: int,
    ) -> list[RankedHit]: ...


def bm25_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    for part in CODE_OR_CJK_RE.findall(value or ""):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            tokens.extend(
                token.strip().lower()
                for token in jieba.cut_for_search(part)
                if token.strip()
            )
        else:
            tokens.append(part.lower())
    return tokens


class ReferenceBm25Retriever:
    def __init__(self, *, k1: float = 1.2, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b

    def search(
        self,
        engine: Engine,
        query: str,
        *,
        filters: RetrievalFilters,
        limit: int,
    ) -> list[RankedHit]:
        query_tokens = list(dict.fromkeys(bm25_tokens(query)))
        if not query_tokens or limit <= 0:
            return []

        statement = (
            select(chunk_search_index.c.chunk_id, chunk_search_index.c.search_text)
            .select_from(
                chunk_search_index.join(
                    documents,
                    chunk_search_index.c.document_id == documents.c.id,
                )
            )
            .where(*document_filter_conditions(filters))
            .where(*chunk_filter_conditions(chunk_search_index.c.chunk_id, filters))
        )
        with engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()

        documents_tokens = [
            (str(row["chunk_id"]), bm25_tokens(row["search_text"])) for row in rows
        ]
        if not documents_tokens:
            return []

        average_length = sum(len(tokens) for _, tokens in documents_tokens) / len(
            documents_tokens
        )
        document_frequency = {
            term: sum(1 for _, tokens in documents_tokens if term in set(tokens))
            for term in query_tokens
        }
        scored: list[tuple[str, float]] = []
        document_count = len(documents_tokens)
        for chunk_id, tokens in documents_tokens:
            frequencies = Counter(tokens)
            score = 0.0
            for term in query_tokens:
                frequency = frequencies[term]
                if frequency == 0:
                    continue
                idf = math.log(
                    1
                    + (document_count - document_frequency[term] + 0.5)
                    / (document_frequency[term] + 0.5)
                )
                denominator = frequency + self.k1 * (
                    1
                    - self.b
                    + self.b * len(tokens) / max(1.0, average_length)
                )
                score += idf * frequency * (self.k1 + 1) / denominator
            if score > 0:
                scored.append((chunk_id, score))

        ranked = sorted(scored, key=lambda item: (-item[1], item[0]))[:limit]
        return [
            RankedHit(chunk_id=chunk_id, rank=rank, score=score)
            for rank, (chunk_id, score) in enumerate(ranked, start=1)
        ]


class PostgresBm25Retriever:
    def search(
        self,
        engine: Engine,
        query: str,
        *,
        filters: RetrievalFilters,
        limit: int,
    ) -> list[RankedHit]:
        if not query.strip() or limit <= 0:
            return []

        statement = text(
            """
            SELECT csi.chunk_id, pdb.score(csi.id) AS score
            FROM chunk_search_index AS csi
            JOIN documents AS d ON d.id = csi.document_id
            WHERE csi.search_text ||| :query
              AND d.status = 'indexed'
              AND d.deleted_at IS NULL
              AND (
                    d.visibility = 'public'
                    OR (
                        :viewer_user_id IS NOT NULL
                        AND d.owner_user_id = :viewer_user_id
                    )
              )
              AND (:brand IS NULL OR d.brand = :brand)
              AND (:model IS NULL OR d.model = :model)
              AND (:document_type IS NULL OR d.document_type = :document_type)
              AND (:language IS NULL OR d.language = :language)
              AND (:document_version IS NULL OR d.document_version = :document_version)
              AND (:subsystem IS NULL OR EXISTS (
                    SELECT 1
                    FROM chunk_entity_links AS cel
                    WHERE cel.chunk_id = csi.chunk_id
                      AND cel.entity_type = 'system'
                      AND cel.normalized_value = :subsystem
              ))
            ORDER BY pdb.score(csi.id) DESC, csi.id ASC
            LIMIT :limit
            """
        )
        params = {**asdict(filters), "query": query, "limit": limit}
        with engine.connect() as connection:
            rows = connection.execute(statement, params).mappings().all()
        return [
            RankedHit(
                chunk_id=str(row["chunk_id"]),
                rank=rank,
                score=float(row["score"]),
            )
            for rank, row in enumerate(rows, start=1)
        ]


def build_bm25_retriever(engine: Engine) -> Bm25Retriever:
    if engine.dialect.name == "postgresql":
        return PostgresBm25Retriever()
    return ReferenceBm25Retriever()
