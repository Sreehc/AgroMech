from sqlalchemy import create_engine, insert, select, update

from agromech_api.db.enums import ChunkType, DocumentStatus, TaskType
from agromech_api.db.models import (
    chunk_entity_links,
    document_chunks,
    documents,
    graph_edges,
    graph_nodes,
    metadata,
)
from agromech_api.domain.entities import process_document_entities
from agromech_api.core.config import Settings
from agromech_api.integrations.graph.rag import GraphRagService, GraphSyncError, build_graph_service
from agromech_api.ingestion import IngestFailure
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
                visibility="public",
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


def test_graph_sync_deactivates_old_edges_before_rebuild(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document_with_chunks(engine)
    service = GraphRagService(engine)
    service.sync_document("doc-1")

    with engine.begin() as connection:
        connection.execute(
            update(document_chunks)
            .where(document_chunks.c.id == "chunk-1")
            .values(content="Kubota M7040 hydraulic valve fault code E02")
        )
    process_document_entities(engine, "doc-1")
    service.sync_document("doc-1")

    with engine.connect() as connection:
        edges = connection.execute(select(graph_edges)).mappings().all()
    inactive_edges = [edge for edge in edges if not edge["is_active"]]
    active_edges = [edge for edge in edges if edge["is_active"]]
    assert inactive_edges
    assert all(edge["valid_to"] is not None for edge in inactive_edges)
    assert any("E02" in {edge["source_entity_value"], edge["target_entity_value"]} for edge in active_edges)


def test_graph_expansion_ignores_inactive_edges(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document_with_chunks(engine)
    service = GraphRagService(engine)
    service.sync_document("doc-1")
    with engine.begin() as connection:
        connection.execute(
            update(graph_edges)
            .where(graph_edges.c.source_document_id == "doc-1")
            .values(is_active=False)
        )

    candidates = service.expand(entity_type="model", value="M7040", max_hops=2)

    assert candidates == []


def test_worker_main_ingest_path_does_not_sync_graph(tmp_path) -> None:
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
                visibility="public",
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
    assert edges == []


class FakeNeo4jSession:
    def __init__(self) -> None:
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def run(self, query, **parameters):
        self.calls.append((query, parameters))
        return []


class FakeNeo4jDriver:
    def __init__(self) -> None:
        self.session_instance = FakeNeo4jSession()

    def session(self):
        return self.session_instance


class FakeNeo4jExpandRecord:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def data(self) -> dict[str, object]:
        return self.payload


class FakeNeo4jExpandSession(FakeNeo4jSession):
    def run(self, query, **parameters):
        self.calls.append((query, parameters))
        return [
            FakeNeo4jExpandRecord(
                {
                    "entity_type": "fault_code",
                    "entity_value": "E01",
                    "hop_count": 1,
                    "source_document_id": "doc-1",
                    "source_chunk_id": "chunk-1",
                    "relationship_type": "co_occurs:model:fault_code",
                    "confidence": 0.88,
                }
            ),
            FakeNeo4jExpandRecord(
                {
                    "entity_type": "component",
                    "entity_value": "hydraulic pump",
                    "hop_count": 2,
                    "source_document_id": "doc-1",
                    "source_chunk_id": None,
                    "relationship_type": "co_occurs:component:fault_code",
                    "confidence": 0.74,
                }
            ),
        ]


class FakeNeo4jExpandDriver:
    def __init__(self) -> None:
        self.session_instance = FakeNeo4jExpandSession()

    def session(self):
        return self.session_instance


def graph_settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "file_storage_backend": "local",
        "vector_backend": "local",
        "graph_backend": "neo4j",
        "model_provider": "local",
        "embedding_provider": "local",
        "graph_schema_version": "graph-v1",
    }
    base.update(overrides)
    return Settings(**base)


def test_build_graph_service_syncs_chunk_backed_relationships_to_neo4j(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document_with_chunks(engine)
    driver = FakeNeo4jDriver()
    service = build_graph_service(engine, graph_settings(), neo4j_driver=driver)

    result = service.sync_document("doc-1")

    assert result.edge_count >= 4
    calls = driver.session_instance.calls
    assert any("MERGE (source:AgroMechEntity" in query for query, _ in calls)
    assert any("MERGE (source)-[relationship:RELATED_TO" in query for query, _ in calls)
    edge_parameters = [
        parameters
        for query, parameters in calls
        if "MERGE (source)-[relationship:RELATED_TO" in query
    ][0]
    relationship_rows = edge_parameters["relationships"]
    assert relationship_rows
    assert all(row["source_document_id"] == "doc-1" for row in relationship_rows)
    assert all(row["source_chunk_id"] in {"chunk-1", "chunk-2"} for row in relationship_rows)
    assert all(row["schema_version"] == "graph-v1" for row in relationship_rows)
    assert all(row["confidence"] > 0 for row in relationship_rows)


def test_neo4j_sync_deactivates_old_document_edges_before_upsert(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document_with_chunks(engine)
    driver = FakeNeo4jDriver()
    service = build_graph_service(engine, graph_settings(), neo4j_driver=driver)

    service.sync_document("doc-1")

    first_query, first_parameters = driver.session_instance.calls[0]
    assert "MATCH ()-[relationship:RELATED_TO" in first_query
    assert first_parameters == {"document_id": "doc-1", "schema_version": "graph-v1"}


def test_neo4j_graph_expansion_returns_only_chunk_backed_candidates(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    driver = FakeNeo4jExpandDriver()
    service = build_graph_service(engine, graph_settings(), neo4j_driver=driver)

    candidates = service.expand(entity_type="model", value="M7040", max_hops=2)

    assert candidates == [
        {
            "entity_type": "fault_code",
            "entity_value": "E01",
            "hop_count": 1,
            "source_document_id": "doc-1",
            "source_chunk_id": "chunk-1",
            "relationship_type": "co_occurs:model:fault_code",
            "confidence": 0.88,
            "channel": "graph",
            "final_answer_eligible": False,
        }
    ]
    query, parameters = driver.session_instance.calls[0]
    assert "source_chunk_id IS NOT NULL" in query
    assert parameters["entity_type"] == "model"
    assert parameters["normalized_value"] == "m7040"
    assert parameters["max_hops"] == 2


class FailingGraphService:
    def sync_document(self, document_id: str):
        raise GraphSyncError("boom")


def test_worker_ignores_graph_service_failures_when_graph_is_disabled(tmp_path) -> None:
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
                visibility="public",
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
        graph_service=FailingGraphService(),
    )

    with engine.connect() as connection:
        edges = connection.execute(select(graph_edges)).mappings().all()
    assert edges == []
