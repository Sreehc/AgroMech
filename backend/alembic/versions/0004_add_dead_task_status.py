from __future__ import annotations

from alembic import op

from agromech_api.db.enums import IngestTaskStatus
from agromech_api.db.models import enum_check


revision = "0004_add_dead_task_status"
down_revision = "0003_add_model_aliases"
branch_labels = None
depends_on = None


CONSTRAINT_NAME = "ingest_task_status"
PREVIOUS_STATUSES = ("queued", "processing", "succeeded", "failed", "cancelled")


def _check_clause(values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"status IN ({rendered})"


def upgrade() -> None:
    # SQLite cannot ALTER a CHECK constraint in place; the enum-derived
    # constraint is recreated only where the dialect supports it. Fresh SQLite
    # schemas already include the new value via metadata.create_all.
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    with op.batch_alter_table("ingest_tasks") as batch:
        batch.drop_constraint(CONSTRAINT_NAME, type_="check")
        batch.create_check_constraint(CONSTRAINT_NAME, enum_check("status", IngestTaskStatus))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    with op.batch_alter_table("ingest_tasks") as batch:
        batch.drop_constraint(CONSTRAINT_NAME, type_="check")
        batch.create_check_constraint(CONSTRAINT_NAME, _check_clause(PREVIOUS_STATUSES))
