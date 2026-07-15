from sqlalchemy import create_engine, insert, select

from agromech_api.db.enums import DocumentStatus, IngestTaskStatus, UserRole
from agromech_api.db.models import metadata


def test_core_tables_are_declared() -> None:
    expected_tables = {
        "documents",
        "users",
        "auth_audit_logs",
        "document_chunks",
        "document_assets",
        "ingest_tasks",
        "chunk_vector_embeddings",
        "visual_page_vector_embeddings",
        "chunk_search_index",
        "chunk_entity_links",
        "document_entity_extractions",
        "graph_nodes",
        "graph_edges",
        "retrieval_logs",
        "qa_records",
        "qa_messages",
        "answer_citations",
        "chat_sessions",
        "evaluation_questions",
        "evaluation_runs",
    }

    assert expected_tables.issubset(metadata.tables.keys())


def test_key_indexes_are_declared() -> None:
    documents = metadata.tables["documents"]
    chunks = metadata.tables["document_chunks"]
    tasks = metadata.tables["ingest_tasks"]
    search_index = metadata.tables["chunk_search_index"]
    chunk_vector_embeddings = metadata.tables["chunk_vector_embeddings"]
    visual_page_vector_embeddings = metadata.tables["visual_page_vector_embeddings"]
    entity_links = metadata.tables["chunk_entity_links"]
    graph_nodes = metadata.tables["graph_nodes"]
    graph_edges = metadata.tables["graph_edges"]
    retrieval_logs = metadata.tables["retrieval_logs"]
    chat_sessions = metadata.tables["chat_sessions"]
    qa_messages = metadata.tables["qa_messages"]
    evaluation_questions = metadata.tables["evaluation_questions"]
    users = metadata.tables["users"]
    auth_audit_logs = metadata.tables["auth_audit_logs"]

    index_names = {
        index.name
        for table in [
            documents,
            chunks,
            tasks,
            search_index,
            chunk_vector_embeddings,
            visual_page_vector_embeddings,
            entity_links,
            graph_nodes,
            graph_edges,
            retrieval_logs,
            chat_sessions,
            qa_messages,
            evaluation_questions,
            users,
            auth_audit_logs,
        ]
        for index in table.indexes
    }

    assert {
        "ix_documents_status",
        "ix_documents_brand_model",
        "ix_document_chunks_document_id",
        "ix_chunk_search_index_chunk_id_version",
        "ix_chunk_vector_embeddings_chunk_version",
        "ix_visual_page_vector_embeddings_asset_version",
        "ix_chunk_entity_links_lookup",
        "ix_graph_nodes_lookup",
        "ix_graph_edges_chunk",
        "ix_ingest_tasks_document_id_status",
        "ix_retrieval_logs_trace_id",
        "ix_chat_sessions_username_updated_at",
        "ix_qa_messages_session_id_created_at",
        "ix_evaluation_questions_dataset_version",
        "ix_users_username",
        "ix_auth_audit_logs_user_id_created_at",
    }.issubset(index_names)


def test_users_table_declares_database_auth_fields() -> None:
    users = metadata.tables["users"]

    assert {
        "id",
        "username",
        "password_hash",
        "role",
        "status",
        "display_name",
        "last_login_at",
        "password_changed_at",
        "token_version",
        "created_at",
        "updated_at",
    }.issubset(users.c.keys())
    assert users.c.username.nullable is False
    assert users.c.password_hash.nullable is False
    assert users.c.token_version.nullable is False


def test_auth_audit_logs_table_declares_login_audit_fields() -> None:
    audit_logs = metadata.tables["auth_audit_logs"]

    assert {
        "id",
        "user_id",
        "username",
        "event_type",
        "success",
        "ip_address",
        "user_agent",
        "metadata",
        "created_at",
    }.issubset(audit_logs.c.keys())


def test_embedding_tables_declare_version_fields() -> None:
    search_index = metadata.tables["chunk_search_index"]
    chunk_embeddings = metadata.tables["chunk_vector_embeddings"]

    for table in [search_index, chunk_embeddings]:
        assert {"embedding_version", "chunk_profile", "embedding_dimension"}.issubset(table.c.keys())
        assert table.c.embedding_version.nullable is False
        assert table.c.chunk_profile.nullable is False
        assert table.c.embedding_dimension.nullable is False


def test_visual_page_embeddings_table_declares_visual_index_fields() -> None:
    visual_embeddings = metadata.tables["visual_page_vector_embeddings"]

    assert {
        "id",
        "asset_id",
        "document_id",
        "page_number",
        "provider",
        "model",
        "embedding_version",
        "embedding_dimension",
        "embedding",
        "status",
        "created_at",
    }.issubset(visual_embeddings.c.keys())
    assert visual_embeddings.c.asset_id.nullable is False
    assert visual_embeddings.c.document_id.nullable is False
    assert visual_embeddings.c.embedding_version.nullable is False
    assert visual_embeddings.c.embedding_dimension.nullable is False


def test_pgvector_tables_replace_external_vector_references() -> None:
    assert "embedding_references" not in metadata.tables
    assert "visual_page_embeddings" not in metadata.tables
    assert "chunk_vector_embeddings" in metadata.tables
    assert "visual_page_vector_embeddings" in metadata.tables


def test_chunk_vector_embeddings_table_declares_pgvector_fields() -> None:
    table = metadata.tables["chunk_vector_embeddings"]
    columns = table.c

    assert "chunk_id" in columns
    assert "document_id" in columns
    assert "embedding" in columns
    assert "embedding_version" in columns
    assert "chunk_profile" in columns
    assert "embedding_dimension" in columns
    assert "status" in columns


def test_visual_page_vector_embeddings_table_declares_pgvector_fields() -> None:
    table = metadata.tables["visual_page_vector_embeddings"]
    columns = table.c

    assert "asset_id" in columns
    assert "document_id" in columns
    assert "page_number" in columns
    assert "embedding" in columns
    assert "embedding_version" in columns
    assert "embedding_dimension" in columns
    assert "status" in columns


def test_retrieval_logs_declare_model_config_field() -> None:
    retrieval_logs = metadata.tables["retrieval_logs"]

    assert "model_config" in retrieval_logs.c.keys()
    assert retrieval_logs.c.model_config.nullable is False
    assert retrieval_logs.c.retrieval_round.nullable is False
    assert retrieval_logs.c.citation_status.nullable is False


def test_documents_table_declares_document_version_field() -> None:
    documents = metadata.tables["documents"]

    assert "document_version" in documents.c.keys()
    assert documents.c.document_version.nullable is True


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
        "dead",
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


def test_qa_messages_and_evaluation_questions_tables_declare_required_fields() -> None:
    qa_messages = metadata.tables["qa_messages"]
    evaluation_questions = metadata.tables["evaluation_questions"]

    assert {"id", "session_id", "role", "content", "metadata", "created_at"}.issubset(qa_messages.c.keys())
    assert qa_messages.c.session_id.nullable is False
    assert qa_messages.c.role.nullable is False
    assert qa_messages.c.content.nullable is False

    assert {
        "id",
        "question_id",
        "dataset_version",
        "category",
        "question",
        "expected_model",
        "expected_answer_summary",
        "expected_sources",
        "requires_safety_warning",
        "must_not_include",
        "created_at",
    }.issubset(evaluation_questions.c.keys())
    assert evaluation_questions.c.question_id.nullable is False
    assert evaluation_questions.c.dataset_version.nullable is False
    assert evaluation_questions.c.question.nullable is False
