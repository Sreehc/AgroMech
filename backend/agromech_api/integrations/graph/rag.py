from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import combinations
from uuid import uuid4

from sqlalchemy import Engine, insert, select, update

from agromech_api.core.config import Settings
from agromech_api.db.models import chunk_entity_links, graph_edges, graph_nodes
from agromech_api.domain.entities import normalize


@dataclass(frozen=True)
class GraphSyncResult:
    node_count: int
    edge_count: int


class GraphSyncError(RuntimeError):
    """Raised when a configured graph backend cannot sync a document."""


class GraphRagService:
    def __init__(self, engine: Engine, *, schema_version: str = "graph-v1") -> None:
        self.engine = engine
        self.schema_version = schema_version

    def sync_document(self, document_id: str) -> GraphSyncResult:
        with self.engine.connect() as connection:
            links = connection.execute(
                select(chunk_entity_links)
                .where(chunk_entity_links.c.document_id == document_id)
                .order_by(chunk_entity_links.c.chunk_id, chunk_entity_links.c.entity_type)
            ).mappings().all()

        with self.engine.begin() as connection:
            expire_document_edges(connection, document_id)
            node_lookup = ensure_nodes(connection, links)
            edge_rows = edge_rows_for_links(links, node_lookup, schema_version=self.schema_version)
            if edge_rows:
                connection.execute(insert(graph_edges), edge_rows)

        return GraphSyncResult(node_count=len({(link["entity_type"], link["normalized_value"]) for link in links}), edge_count=len(edge_rows))

    def expand(self, *, entity_type: str, value: str, max_hops: int = 2) -> list[dict[str, object]]:
        normalized_value = normalize(value)
        with self.engine.connect() as connection:
            start_node = connection.execute(
                select(graph_nodes)
                .where(graph_nodes.c.entity_type == entity_type)
                .where(graph_nodes.c.normalized_value == normalized_value)
            ).mappings().one_or_none()
            if start_node is None:
                return []
            edges = connection.execute(select(graph_edges).where(graph_edges.c.is_active.is_(True))).mappings().all()

        candidates: dict[tuple[str, str, str], dict[str, object]] = {}
        frontier = {start_node["id"]}
        visited = {start_node["id"]}
        for hop in range(1, max_hops + 1):
            next_frontier = set()
            for edge in edges:
                neighbor = None
                source_side = edge["source_node_id"] in frontier
                target_side = edge["target_node_id"] in frontier
                if source_side:
                    neighbor = {
                        "node_id": edge["target_node_id"],
                        "entity_type": edge["target_entity_type"],
                        "entity_value": edge["target_entity_value"],
                    }
                elif target_side:
                    neighbor = {
                        "node_id": edge["source_node_id"],
                        "entity_type": edge["source_entity_type"],
                        "entity_value": edge["source_entity_value"],
                    }
                if neighbor is None:
                    continue
                if neighbor["node_id"] not in visited:
                    next_frontier.add(neighbor["node_id"])
                key = (neighbor["entity_type"], neighbor["entity_value"], edge["source_chunk_id"])
                candidates.setdefault(
                    key,
                    {
                        "entity_type": neighbor["entity_type"],
                        "entity_value": neighbor["entity_value"],
                        "hop_count": hop,
                        "source_document_id": edge["source_document_id"],
                        "source_chunk_id": edge["source_chunk_id"],
                        "relationship_type": edge["relationship_type"],
                        "confidence": edge["confidence"],
                        "channel": "graph",
                        "final_answer_eligible": False,
                    },
                )
            visited.update(next_frontier)
            frontier = next_frontier
            if not frontier:
                break
        return sorted(candidates.values(), key=lambda item: (item["hop_count"], item["entity_type"], item["entity_value"]))


def ensure_nodes(connection, links) -> dict[tuple[str, str], str]:
    node_lookup: dict[tuple[str, str], str] = {}
    for link in links:
        key = (link["entity_type"], link["normalized_value"])
        if key in node_lookup:
            continue
        existing = connection.execute(
            select(graph_nodes.c.id)
            .where(graph_nodes.c.entity_type == link["entity_type"])
            .where(graph_nodes.c.normalized_value == link["normalized_value"])
        ).scalar_one_or_none()
        if existing:
            node_lookup[key] = existing
            continue
        node_id = str(uuid4())
        connection.execute(
            insert(graph_nodes).values(
                id=node_id,
                entity_type=link["entity_type"],
                entity_value=link["entity_value"],
                normalized_value=link["normalized_value"],
            )
        )
        node_lookup[key] = node_id
    return node_lookup


def expire_document_edges(connection, document_id: str) -> None:
    connection.execute(
        update(graph_edges)
        .where(graph_edges.c.source_document_id == document_id)
        .where(graph_edges.c.is_active.is_(True))
        .values(is_active=False, valid_to=datetime.now(UTC))
    )


def edge_rows_for_links(
    links,
    node_lookup: dict[tuple[str, str], str],
    *,
    schema_version: str = "graph-v1",
) -> list[dict[str, object]]:
    grouped: dict[str, list] = {}
    for link in links:
        grouped.setdefault(link["chunk_id"], []).append(link)

    rows = []
    seen = set()
    for chunk_id, chunk_links in grouped.items():
        for left, right in combinations(chunk_links, 2):
            left_key = (left["entity_type"], left["normalized_value"])
            right_key = (right["entity_type"], right["normalized_value"])
            if left_key == right_key:
                continue
            source, target = sorted([left, right], key=lambda item: (item["entity_type"], item["entity_value"]))
            unique_key = (
                source["entity_type"],
                source["normalized_value"],
                target["entity_type"],
                target["normalized_value"],
                chunk_id,
            )
            if unique_key in seen:
                continue
            seen.add(unique_key)
            rows.append(
                {
                    "id": str(uuid4()),
                    "source_node_id": node_lookup[(source["entity_type"], source["normalized_value"])],
                    "target_node_id": node_lookup[(target["entity_type"], target["normalized_value"])],
                    "source_entity_type": source["entity_type"],
                    "source_entity_value": source["entity_value"],
                    "target_entity_type": target["entity_type"],
                    "target_entity_value": target["entity_value"],
                    "relationship_type": relationship_type(source["entity_type"], target["entity_type"]),
                    "source_document_id": source["document_id"],
                    "source_chunk_id": chunk_id,
                    "schema_version": schema_version,
                    "confidence": min(source["confidence"], target["confidence"]),
                    "is_active": True,
                    "valid_to": None,
                }
            )
    return rows


def relationship_type(left_type: str, right_type: str) -> str:
    return f"co_occurs:{left_type}:{right_type}"


class Neo4jGraphRagService:
    def __init__(self, engine: Engine, settings: Settings, *, driver=None) -> None:
        self.engine = engine
        self.settings = settings
        self.driver = driver or create_neo4j_driver(settings)

    def sync_document(self, document_id: str) -> GraphSyncResult:
        with self.engine.connect() as connection:
            links = connection.execute(
                select(chunk_entity_links)
                .where(chunk_entity_links.c.document_id == document_id)
                .order_by(chunk_entity_links.c.chunk_id, chunk_entity_links.c.entity_type)
            ).mappings().all()

        node_rows = node_rows_for_links(links, schema_version=self.settings.graph_schema_version)
        node_lookup = {
            (row["entity_type"], row["normalized_value"]): row["node_key"]
            for row in node_rows
        }
        relationship_rows = edge_rows_for_links(
            links,
            node_lookup,
            schema_version=self.settings.graph_schema_version,
        )

        try:
            with self.driver.session() as session:
                session.run(
                    """
                    MATCH ()-[relationship:RELATED_TO {
                        source_document_id: $document_id,
                        schema_version: $schema_version
                    }]-()
                    WHERE relationship.is_active = true
                    SET relationship.is_active = false,
                        relationship.valid_to = datetime()
                    """,
                    document_id=document_id,
                    schema_version=self.settings.graph_schema_version,
                )
                session.run(
                    """
                    UNWIND $nodes AS row
                    MERGE (source:AgroMechEntity {node_key: row.node_key})
                    SET source.entity_type = row.entity_type,
                        source.entity_value = row.entity_value,
                        source.normalized_value = row.normalized_value,
                        source.schema_version = row.schema_version
                    """,
                    nodes=node_rows,
                )
                if relationship_rows:
                    session.run(
                        """
                        UNWIND $relationships AS row
                        MATCH (source:AgroMechEntity {node_key: row.source_node_id})
                        MATCH (target:AgroMechEntity {node_key: row.target_node_id})
                        MERGE (source)-[relationship:RELATED_TO {
                            source_document_id: row.source_document_id,
                            source_chunk_id: row.source_chunk_id,
                            relationship_type: row.relationship_type,
                            schema_version: row.schema_version,
                            source_entity_type: row.source_entity_type,
                            source_entity_value: row.source_entity_value,
                            target_entity_type: row.target_entity_type,
                            target_entity_value: row.target_entity_value
                        }]->(target)
                        SET relationship.confidence = row.confidence,
                            relationship.is_active = row.is_active
                        """,
                        relationships=relationship_rows,
                    )
        except Exception as exc:
            raise GraphSyncError(f"Neo4j graph sync failed for document {document_id}") from exc

        return GraphSyncResult(node_count=len(node_rows), edge_count=len(relationship_rows))

    def expand(self, *, entity_type: str, value: str, max_hops: int = 2) -> list[dict[str, object]]:
        normalized_value = normalize(value)
        try:
            with self.driver.session() as session:
                records = session.run(
                    """
                    MATCH (start:AgroMechEntity {
                        entity_type: $entity_type,
                        normalized_value: $normalized_value,
                        schema_version: $schema_version
                    })
                    MATCH path = (start)-[relationship:RELATED_TO*1..2]-(neighbor:AgroMechEntity)
                    WHERE length(path) <= $max_hops
                      AND ALL(edge IN relationship WHERE edge.is_active = true AND edge.source_chunk_id IS NOT NULL)
                    WITH neighbor, relationship, length(path) AS hop_count
                    WITH neighbor, relationship[hop_count - 1] AS evidence_edge, hop_count
                    RETURN neighbor.entity_type AS entity_type,
                           neighbor.entity_value AS entity_value,
                           hop_count AS hop_count,
                           evidence_edge.source_document_id AS source_document_id,
                           evidence_edge.source_chunk_id AS source_chunk_id,
                           evidence_edge.relationship_type AS relationship_type,
                           evidence_edge.confidence AS confidence
                    ORDER BY hop_count ASC, entity_type ASC, entity_value ASC
                    """,
                    entity_type=entity_type,
                    normalized_value=normalized_value,
                    schema_version=self.settings.graph_schema_version,
                    max_hops=max_hops,
                )
        except Exception as exc:
            raise GraphSyncError("Neo4j graph expansion failed") from exc

        candidates: list[dict[str, object]] = []
        for record in records:
            row = record.data() if hasattr(record, "data") else dict(record)
            if not row.get("source_chunk_id"):
                continue
            candidates.append(
                {
                    "entity_type": row["entity_type"],
                    "entity_value": row["entity_value"],
                    "hop_count": row["hop_count"],
                    "source_document_id": row["source_document_id"],
                    "source_chunk_id": row["source_chunk_id"],
                    "relationship_type": row["relationship_type"],
                    "confidence": row["confidence"],
                    "channel": "graph",
                    "final_answer_eligible": False,
                }
            )
        return candidates


def node_rows_for_links(links, *, schema_version: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen = set()
    for link in links:
        key = (link["entity_type"], link["normalized_value"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "node_key": graph_node_key(link["entity_type"], link["normalized_value"]),
                "entity_type": link["entity_type"],
                "entity_value": link["entity_value"],
                "normalized_value": link["normalized_value"],
                "schema_version": schema_version,
            }
        )
    return rows


def graph_node_key(entity_type: str, normalized_value: str) -> str:
    return f"{entity_type}:{normalized_value}"


def create_neo4j_driver(settings: Settings):
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:
        raise GraphSyncError("neo4j package is required when GRAPH_BACKEND=neo4j") from exc
    return GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )


def build_graph_service(engine: Engine, settings: Settings, *, neo4j_driver=None):
    if settings.graph_backend == "neo4j":
        return Neo4jGraphRagService(engine, settings, driver=neo4j_driver)
    return GraphRagService(engine)
