from functools import lru_cache
import os

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Application
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    api_port: int = 8000
    frontend_port: int = 3000
    worker_concurrency: int = 1

    # Auth and permissions
    auth_token_secret: str = "change-me"
    session_ttl_minutes: int = 720

    # Next.js frontend server-side adapter (consumed by the frontend, loaded here
    # so a single .env stays the source of truth and nothing is silently dropped).
    agromech_api_base_url: str = "http://127.0.0.1:8000"

    # Upload limits
    upload_max_file_size_mb: int = 100
    upload_max_image_size_mb: int = 20
    upload_max_concurrent: int = 2
    document_library_max_size_gb: int = 5

    # File storage
    file_storage_backend: str = "local"
    local_file_storage_path: str = "./.agromech-data/storage/files"
    document_delete_mode: str = "soft_delete"

    # Aliyun OSS
    oss_region: str = "cn-beijing"
    oss_endpoint: str = "https://oss-cn-beijing.aliyuncs.com"
    oss_bucket: str = "agromech-rag-dev-cn-beijing"
    oss_access_key_id: str = ""
    oss_access_key_secret: str = ""
    oss_prefix: str = "agromech/dev"
    oss_signed_url_ttl_seconds: int = 600
    oss_download_signed_url_ttl_seconds: int = 3600

    # API, external service and task timeouts
    api_request_timeout_seconds: float = 60.0
    dependency_connect_timeout_seconds: float = 2.0
    upload_timeout_seconds: float = 300.0
    ingestion_task_timeout_seconds: float = 1800.0
    retrieval_timeout_seconds: float = 30.0
    llm_request_timeout_seconds: float = 120.0
    embedding_timeout_seconds: float = 30.0
    vision_timeout_seconds: float = 60.0
    rerank_timeout_seconds: float = 8.0
    evaluation_task_timeout_seconds: float = 3600.0

    # Ingestion decisions
    table_pdf_mode: str = "text_or_ocr"
    ocr_provider: str = "paddleocr"
    ocr_engine: str = "paddleocr"
    vision_confidence_threshold: float = 0.55
    max_qa_question_chars: int = 2000
    max_images_per_question: int = 1

    # OCR text-extraction strategy: "legacy" keeps the local OCR + pypdf text
    # path; "cloud_text" routes PDFs through the PaddleOCR cloud API for
    # text-only recognition (every page's text → TEXT chunks → text index).
    ocr_text_mode: str = "legacy"

    # OCR: PaddleOCR cloud API (Baidu AI Studio hosted, async job model)
    paddleocr_api_base_url: str = "https://paddleocr.aistudio-app.com"
    paddleocr_api_token: str = ""
    paddleocr_api_model: str = "PaddleOCR-VL-1.6"
    paddleocr_submit_timeout_seconds: float = 60.0
    paddleocr_poll_interval_seconds: float = 5.0
    paddleocr_poll_timeout_seconds: float = 1800.0

    # RabbitMQ task dispatch
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/%2F"
    rabbitmq_queue: str = "agromech.ingest"
    rabbitmq_exchange: str = ""
    rabbitmq_routing_key: str = "agromech.ingest"
    rabbitmq_publish_enabled: bool = False
    rabbitmq_consume_prefetch: int = 1
    rabbitmq_reconnect_seconds: float = 5.0

    # PostgreSQL
    database_url: str = "postgresql+psycopg://agromech:agromech@localhost:5432/agromech"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "agromech"
    postgres_user: str = "agromech"
    postgres_password: str = "agromech"

    # Legacy vector target retained for health checks until Zvec health lands (T57).
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection: str = "agromech_chunks"

    # Vector storage: Zvec
    vector_backend: str = "zvec"
    zvec_path: str = "./.agromech-data/zvec"
    zvec_collection: str = "agromech_chunks"
    zvec_backup_path: str = "./.agromech-data/backups/zvec"
    zvec_backup_retention_days: int = 7

    # Embedding
    embedding_provider: str = "local"
    embedding_model: str = "text-embedding-v4"
    embedding_dimension: int = 1024
    embedding_batch_size: int = 8
    embedding_max_retries: int = 3
    embedding_retry_backoff_seconds: str = "1,2,4"
    embedding_version: str = "emb_202606231530_text-embedding-v4_1024_chunk-v1"
    chunk_profile: str = "chunk-v1"

    # Graph: disabled from the main product path; local keeps Neo4j optional.
    graph_backend: str = "local"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "agromech"
    graph_max_hops: int = 2
    graph_schema_version: str = "graph-v1"
    graph_auto_accept_rule_confidence: float = 0.90
    graph_auto_accept_llm_confidence: float = 0.85
    graph_review_min_confidence: float = 0.65

    # Model services: Aliyun Bailian
    model_provider: str = "local"
    bailian_api_key: str = ""
    bailian_base_url: str = ""
    llm_model: str = "qwen3.7-plus"
    llm_fallback_model: str = "qwen3.6-flash"
    vision_model: str = "qwen3.7-plus"
    rerank_model: str = "qwen3-rerank"

    # Rerank
    rerank_enabled: bool = True
    rerank_top_k: int = 30
    final_evidence_limit: int = 5
    rerank_degrade_on_failure: bool = True

    # Retrieval degradation policy
    retrieval_degrade_on_optional_channel_failure: bool = True
    optional_retrieval_channels: str = "vision,rerank"

    # Evaluation
    evaluation_runner_mode: str = "cli"
    evaluation_default_dataset: str = "curated-mvp"
    evaluation_target_question_count: int = 30
    evaluation_top_k: int = 5
    evaluation_target_top5_source_hit_rate: float = 0.80
    evaluation_target_citation_accuracy: float = 0.70
    evaluation_target_model_confusion_rate: float = 0.10

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        if os.getenv("CI", "").lower() == "true":
            return init_settings, env_settings, file_secret_settings
        return init_settings, env_settings, dotenv_settings, file_secret_settings

    @field_validator(
        "database_url",
        "milvus_host",
        "neo4j_uri",
        "neo4j_user",
        "neo4j_password",
        "local_file_storage_path",
        "auth_token_secret",
    )
    @classmethod
    def required_non_empty(cls, value: str, info) -> str:
        if not value or not value.strip():
            env_name = info.field_name.upper()
            raise ValueError(f"{env_name} is required")
        return value

    @model_validator(mode="after")
    def validate_backend_modes(self) -> "Settings":
        """Require credentials only for the backends that are actually selected.

        Keeping the checks conditional lets local development run with the
        deterministic fallbacks while still failing fast and readably once a
        real backend (OSS, Zvec, Neo4j, Bailian) is switched on.
        """
        if self.file_storage_backend == "oss":
            require_settings(
                self,
                ["oss_access_key_id", "oss_access_key_secret", "oss_bucket", "oss_endpoint"],
                mode="FILE_STORAGE_BACKEND=oss",
            )
        if self.vector_backend == "zvec":
            require_settings(self, ["zvec_path", "zvec_collection"], mode="VECTOR_BACKEND=zvec")
        if self.graph_backend == "neo4j":
            require_settings(self, ["neo4j_uri", "neo4j_user", "neo4j_password"], mode="GRAPH_BACKEND=neo4j")
        if "bailian" in {self.model_provider, self.embedding_provider}:
            require_settings(self, ["bailian_api_key", "bailian_base_url"], mode="provider=bailian")
        if self.final_evidence_limit > self.rerank_top_k:
            raise ValueError("FINAL_EVIDENCE_LIMIT must be <= RERANK_TOP_K")
        ensure_probability(self.graph_review_min_confidence, "GRAPH_REVIEW_MIN_CONFIDENCE")
        ensure_probability(
            self.graph_auto_accept_llm_confidence,
            "GRAPH_AUTO_ACCEPT_LLM_CONFIDENCE",
        )
        ensure_probability(
            self.graph_auto_accept_rule_confidence,
            "GRAPH_AUTO_ACCEPT_RULE_CONFIDENCE",
        )
        ensure_probability(
            self.evaluation_target_top5_source_hit_rate,
            "EVALUATION_TARGET_TOP5_SOURCE_HIT_RATE",
        )
        ensure_probability(
            self.evaluation_target_citation_accuracy,
            "EVALUATION_TARGET_CITATION_ACCURACY",
        )
        ensure_probability(
            self.evaluation_target_model_confusion_rate,
            "EVALUATION_TARGET_MODEL_CONFUSION_RATE",
        )
        if self.graph_review_min_confidence > self.graph_auto_accept_llm_confidence:
            raise ValueError(
                "GRAPH_REVIEW_MIN_CONFIDENCE must be <= GRAPH_AUTO_ACCEPT_LLM_CONFIDENCE"
            )
        if self.graph_auto_accept_llm_confidence > self.graph_auto_accept_rule_confidence:
            raise ValueError(
                "GRAPH_AUTO_ACCEPT_LLM_CONFIDENCE must be <= GRAPH_AUTO_ACCEPT_RULE_CONFIDENCE"
            )
        return self

    @property
    def embedding_retry_backoff(self) -> list[float]:
        return [float(part) for part in split_csv(self.embedding_retry_backoff_seconds)]

    @property
    def optional_retrieval_channel_list(self) -> list[str]:
        return split_csv(self.optional_retrieval_channels)


def require_settings(settings: "Settings", field_names: list[str], *, mode: str) -> None:
    missing = []
    for field_name in field_names:
        value = getattr(settings, field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field_name.upper())
    if missing:
        raise ValueError(f"{', '.join(missing)} required when {mode}")


def split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def ensure_probability(value: float, env_name: str) -> None:
    if not 0 <= value <= 1:
        raise ValueError(f"{env_name} must be between 0 and 1")


@lru_cache
def get_settings() -> Settings:
    return Settings()
