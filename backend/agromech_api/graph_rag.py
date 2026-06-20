from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from uuid import uuid4

from sqlalchemy import Engine, delete, insert, select

from agromech_api.db.models import chunk_entity_links, graph_edges, graph_nodes
from agromech_api.entity_extraction import normalize


@dataclass(frozen=True)
class GraphSyncResult:
    node_count: int
    edge_count: int


class GraphRagService:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def sync_document(self, document_id: str) -> GraphSyncResult:
        with self.engine.connect() as connection:
            links = connection.execute(
                select(chunk_entity_links)
                .where(chunk_entity_links.c.document_id == document_id)
                .order_by(chunk_entity_links.c.chunk_id, chunk_entity_links.c.entity_type)
            ).mappings().all()

        with self.engine.begin() as connection:
            connection.execute(delete(graph_edges).where(graph_edges.c.source_document_id == document_id))
            node_lookup = ensure_nodes(connection, links)
            edge_rows = edge_rows_for_links(links, node_lookup)
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
            edges = connection.execute(select(graph_edges)).mappings().all()

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


def edge_rows_for_links(links, node_lookup: dict[tuple[str, str], str]) -> list[dict[str, object]]:
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
                    "confidence": min(source["confidence"], target["confidence"]),
                }
            )
    return rows


def relationship_type(left_type: str, right_type: str) -> str:
    return f"co_occurs:{left_type}:{right_type}"
