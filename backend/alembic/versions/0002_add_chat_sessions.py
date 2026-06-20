from __future__ import annotations

from alembic import op

from agromech_api.db.models import chat_sessions


revision = "0002_add_chat_sessions"
down_revision = "0001_create_core_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    chat_sessions.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    chat_sessions.drop(bind=op.get_bind(), checkfirst=True)
