from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0007_add_document_version"
down_revision = "0006_graph_edge_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("documents")}
    if "document_version" not in columns:
        op.add_column("documents", sa.Column("document_version", sa.String(length=80), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("documents")}
    if "document_version" in columns:
        op.drop_column("documents", "document_version")
