from enum import StrEnum


class DocumentStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    REPROCESSING = "reprocessing"
    INDEXED = "indexed"
    FAILED = "failed"
    DELETING = "deleting"
    DELETED = "deleted"


class IngestTaskStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UserRole(StrEnum):
    ADMIN = "admin"
    MAINTAINER = "maintainer"
    USER = "user"
    EVALUATOR = "evaluator"


class TaskType(StrEnum):
    INGEST = "ingest"
    REPROCESS = "reprocess"
    DELETE = "delete"


class ChunkType(StrEnum):
    TEXT = "text"
    TABLE = "table"
    IMAGE = "image"


class AssetType(StrEnum):
    PAGE_IMAGE = "page_image"
    SOURCE_IMAGE = "source_image"
    EXTRACTED_IMAGE = "extracted_image"
