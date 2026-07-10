from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0005_add_embedding_versions"
down_revision = "0004_add_dead_task_status"
branch_labels = None
depends_on = None


DEFAULT_VERSION = "emb_local_256_chunk-v1"
DEFAULT_PROFILE = "chunk-v1"
DEFAULT_DIMENSION = "256"


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for table_name in ["chunk_search_index", "embedding_references"]:
        if table_name not in tables:
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        with op.batch_alter_table(table_name) as batch:
            if "embedding_version" not in existing_columns:
                batch.add_column(
                    sa.Column(
                        "embedding_version",
                        sa.String(length=160),
                        nullable=False,
                        server_default=DEFAULT_VERSION,
                    )
                )
            if "chunk_profile" not in existing_columns:
                batch.add_column(
                    sa.Column(
                        "chunk_profile",
                        sa.String(length=80),
                        nullable=False,
                        server_default=DEFAULT_PROFILE,
                    )
                )
            if "embedding_dimension" not in existing_columns:
                batch.add_column(
                    sa.Column(
                        "embedding_dimension",
                        sa.Integer(),
                        nullable=False,
                        server_default=DEFAULT_DIMENSION,
                    )
                )
    if "chunk_search_index" in tables:
        index_names = {index["name"] for index in inspector.get_indexes("chunk_search_index")}
        with op.batch_alter_table("chunk_search_index") as batch:
            if "ix_chunk_search_index_chunk_id" in index_names:
                batch.drop_index("ix_chunk_search_index_chunk_id")
            if "ix_chunk_search_index_chunk_id_version" not in index_names:
                batch.create_index(
                    "ix_chunk_search_index_chunk_id_version",
                    ["chunk_id", "embedding_version"],
                    unique=True,
                )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "chunk_search_index" in tables:
        with op.batch_alter_table("chunk_search_index") as batch:
            batch.drop_index("ix_chunk_search_index_chunk_id_version")
            batch.create_index("ix_chunk_search_index_chunk_id", ["chunk_id"], unique=True)
    for table_name in ["embedding_references", "chunk_search_index"]:
        if table_name not in tables:
            continue
        with op.batch_alter_table(table_name) as batch:
            batch.drop_column("embedding_dimension")
            batch.drop_column("chunk_profile")
            batch.drop_column("embedding_version")
