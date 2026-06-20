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

    index_names = {
        index.name
        for table in [documents, chunks, tasks, search_index, entity_links, graph_nodes, graph_edges, retrieval_logs]
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
