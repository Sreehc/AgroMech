from __future__ import annotations

import json
import math
from pathlib import Path

from agromech_api.core.config import Settings
class ZvecError(RuntimeError):
    """Raised when the Zvec adapter cannot complete an operation."""


class ZvecDimensionError(ZvecError):
    """Raised when a vector does not match the collection dimension."""


class ZvecVectorStore:
    """Project-local persistent vector store adapter for Zvec collections.

    The first implementation stores each collection as JSON under ``ZVEC_PATH``.
    It keeps the adapter boundary explicit while satisfying the production
    contract: persistent directory, vector refs, querying, deletion and
    dimension checks.
    """

    name = "zvec"

    def __init__(self, settings: Settings, *, expected_dimension: int | None = None) -> None:
        self.path = Path(settings.zvec_path)
        self.expected_dimension = expected_dimension or settings.embedding_dimension

    @classmethod
    def from_path(cls, path: Path, *, expected_dimension: int) -> "ZvecVectorStore":
        instance = cls.__new__(cls)
        instance.path = Path(path)
        instance.expected_dimension = expected_dimension
        return instance

    def upsert(self, *, collection: str, chunk_id: str, embedding: list[float]) -> str:
        self._validate_dimension(embedding)
        payload = self._load_collection(collection)
        dimension = payload.get("dimension")
        if dimension is None:
            payload["dimension"] = len(embedding)
        elif dimension != len(embedding):
            raise ZvecDimensionError(
                f"Zvec collection dimension {dimension} does not match vector dimension {len(embedding)}"
            )

        payload.setdefault("vectors", {})[chunk_id] = {
            "embedding": [float(value) for value in embedding],
        }
        self._save_collection(collection, payload)
        return self.vector_ref(collection, chunk_id)

    def query(
        self,
        *,
        collection: str,
        embedding: list[float],
        limit: int = 10,
    ) -> list[dict[str, object]]:
        self._validate_dimension(embedding)
        payload = self._load_collection(collection)
        dimension = payload.get("dimension")
        if dimension is not None and dimension != len(embedding):
            raise ZvecDimensionError(
                f"Zvec collection dimension {dimension} does not match query dimension {len(embedding)}"
            )

        scored = []
        for chunk_id, item in payload.get("vectors", {}).items():
            stored_embedding = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(stored_embedding, list):
                continue
            score = cosine_similarity(embedding, [float(value) for value in stored_embedding])
            if score > 0:
                scored.append(
                    {
                        "chunk_id": chunk_id,
                        "score": score,
                        "vector_ref": self.vector_ref(collection, chunk_id),
                    }
                )
        return sorted(scored, key=lambda item: item["score"], reverse=True)[:limit]

    def delete(self, *, collection: str, chunk_ids: list[str]) -> None:
        payload = self._load_collection(collection)
        vectors = payload.get("vectors")
        if not isinstance(vectors, dict):
            return
        for chunk_id in chunk_ids:
            vectors.pop(chunk_id, None)
        self._save_collection(collection, payload)

    def vector_ref(self, collection: str, chunk_id: str) -> str:
        return f"zvec://{collection}/{chunk_id}"

    def _validate_dimension(self, embedding: list[float]) -> None:
        if len(embedding) != self.expected_dimension:
            raise ZvecDimensionError(
                f"Vector dimension {len(embedding)} does not match configured {self.expected_dimension}"
            )

    def _collection_path(self, collection: str) -> Path:
        safe_name = collection.replace("/", "_")
        return self.path / f"{safe_name}.json"

    def _load_collection(self, collection: str) -> dict[str, object]:
        path = self._collection_path(collection)
        if not path.exists():
            return {"dimension": None, "vectors": {}}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ZvecError(f"Zvec collection {collection} is corrupted") from exc

    def _save_collection(self, collection: str, payload: dict[str, object]) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        path = self._collection_path(collection)
        temporary_path = path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary_path.replace(path)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def build_vector_store(settings: Settings, *, expected_dimension: int | None = None):
    if settings.vector_backend == "zvec":
        return ZvecVectorStore(settings, expected_dimension=expected_dimension)
    from agromech_api.rag.retrieval.indexing import LocalVectorStore

    return LocalVectorStore()
