from pathlib import Path
import importlib.util
import re

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from agromech_api.db.models import ingest_tasks


def test_ingest_task_status_migration_uses_base_name_for_batch_constraint_ops() -> None:
    migration_path = Path("backend/alembic/versions/0004_add_dead_task_status.py")
    spec = importlib.util.spec_from_file_location("migration_0004_add_dead_task_status", migration_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    check_constraints = [constraint for constraint in ingest_tasks.constraints if constraint.__class__.__name__ == "CheckConstraint"]
    status_constraint_name = next(
        constraint
        for constraint in check_constraints
        if getattr(constraint, "name", None) == "ck_ingest_tasks_ingest_task_status"
    ).name

    assert status_constraint_name == "ck_ingest_tasks_ingest_task_status"
    assert migration.CONSTRAINT_NAME == "ingest_task_status"


def test_alembic_revision_ids_fit_default_version_table_limit() -> None:
    revision_pattern = re.compile(r'^revision\s*=\s*"([^"]+)"$', re.MULTILINE)
    revision_ids = []
    for migration_path in sorted(Path("backend/alembic/versions").glob("*.py")):
        content = migration_path.read_text(encoding="utf-8")
        match = revision_pattern.search(content)
        assert match, f"missing revision in {migration_path}"
        revision_ids.append((migration_path.name, match.group(1)))

    too_long = [(name, revision_id) for name, revision_id in revision_ids if len(revision_id) > 32]
    assert too_long == []


def test_pgvector_migration_file_exists() -> None:
    path = Path("backend/alembic/versions/0012_replace_zvec_with_pgvector.py")
    assert path.exists()
    contents = path.read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS vector" in contents
    assert "chunk_vector_embeddings" in contents
    assert "visual_page_vector_embeddings" in contents


def test_initial_migration_enables_pgvector_before_creating_metadata() -> None:
    path = Path("backend/alembic/versions/0001_create_core_tables.py")
    contents = path.read_text(encoding="utf-8")

    extension_position = contents.index("CREATE EXTENSION IF NOT EXISTS vector")
    create_all_position = contents.index("metadata.create_all")

    assert extension_position < create_all_position


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
    assert "qa_messages" in inspector.get_table_names()
    assert "ingest_tasks" in inspector.get_table_names()
    assert "evaluation_questions" in inspector.get_table_names()
    assert "embedding_references" not in inspector.get_table_names()
    assert "visual_page_embeddings" not in inspector.get_table_names()
    assert "chunk_vector_embeddings" in inspector.get_table_names()
    assert "visual_page_vector_embeddings" in inspector.get_table_names()

    search_columns = {column["name"] for column in inspector.get_columns("chunk_search_index")}
    chunk_embedding_columns = {column["name"] for column in inspector.get_columns("chunk_vector_embeddings")}
    visual_embedding_columns = {column["name"] for column in inspector.get_columns("visual_page_vector_embeddings")}
    graph_edge_columns = {column["name"] for column in inspector.get_columns("graph_edges")}
    document_columns = {column["name"] for column in inspector.get_columns("documents")}
    retrieval_log_columns = {column["name"] for column in inspector.get_columns("retrieval_logs")}
    assert {"embedding_version", "chunk_profile", "embedding_dimension"}.issubset(search_columns)
    assert "embedding" not in search_columns
    assert {"embedding_version", "chunk_profile", "embedding_dimension", "embedding"}.issubset(chunk_embedding_columns)
    assert {"embedding_version", "embedding_dimension", "embedding"}.issubset(visual_embedding_columns)
    assert {"schema_version", "is_active", "valid_to"}.issubset(graph_edge_columns)
    assert "document_version" in document_columns
    assert "model_config" in retrieval_log_columns

    chat_session_indexes = {index["name"] for index in inspector.get_indexes("chat_sessions")}
    assert "ix_chat_sessions_username_updated_at" in chat_session_indexes
