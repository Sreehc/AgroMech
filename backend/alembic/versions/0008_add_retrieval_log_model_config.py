from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0008_retrieval_log_model_cfg"
down_revision = "0007_add_document_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("retrieval_logs")}
    if "model_config" not in columns:
        op.add_column(
            "retrieval_logs",
            sa.Column("model_config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("retrieval_logs")}
    if "model_config" in columns:
        op.drop_column("retrieval_logs", "model_config")
