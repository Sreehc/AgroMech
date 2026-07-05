from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0011_add_document_visibility"
down_revision = "0010_visual_page_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("documents")}

    with op.batch_alter_table("documents") as batch:
        if "owner_user_id" not in columns:
            batch.add_column(sa.Column("owner_user_id", sa.String(length=36), nullable=True))
            batch.create_foreign_key(
                "fk_documents_owner_user_id_users",
                "users",
                ["owner_user_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if "visibility" not in columns:
            # 存量文档都是既有共享资料，迁移后应保持对所有登录用户与匿名访客可检索，
            # 因此先以 server_default 'public' 建列（回填存量行），随后把默认改回
            # 'private'，让迁移之后的新增上传默认进私库。
            batch.add_column(
                sa.Column(
                    "visibility",
                    sa.String(length=16),
                    nullable=False,
                    server_default="public",
                )
            )
            batch.create_check_constraint(
                "document_visibility",
                "visibility IN ('public', 'private')",
            )

    if "visibility" not in columns:
        with op.batch_alter_table("documents") as batch:
            batch.alter_column("visibility", server_default="private")

    # batch_alter_table 会按 target_metadata 重建表，可能已带上索引；重新探查避免重复创建。
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("documents")}
    if "ix_documents_visibility_owner" not in indexes:
        op.create_index(
            "ix_documents_visibility_owner",
            "documents",
            ["visibility", "owner_user_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("documents")}
    columns = {column["name"] for column in inspector.get_columns("documents")}

    if "ix_documents_visibility_owner" in indexes:
        op.drop_index("ix_documents_visibility_owner", table_name="documents")

    with op.batch_alter_table("documents") as batch:
        if "visibility" in columns:
            batch.drop_column("visibility")
        if "owner_user_id" in columns:
            batch.drop_column("owner_user_id")
