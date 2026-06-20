from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_alembic_migration_can_run_repeatedly(tmp_path: Path) -> None:
    database_path = tmp_path / "agromech.db"
    config = Config("alembic.ini")
    config.set_main_option("script_location", "backend/alembic")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

    command.upgrade(config, "head")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path}")
    inspector = inspect(engine)

    assert "documents" in inspector.get_table_names()
    assert "document_chunks" in inspector.get_table_names()
    assert "chat_sessions" in inspector.get_table_names()
    assert "ingest_tasks" in inspector.get_table_names()

    chat_session_indexes = {index["name"] for index in inspector.get_indexes("chat_sessions")}
    assert "ix_chat_sessions_username_updated_at" in chat_session_indexes
