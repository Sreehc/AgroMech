from sqlalchemy import create_engine, insert, select

from agromech_api.db.enums import ChunkType, DocumentStatus, TaskType
from agromech_api.db.models import (
    chunk_entity_links,
    document_chunks,
    documents,
    graph_edges,
    graph_nodes,
    metadata,
)
from agromech_api.entity_extraction import process_document_entities
from agromech_api.graph_rag import GraphRagService
from agromech_api.ingestion import QueuedTask
from agromech_worker.main import process_ingest_task


def create_test_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    return engine


def seed_document_with_chunks(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id="doc-1",
                title="Manual",
                original_file_name="manual.txt",
                file_hash="hash-doc-1",
                file_size_bytes=100,
                mime_type="text/plain",
                storage_uri="file:///tmp/manual.txt",
                status=DocumentStatus.PROCESSING.value,
                created_by_role="admin",
            )
        )
        connection.execute(
            insert(document_chunks),
            [
                {
                    "id": "chunk-1",
                    "document_id": "doc-1",
                    "chunk_type": ChunkType.TEXT.value,
                    "content": "Kubota M7040 hydraulic pump fault code E01",
                    "source_locator": {"type": "text", "line_start": 1, "line_end": 1},
                },
                {
                    "id": "chunk-2",
                    "document_id": "doc-1",
                    "chunk_type": ChunkType.TEXT.value,
                    "content": "Fault code E01 requires checking part HH-123",
                    "source_locator": {"type": "text", "line_start": 2, "line_end": 2},
                },
            ],
        )
    process_document_entities(engine, "doc-1")


def test_graph_service_writes_entity_nodes_and_chunk_backed_relationships(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document_with_chunks(engine)

    result = GraphRagService(engine).sync_document("doc-1")

    assert result.node_count >= 5
    assert result.edge_count >= 4
    with engine.connect() as connection:
        nodes = connection.execute(select(graph_nodes)).mappings().all()
        edges = connection.execute(select(graph_edges)).mappings().all()
    assert ("model", "M7040") in {(node["entity_type"], node["entity_value"]) for node in nodes}
    assert ("fault_code", "E01") in {(node["entity_type"], node["entity_value"]) for node in nodes}
    assert all(edge["source_chunk_id"] in {"chunk-1", "chunk-2"} for edge in edges)
    assert all(edge["source_document_id"] == "doc-1" for edge in edges)


def test_graph_expansion_returns_one_and_two_hop_candidates_with_sources(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document_with_chunks(engine)
    service = GraphRagService(engine)
    service.sync_document("doc-1")

    candidates = service.expand(entity_type="model", value="M7040", max_hops=2)

    assert any(candidate["entity_type"] == "fault_code" and candidate["entity_value"] == "E01" for candidate in candidates)
    assert any(candidate["entity_type"] == "part_number" and candidate["entity_value"] == "HH-123" for candidate in candidates)
    assert all(candidate["source_chunk_id"] for candidate in candidates)
    assert all(candidate["channel"] == "graph" for candidate in candidates)
    assert all(candidate["final_answer_eligible"] is False for candidate in candidates)


def test_graph_sync_replaces_old_document_edges(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document_with_chunks(engine)
    service = GraphRagService(engine)
    service.sync_document("doc-1")
    with engine.begin() as connection:
        connection.execute(
            insert(chunk_entity_links).values(
                id="manual-link",
                chunk_id="chunk-1",
                document_id="doc-1",
                entity_type="component",
                entity_value="valve",
                normalized_value="valve",
                confidence=0.72,
                source="rule",
            )
        )

    service.sync_document("doc-1")

    with engine.connect() as connection:
        edges = connection.execute(select(graph_edges)).mappings().all()
    assert any("valve" in {edge["source_entity_value"], edge["target_entity_value"]} for edge in edges)


def test_worker_runs_graph_sync_after_entity_extraction(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    source_path = tmp_path / "manual.txt"
    source_path.write_text("Kubota M7040 hydraulic pump fault code E01", encoding="utf-8")
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id="doc-1",
                title="Manual",
                original_file_name="manual.txt",
                file_hash="hash-doc-1",
                file_size_bytes=source_path.stat().st_size,
                mime_type="text/plain",
                storage_uri=f"file://{source_path}",
                status=DocumentStatus.PROCESSING.value,
                created_by_role="admin",
            )
        )

    process_ingest_task(
        engine,
        QueuedTask(
            id="task-1",
            document_id="doc-1",
            task_type=TaskType.INGEST.value,
            attempt_count=0,
            stage="processing",
        ),
    )

    with engine.connect() as connection:
        edges = connection.execute(select(graph_edges)).mappings().all()
    assert edges
