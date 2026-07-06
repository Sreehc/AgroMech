from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector


revision = "0012_pgvector"
down_revision = "0011_add_document_visibility"
branch_labels = None
depends_on = None


def _embedding_type(dialect_name: str) -> sa.types.TypeEngine:
    if dialect_name == "postgresql":
        return Vector(1024)
    return sa.JSON()


def _create_index_if_missing(
    table_name: str,
    index_name: str,
    columns: list[str],
    *,
    unique: bool = False,
) -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes(table_name)}
    if index_name not in indexes:
        op.create_index(index_name, table_name, columns, unique=unique)


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if dialect_name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    if "embedding_references" in tables:
        op.drop_table("embedding_references")
        tables.remove("embedding_references")

    if "visual_page_embeddings" in tables:
        op.drop_table("visual_page_embeddings")
        tables.remove("visual_page_embeddings")

    if "chunk_search_index" in tables:
        search_columns = {column["name"] for column in inspector.get_columns("chunk_search_index")}
        if "embedding" in search_columns:
            with op.batch_alter_table("chunk_search_index") as batch:
                batch.drop_column("embedding")

    embedding_type = _embedding_type(dialect_name)

    if "chunk_vector_embeddings" not in tables:
        op.create_table(
            "chunk_vector_embeddings",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "chunk_id",
                sa.String(length=36),
                sa.ForeignKey("document_chunks.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "document_id",
                sa.String(length=36),
                sa.ForeignKey("documents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("provider", sa.String(length=80), nullable=False),
            sa.Column("model", sa.String(length=120), nullable=False),
            sa.Column("embedding_version", sa.String(length=160), nullable=False),
            sa.Column("chunk_profile", sa.String(length=80), nullable=False),
            sa.Column("embedding_dimension", sa.Integer(), nullable=False),
            sa.Column("embedding", embedding_type, nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    _create_index_if_missing(
        "chunk_vector_embeddings",
        "ix_chunk_vector_embeddings_chunk_version",
        ["chunk_id", "embedding_version"],
        unique=True,
    )
    _create_index_if_missing(
        "chunk_vector_embeddings",
        "ix_chunk_vector_embeddings_document_id",
        ["document_id"],
    )

    if "visual_page_vector_embeddings" not in tables:
        op.create_table(
            "visual_page_vector_embeddings",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "asset_id",
                sa.String(length=36),
                sa.ForeignKey("document_assets.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "document_id",
                sa.String(length=36),
                sa.ForeignKey("documents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("page_number", sa.Integer()),
            sa.Column("provider", sa.String(length=80), nullable=False),
            sa.Column("model", sa.String(length=120), nullable=False),
            sa.Column("embedding_version", sa.String(length=160), nullable=False),
            sa.Column("embedding_dimension", sa.Integer(), nullable=False),
            sa.Column("embedding", embedding_type, nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    _create_index_if_missing(
        "visual_page_vector_embeddings",
        "ix_visual_page_vector_embeddings_asset_version",
        ["asset_id", "embedding_version"],
        unique=True,
    )
    _create_index_if_missing(
        "visual_page_vector_embeddings",
        "ix_visual_page_vector_embeddings_document_id",
        ["document_id"],
    )

    if dialect_name == "postgresql":
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_chunk_vector_embeddings_embedding_hnsw "
            "ON chunk_vector_embeddings USING hnsw (embedding vector_cosine_ops)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_visual_page_vector_embeddings_embedding_hnsw "
            "ON visual_page_vector_embeddings USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    raise RuntimeError("Downgrade from pgvector embedding storage is not supported")
