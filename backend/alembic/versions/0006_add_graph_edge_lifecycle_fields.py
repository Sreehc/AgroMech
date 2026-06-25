from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0006_graph_edge_lifecycle"
down_revision = "0005_add_embedding_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if bind.dialect.name == "sqlite":
        # SQLite cannot alter FK actions in place; batch recreate keeps existing
        # rows while changing source_chunk_id from CASCADE to SET NULL.
        with op.batch_alter_table("graph_edges") as batch_op:
            batch_op.alter_column("source_chunk_id", existing_type=sa.String(length=36), nullable=True)
    else:
        op.drop_constraint("fk_graph_edges_source_chunk_id_document_chunks", "graph_edges", type_="foreignkey")
        op.alter_column("graph_edges", "source_chunk_id", existing_type=sa.String(length=36), nullable=True)
        op.create_foreign_key(
            "fk_graph_edges_source_chunk_id_document_chunks",
            "graph_edges",
            "document_chunks",
            ["source_chunk_id"],
            ["id"],
            ondelete="SET NULL",
        )
    columns = {column["name"] for column in inspector.get_columns("graph_edges")}
    if "schema_version" not in columns:
        op.add_column(
            "graph_edges",
            sa.Column("schema_version", sa.String(length=80), nullable=False, server_default="graph-v1"),
        )
    if "is_active" not in columns:
        op.add_column(
            "graph_edges",
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
    if "valid_to" not in columns:
        op.add_column("graph_edges", sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True))

    indexes = {index["name"] for index in inspector.get_indexes("graph_edges")}
    if "ix_graph_edges_active_document" not in indexes:
        op.create_index(
            "ix_graph_edges_active_document",
            "graph_edges",
            ["source_document_id", "is_active"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("graph_edges")}
    if "ix_graph_edges_active_document" in indexes:
        op.drop_index("ix_graph_edges_active_document", table_name="graph_edges")
    columns = {column["name"] for column in inspector.get_columns("graph_edges")}
    if "valid_to" in columns:
        op.drop_column("graph_edges", "valid_to")
    if "is_active" in columns:
        op.drop_column("graph_edges", "is_active")
    if "schema_version" in columns:
        op.drop_column("graph_edges", "schema_version")
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("graph_edges") as batch_op:
            batch_op.alter_column("source_chunk_id", existing_type=sa.String(length=36), nullable=False)
    else:
        op.drop_constraint("fk_graph_edges_source_chunk_id_document_chunks", "graph_edges", type_="foreignkey")
        op.alter_column("graph_edges", "source_chunk_id", existing_type=sa.String(length=36), nullable=False)
        op.create_foreign_key(
            "fk_graph_edges_source_chunk_id_document_chunks",
            "graph_edges",
            "document_chunks",
            ["source_chunk_id"],
            ["id"],
            ondelete="CASCADE",
        )
