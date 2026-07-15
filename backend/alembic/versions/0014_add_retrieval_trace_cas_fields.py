from __future__ import annotations

import json
from collections.abc import Mapping

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
    backfill_historical_citation_status(bind)


def backfill_historical_citation_status(bind) -> None:
    retrieval_logs = sa.Table("retrieval_logs", sa.MetaData(), autoload_with=bind)
    if "citation_status" not in retrieval_logs.c:
        return
    rows = bind.execute(
        sa.select(retrieval_logs.c.id, retrieval_logs.c.channels)
    ).mappings()
    for row in rows:
        if historical_channels_have_citation(row["channels"]):
            bind.execute(
                retrieval_logs.update()
                .where(retrieval_logs.c.id == row["id"])
                .where(retrieval_logs.c.citation_status != "completed")
                .values(citation_status="completed")
            )


def historical_channels_have_citation(channels: object) -> bool:
    if isinstance(channels, (bytes, bytearray)):
        channels = channels.decode("utf-8")
    if isinstance(channels, str):
        try:
            channels = json.loads(channels)
        except json.JSONDecodeError:
            return False
    return isinstance(channels, Mapping) and "citation" in channels


def downgrade() -> None:
    raise RuntimeError("Downgrade from retrieval trace CAS fields is not supported")
