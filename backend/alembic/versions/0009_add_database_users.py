from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0009_add_database_users"
down_revision = "0008_retrieval_log_model_cfg"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "users" not in tables:
        op.create_table(
            "users",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("username", sa.String(length=120), nullable=False),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("role", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
            sa.Column("display_name", sa.String(length=255)),
            sa.Column("last_login_at", sa.DateTime(timezone=True)),
            sa.Column("password_changed_at", sa.DateTime(timezone=True)),
            sa.Column("token_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint("role IN ('admin', 'maintainer', 'user', 'evaluator')", name="ck_users_user_role"),
            sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_users_user_status"),
        )
        op.create_index("ix_users_username", "users", ["username"], unique=True)
        op.create_index("ix_users_role_status", "users", ["role", "status"])
    if "auth_audit_logs" not in tables:
        op.create_table(
            "auth_audit_logs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("username", sa.String(length=120), nullable=False),
            sa.Column("event_type", sa.String(length=80), nullable=False),
            sa.Column("success", sa.Boolean(), nullable=False),
            sa.Column("ip_address", sa.String(length=80)),
            sa.Column("user_agent", sa.String(length=255)),
            sa.Column("metadata", sa.JSON()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index(
            "ix_auth_audit_logs_user_id_created_at",
            "auth_audit_logs",
            ["user_id", "created_at"],
        )
        op.create_index(
            "ix_auth_audit_logs_username_created_at",
            "auth_audit_logs",
            ["username", "created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "auth_audit_logs" in tables:
        op.drop_index("ix_auth_audit_logs_username_created_at", table_name="auth_audit_logs")
        op.drop_index("ix_auth_audit_logs_user_id_created_at", table_name="auth_audit_logs")
        op.drop_table("auth_audit_logs")
    if "users" in tables:
        op.drop_index("ix_users_role_status", table_name="users")
        op.drop_index("ix_users_username", table_name="users")
        op.drop_table("users")
