from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0010_visual_page_embeddings"
down_revision = "0009_add_database_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "visual_page_embeddings" not in tables:
        op.create_table(
            "visual_page_embeddings",
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
            sa.Column("vector_store", sa.String(length=80), nullable=False),
            sa.Column("collection", sa.String(length=120), nullable=False),
            sa.Column("vector_id", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index(
            "ix_visual_page_embeddings_asset_id_version",
            "visual_page_embeddings",
            ["asset_id", "embedding_version"],
            unique=True,
        )
        op.create_index(
            "ix_visual_page_embeddings_document_id",
            "visual_page_embeddings",
            ["document_id"],
        )
        op.create_index(
            "ix_visual_page_embeddings_vector",
            "visual_page_embeddings",
            ["vector_store", "vector_id"],
        )

    citation_columns = {column["name"] for column in inspector.get_columns("answer_citations")}
    with op.batch_alter_table("answer_citations") as batch:
        if "asset_id" not in citation_columns:
            batch.add_column(sa.Column("asset_id", sa.String(length=36)))
            batch.create_foreign_key(
                "fk_answer_citations_asset_id_document_assets",
                "document_assets",
                ["asset_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if "page_number" not in citation_columns:
            batch.add_column(sa.Column("page_number", sa.Integer()))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    citation_columns = {column["name"] for column in inspector.get_columns("answer_citations")}
    with op.batch_alter_table("answer_citations") as batch:
        if "page_number" in citation_columns:
            batch.drop_column("page_number")
        if "asset_id" in citation_columns:
            batch.drop_constraint("fk_answer_citations_asset_id_document_assets", type_="foreignkey")
            batch.drop_column("asset_id")

    if "visual_page_embeddings" in tables:
        op.drop_index("ix_visual_page_embeddings_vector", table_name="visual_page_embeddings")
        op.drop_index("ix_visual_page_embeddings_document_id", table_name="visual_page_embeddings")
        op.drop_index("ix_visual_page_embeddings_asset_id_version", table_name="visual_page_embeddings")
        op.drop_table("visual_page_embeddings")
