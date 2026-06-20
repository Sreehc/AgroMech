from __future__ import annotations

from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    func,
)

from agromech_api.db.enums import AssetType, ChunkType, DocumentStatus, IngestTaskStatus, TaskType


metadata = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


def enum_check(column_name: str, enum_type: type[StrEnum]) -> str:
    values = ", ".join(f"'{item.value}'" for item in enum_type)
    return f"{column_name} IN ({values})"


documents = Table(
    "documents",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("title", String(255), nullable=False),
    Column("original_file_name", String(255), nullable=False),
    Column("file_hash", String(128), nullable=False),
    Column("file_size_bytes", BigInteger, nullable=False),
    Column("mime_type", String(120), nullable=False),
    Column("storage_uri", String(500), nullable=False),
    Column("brand", String(120)),
    Column("model", String(120)),
    Column("document_type", String(80)),
    Column("language", String(32)),
    Column("source", String(255)),
    Column("status", String(32), nullable=False, default=DocumentStatus.QUEUED.value),
    Column("failure_stage", String(80)),
    Column("failure_code", String(80)),
    Column("failure_message", Text),
    Column("created_by_role", String(32), nullable=False, default="admin"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("deleted_at", DateTime(timezone=True)),
    CheckConstraint(enum_check("status", DocumentStatus), name="document_status"),
)
Index("ix_documents_status", documents.c.status)
Index("ix_documents_brand_model", documents.c.brand, documents.c.model)
Index("ix_documents_file_hash", documents.c.file_hash)

document_assets = Table(
    "document_assets",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("document_id", ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
    Column("asset_type", String(32), nullable=False),
    Column("storage_uri", String(500), nullable=False),
    Column("mime_type", String(120)),
    Column("page_number", Integer),
    Column("source_locator", JSON),
    Column("ocr_text", Text),
    Column("visual_observation", JSON),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(enum_check("asset_type", AssetType), name="asset_type"),
)
Index("ix_document_assets_document_id", document_assets.c.document_id)

document_chunks = Table(
    "document_chunks",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("document_id", ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
    Column("asset_id", ForeignKey("document_assets.id", ondelete="SET NULL")),
    Column("chunk_type", String(32), nullable=False),
    Column("content", Text, nullable=False),
    Column("summary", Text),
    Column("page_number", Integer),
    Column("section_title", String(255)),
    Column("worksheet_name", String(255)),
    Column("row_start", Integer),
    Column("row_end", Integer),
    Column("source_locator", JSON, nullable=False),
    Column("metadata", JSON),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(enum_check("chunk_type", ChunkType), name="chunk_type"),
)
Index("ix_document_chunks_document_id", document_chunks.c.document_id)
Index("ix_document_chunks_type", document_chunks.c.chunk_type)

ingest_tasks = Table(
    "ingest_tasks",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("document_id", ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
    Column("task_type", String(32), nullable=False),
    Column("status", String(32), nullable=False, default=IngestTaskStatus.QUEUED.value),
    Column("attempt_count", Integer, nullable=False, default=0),
    Column("stage", String(80)),
    Column("error_code", String(80)),
    Column("error_message", Text),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(enum_check("task_type", TaskType), name="task_type"),
    CheckConstraint(enum_check("status", IngestTaskStatus), name="ingest_task_status"),
)
Index("ix_ingest_tasks_document_id_status", ingest_tasks.c.document_id, ingest_tasks.c.status)
Index("ix_ingest_tasks_status", ingest_tasks.c.status)

embedding_references = Table(
    "embedding_references",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("chunk_id", ForeignKey("document_chunks.id", ondelete="CASCADE"), nullable=False),
    Column("provider", String(80), nullable=False),
    Column("model", String(120), nullable=False),
    Column("vector_store", String(80), nullable=False),
    Column("collection", String(120), nullable=False),
    Column("vector_id", String(255), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index("ix_embedding_references_chunk_id", embedding_references.c.chunk_id)
Index("ix_embedding_references_vector", embedding_references.c.vector_store, embedding_references.c.vector_id)

chunk_search_index = Table(
    "chunk_search_index",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("chunk_id", ForeignKey("document_chunks.id", ondelete="CASCADE"), nullable=False),
    Column("document_id", ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
    Column("chunk_type", String(32), nullable=False),
    Column("search_text", Text, nullable=False),
    Column("embedding", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(enum_check("chunk_type", ChunkType), name="search_chunk_type"),
)
Index("ix_chunk_search_index_chunk_id", chunk_search_index.c.chunk_id, unique=True)
Index("ix_chunk_search_index_document_id", chunk_search_index.c.document_id)
Index("ix_chunk_search_index_type", chunk_search_index.c.chunk_type)

chunk_entity_links = Table(
    "chunk_entity_links",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("chunk_id", ForeignKey("document_chunks.id", ondelete="CASCADE"), nullable=False),
    Column("document_id", ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
    Column("entity_type", String(80), nullable=False),
    Column("entity_value", String(255), nullable=False),
    Column("normalized_value", String(255), nullable=False),
    Column("confidence", Float, nullable=False),
    Column("source", String(80), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index("ix_chunk_entity_links_chunk_id", chunk_entity_links.c.chunk_id)
Index("ix_chunk_entity_links_document_type", chunk_entity_links.c.document_id, chunk_entity_links.c.entity_type)
Index("ix_chunk_entity_links_lookup", chunk_entity_links.c.entity_type, chunk_entity_links.c.normalized_value)

document_entity_extractions = Table(
    "document_entity_extractions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("document_id", ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
    Column("extracted_entities", JSON, nullable=False),
    Column("confidence", Float, nullable=False),
    Column("low_confidence", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index("ix_document_entity_extractions_document_id", document_entity_extractions.c.document_id)

retrieval_logs = Table(
    "retrieval_logs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("trace_id", String(64), nullable=False),
    Column("query", Text, nullable=False),
    Column("filters", JSON),
    Column("channels", JSON, nullable=False),
    Column("candidates", JSON),
    Column("rerank", JSON),
    Column("final_evidence", JSON),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index("ix_retrieval_logs_trace_id", retrieval_logs.c.trace_id, unique=True)

qa_records = Table(
    "qa_records",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("trace_id", String(64), nullable=False),
    Column("question", Text, nullable=False),
    Column("answer", Text, nullable=False),
    Column("sections", JSON),
    Column("uncertainty", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index("ix_qa_records_trace_id", qa_records.c.trace_id)

answer_citations = Table(
    "answer_citations",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("qa_record_id", ForeignKey("qa_records.id", ondelete="CASCADE"), nullable=False),
    Column("document_id", ForeignKey("documents.id", ondelete="SET NULL")),
    Column("chunk_id", ForeignKey("document_chunks.id", ondelete="SET NULL")),
    Column("citation_payload", JSON, nullable=False),
    Column("accessible", Boolean, nullable=False, default=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index("ix_answer_citations_qa_record_id", answer_citations.c.qa_record_id)

evaluation_runs = Table(
    "evaluation_runs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_id", String(64), nullable=False),
    Column("dataset_version", String(120), nullable=False),
    Column("model_config", JSON, nullable=False),
    Column("prompt_version", String(120), nullable=False),
    Column("code_version", String(120)),
    Column("metrics_summary", JSON),
    Column("failure_types", JSON),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index("ix_evaluation_runs_run_id", evaluation_runs.c.run_id, unique=True)
