from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0013_pg_search_bm25"
down_revision = "0012_pgvector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("retrieval_logs")}
    with op.batch_alter_table("retrieval_logs") as batch:
        if "query_rewrite" not in columns:
            batch.add_column(
                sa.Column(
                    "query_rewrite",
                    sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'{}'"),
                )
            )
        if "fusion" not in columns:
            batch.add_column(
                sa.Column(
                    "fusion",
                    sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'{}'"),
                )
            )

    document_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("documents")}
    if "ix_documents_retrieval_state" not in document_indexes:
        op.create_index(
            "ix_documents_retrieval_state",
            "documents",
            ["status", "deleted_at", "visibility", "owner_user_id"],
        )
    if "ix_documents_retrieval_metadata" not in document_indexes:
        op.create_index(
            "ix_documents_retrieval_metadata",
            "documents",
            ["document_type", "language", "document_version"],
        )

    if bind.dialect.name != "postgresql":
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_search")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_chunk_search_index_bm25
        ON chunk_search_index
        USING bm25 (
            id,
            chunk_id,
            document_id,
            chunk_type,
            (search_text::pdb.jieba)
        )
        WITH (key_field='id')
        """
    )


def downgrade() -> None:
    raise RuntimeError("Downgrade from pg_search BM25 storage is not supported")
