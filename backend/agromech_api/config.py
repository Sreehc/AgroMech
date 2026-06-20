from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    api_port: int = 8000
    database_url: str = "postgresql+psycopg://agromech:agromech@localhost:5432/agromech"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "agromech"
    postgres_user: str = "agromech"
    postgres_password: str = "agromech"
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection: str = "agromech_chunks"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "agromech"
    local_file_storage_path: str = "./storage/files"
    dependency_connect_timeout_seconds: float = 2.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator(
        "database_url",
        "milvus_host",
        "neo4j_uri",
        "neo4j_user",
        "neo4j_password",
        "local_file_storage_path",
    )
    @classmethod
    def required_non_empty(cls, value: str, info) -> str:
        if not value or not value.strip():
            env_name = info.field_name.upper()
            raise ValueError(f"{env_name} is required")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
