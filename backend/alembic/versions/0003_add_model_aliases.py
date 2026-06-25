from __future__ import annotations

from alembic import op

from agromech_api.db.models import model_aliases


revision = "0003_add_model_aliases"
down_revision = "0002_add_chat_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    model_aliases.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    model_aliases.drop(bind=op.get_bind(), checkfirst=True)
