from __future__ import annotations

from alembic import op

from agromech_api.db.models import metadata


revision = "0001_create_core_tables"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    metadata.create_all(bind=bind)


def downgrade() -> None:
    metadata.drop_all(bind=op.get_bind())
