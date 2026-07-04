import json

import pytest

from agromech_api.core.config import Settings
from agromech_api.integrations.vectorstores.zvec import ZvecDimensionError, ZvecVectorStore, build_vector_store


def zvec_settings(tmp_path, **overrides) -> Settings:
    base = {
        "_env_file": None,
        "file_storage_backend": "local",
        "graph_backend": "local",
        "vector_backend": "zvec",
        "model_provider": "local",
        "embedding_provider": "local",
        "zvec_path": str(tmp_path / "zvec"),
        "zvec_collection": "agromech_text_chunks",
        "embedding_dimension": 3,
    }
    base.update(overrides)
    return Settings(**base)


def test_zvec_upsert_persists_vectors_and_returns_traceable_ref(tmp_path) -> None:
    store = ZvecVectorStore(zvec_settings(tmp_path))

    vector_ref = store.upsert(collection="agromech_text_chunks", chunk_id="chunk-1", embedding=[1.0, 0.0, 0.0])

    assert vector_ref == "zvec://agromech_text_chunks/chunk-1"
    collection_path = tmp_path / "zvec" / "agromech_text_chunks.json"
    assert collection_path.exists()
    payload = json.loads(collection_path.read_text(encoding="utf-8"))
    assert payload["dimension"] == 3
    assert payload["vectors"]["chunk-1"]["embedding"] == [1.0, 0.0, 0.0]


def test_zvec_rejects_dimension_mismatch_without_overwriting(tmp_path) -> None:
    store = ZvecVectorStore(zvec_settings(tmp_path))
    store.upsert(collection="agromech_text_chunks", chunk_id="chunk-1", embedding=[1.0, 0.0, 0.0])

    with pytest.raises(ZvecDimensionError):
        store.upsert(collection="agromech_text_chunks", chunk_id="chunk-2", embedding=[1.0, 0.0])

    assert store.query(collection="agromech_text_chunks", embedding=[1.0, 0.0, 0.0]) == [
        {"chunk_id": "chunk-1", "score": 1.0, "vector_ref": "zvec://agromech_text_chunks/chunk-1"}
    ]


def test_zvec_query_and_delete_collection(tmp_path) -> None:
    store = ZvecVectorStore(zvec_settings(tmp_path))
    store.upsert(collection="agromech_text_chunks", chunk_id="chunk-a", embedding=[1.0, 0.0, 0.0])
    store.upsert(collection="agromech_text_chunks", chunk_id="chunk-b", embedding=[0.0, 1.0, 0.0])

    results = store.query(collection="agromech_text_chunks", embedding=[0.8, 0.2, 0.0], limit=1)

    assert results == [
        {"chunk_id": "chunk-a", "score": pytest.approx(0.9701425001), "vector_ref": "zvec://agromech_text_chunks/chunk-a"}
    ]
    store.delete(collection="agromech_text_chunks", chunk_ids=["chunk-a"])
    assert [result["chunk_id"] for result in store.query(collection="agromech_text_chunks", embedding=[0.0, 1.0, 0.0])] == [
        "chunk-b"
    ]


def test_build_vector_store_selects_zvec(tmp_path) -> None:
    store = build_vector_store(zvec_settings(tmp_path))

    assert isinstance(store, ZvecVectorStore)
