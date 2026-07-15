from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0014_trace_cas_fields"
down_revision = "0013_pg_search_bm25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("retrieval_logs")}
    with op.batch_alter_table("retrieval_logs") as batch:
        if "retrieval_round" not in columns:
            batch.add_column(
                sa.Column(
                    "retrieval_round",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("1"),
                )
            )
        if "citation_status" not in columns:
            batch.add_column(
                sa.Column(
                    "citation_status",
                    sa.String(length=16),
                    nullable=False,
                    server_default=sa.text("'pending'"),
                )
            )


def downgrade() -> None:
    raise RuntimeError("Downgrade from retrieval trace CAS fields is not supported")
