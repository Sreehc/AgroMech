from sqlalchemy import create_engine, insert, select

from agromech_api.db.enums import DocumentStatus, IngestTaskStatus, UserRole
from agromech_api.db.models import metadata


def test_core_tables_are_declared() -> None:
    expected_tables = {
        "documents",
        "document_chunks",
        "document_assets",
        "ingest_tasks",
        "embedding_references",
        "chunk_search_index",
        "chunk_entity_links",
        "document_entity_extractions",
        "graph_nodes",
        "graph_edges",
        "retrieval_logs",
        "qa_records",
        "answer_citations",
        "chat_sessions",
        "evaluation_runs",
    }

    assert expected_tables.issubset(metadata.tables.keys())


def test_key_indexes_are_declared() -> None:
    documents = metadata.tables["documents"]
    chunks = metadata.tables["document_chunks"]
    tasks = metadata.tables["ingest_tasks"]
    search_index = metadata.tables["chunk_search_index"]
    entity_links = metadata.tables["chunk_entity_links"]
    graph_nodes = metadata.tables["graph_nodes"]
    graph_edges = metadata.tables["graph_edges"]
    retrieval_logs = metadata.tables["retrieval_logs"]
    chat_sessions = metadata.tables["chat_sessions"]

    index_names = {
        index.name
        for table in [
            documents,
            chunks,
            tasks,
            search_index,
            entity_links,
            graph_nodes,
            graph_edges,
            retrieval_logs,
            chat_sessions,
        ]
        for index in table.indexes
    }

    assert {
        "ix_documents_status",
        "ix_documents_brand_model",
        "ix_document_chunks_document_id",
        "ix_chunk_search_index_chunk_id",
        "ix_chunk_entity_links_lookup",
        "ix_graph_nodes_lookup",
        "ix_graph_edges_chunk",
        "ix_ingest_tasks_document_id_status",
        "ix_retrieval_logs_trace_id",
        "ix_chat_sessions_username_updated_at",
    }.issubset(index_names)


def test_status_and_role_enums_are_centralized() -> None:
    assert {status.value for status in DocumentStatus} == {
        "queued",
        "processing",
        "reprocessing",
        "indexed",
        "failed",
        "deleting",
        "deleted",
    }
    assert {status.value for status in IngestTaskStatus} == {
        "queued",
        "processing",
        "succeeded",
        "failed",
        "cancelled",
    }
    assert {role.value for role in UserRole} == {"admin", "maintainer", "user", "evaluator"}


def test_chat_sessions_table_declares_required_fields() -> None:
    sessions = metadata.tables["chat_sessions"]

    assert set(sessions.c.keys()) == {
        "id",
        "username",
        "title",
        "messages",
        "filters",
        "has_image",
        "created_at",
        "updated_at",
    }
    assert sessions.c.id.primary_key
    assert not sessions.c.username.nullable
    assert not sessions.c.title.nullable
    assert not sessions.c.messages.nullable
    assert not sessions.c.has_image.nullable
    assert not sessions.c.created_at.nullable
    assert not sessions.c.updated_at.nullable


def test_chat_sessions_can_store_user_isolated_message_state() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    sessions = metadata.tables["chat_sessions"]

    with engine.begin() as connection:
        connection.execute(
            insert(sessions),
            [
                {
                    "id": "session-a",
                    "username": "admin",
                    "title": "液压提升无力",
                    "messages": [{"role": "user", "parts": [{"type": "text", "text": "如何排查？"}]}],
                    "filters": {"brand": "Kubota", "model": "M7040"},
                    "has_image": False,
                },
                {
                    "id": "session-b",
                    "username": "readonly",
                    "title": "仪表盘故障灯",
                    "messages": [{"role": "user", "parts": [{"type": "text", "text": "这个灯什么意思？"}]}],
                    "filters": {},
                    "has_image": True,
                },
            ],
        )

        admin_sessions = connection.execute(
            select(sessions.c.id, sessions.c.filters, sessions.c.has_image).where(sessions.c.username == "admin")
        ).mappings().all()

    assert admin_sessions == [
        {
            "id": "session-a",
            "filters": {"brand": "Kubota", "model": "M7040"},
            "has_image": False,
        }
    ]
