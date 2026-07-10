from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, select

from agromech_api.core.config import Settings, get_settings
from agromech_api.db.enums import DocumentStatus
from agromech_api.db.models import documents
from agromech_api.integrations.embeddings.text import build_embedding_provider
from agromech_api.integrations.embeddings.visual import build_visual_embedding_provider
from agromech_api.rag.retrieval.indexing import SearchIndexer, VisualPageIndexer


@dataclass
class RebuildSummary:
    selected: int
    succeeded: int
    failed: int
    failures: list[tuple[str, str]]


def select_document_ids(engine: Engine, document_id: str | None = None) -> list[str]:
    statement = (
        select(documents.c.id)
        .where(documents.c.status == DocumentStatus.INDEXED.value)
        .order_by(documents.c.updated_at)
    )
    if document_id is not None:
        statement = statement.where(documents.c.id == document_id)

    with engine.connect() as connection:
        return list(connection.execute(statement).scalars())


def rebuild_vector_index(
    engine: Engine,
    document_id: str | None = None,
    include_visual: bool = True,
    dry_run: bool = False,
    settings: Settings | None = None,
    embedding_provider=None,
    visual_embedding_provider=None,
    search_indexer_factory=SearchIndexer,
    visual_indexer_factory=VisualPageIndexer,
) -> RebuildSummary:
    document_ids = select_document_ids(engine, document_id=document_id)
    if dry_run:
        return RebuildSummary(selected=len(document_ids), succeeded=0, failed=0, failures=[])

    active_settings = settings or get_settings()
    active_embedding_provider = embedding_provider or build_embedding_provider(active_settings)
    active_visual_embedding_provider = None
    if include_visual:
        active_visual_embedding_provider = visual_embedding_provider or build_visual_embedding_provider(active_settings)
    search_indexer = search_indexer_factory(engine, embedding_provider=active_embedding_provider)
    visual_indexer = (
        visual_indexer_factory(engine, embedding_provider=active_visual_embedding_provider)
        if include_visual
        else None
    )

    succeeded = 0
    failures: list[tuple[str, str]] = []
    for selected_document_id in document_ids:
        try:
            search_indexer.index_document(selected_document_id)
            if visual_indexer is not None:
                visual_indexer.index_document(selected_document_id)
        except Exception as exc:
            failures.append((selected_document_id, str(exc)))
            continue
        succeeded += 1

    return RebuildSummary(
        selected=len(document_ids),
        succeeded=succeeded,
        failed=len(failures),
        failures=failures,
    )
