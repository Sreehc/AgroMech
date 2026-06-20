import pytest

from agromech_api.config import Settings
from agromech_api.infrastructure import dependency_targets


def test_settings_rejects_missing_required_dependency_config() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL is required"):
        Settings(database_url="")


def test_dependency_targets_include_local_infrastructure() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://agromech:agromech@db.example:5432/agromech",
        milvus_host="milvus.example",
        milvus_port=19530,
        neo4j_uri="bolt://neo4j.example:7687",
    )

    targets = dependency_targets(settings)

    assert targets["postgres"].host == "db.example"
    assert targets["postgres"].port == 5432
    assert targets["milvus"].host == "milvus.example"
    assert targets["milvus"].port == 19530
    assert targets["neo4j"].host == "neo4j.example"
    assert targets["neo4j"].port == 7687
