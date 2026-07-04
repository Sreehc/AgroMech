from __future__ import annotations


def test_rag_modules_are_available_from_structured_packages() -> None:
    from agromech_api.rag.agent.controller import AgentController
    from agromech_api.rag.agent.graph import build_agent_graph
    from agromech_api.rag.generation.answer import build_answer_generator
    from agromech_api.rag.langchain.adapters import ProviderEmbeddings
    from agromech_api.rag.retrieval.hybrid import hybrid_retrieve_with_trace
    from agromech_api.rag.retrieval.indexing import SearchIndexer

    assert AgentController.__name__ == "AgentController"
    assert build_agent_graph.__name__ == "build_agent_graph"
    assert build_answer_generator.__name__ == "build_answer_generator"
    assert ProviderEmbeddings.__name__ == "ProviderEmbeddings"
    assert hybrid_retrieve_with_trace.__name__ == "hybrid_retrieve_with_trace"
    assert SearchIndexer.__name__ == "SearchIndexer"


def test_ingestion_modules_are_available_from_structured_package() -> None:
    from agromech_api.ingestion.chunk_quality import is_referenceable_chunk
    from agromech_api.ingestion.image import process_image_document
    from agromech_api.ingestion.metadata import backfill_document_metadata
    from agromech_api.ingestion.ocr import process_ocr_document
    from agromech_api.ingestion.runner import IngestFailure
    from agromech_api.ingestion.table import process_table_document
    from agromech_api.ingestion.text import process_text_document
    from agromech_api.ingestion.vision import process_visual_observations

    assert IngestFailure.__name__ == "IngestFailure"
    assert is_referenceable_chunk.__name__ == "is_referenceable_chunk"
    assert process_image_document.__name__ == "process_image_document"
    assert backfill_document_metadata.__name__ == "backfill_document_metadata"
    assert process_ocr_document.__name__ == "process_ocr_document"
    assert process_table_document.__name__ == "process_table_document"
    assert process_text_document.__name__ == "process_text_document"
    assert process_visual_observations.__name__ == "process_visual_observations"


def test_integration_modules_are_available_from_structured_packages() -> None:
    from agromech_api.integrations.embeddings.text import build_embedding_provider
    from agromech_api.integrations.embeddings.visual import build_visual_embedding_provider
    from agromech_api.integrations.graph.rag import build_graph_service
    from agromech_api.integrations.ocr.paddleocr import PaddleOcrApiClient
    from agromech_api.integrations.queue.task_queue import build_task_publisher
    from agromech_api.integrations.storage.file_storage import build_file_storage
    from agromech_api.integrations.vectorstores.zvec import ZvecVectorStore
    from agromech_api.integrations.vectorstores.zvec_backup import backup_zvec
    from agromech_api.integrations.service_adapters import ServiceTimeouts

    assert build_embedding_provider.__name__ == "build_embedding_provider"
    assert build_visual_embedding_provider.__name__ == "build_visual_embedding_provider"
    assert build_graph_service.__name__ == "build_graph_service"
    assert PaddleOcrApiClient.__name__ == "PaddleOcrApiClient"
    assert build_task_publisher.__name__ == "build_task_publisher"
    assert build_file_storage.__name__ == "build_file_storage"
    assert ZvecVectorStore.__name__ == "ZvecVectorStore"
    assert backup_zvec.__name__ == "backup_zvec"
    assert ServiceTimeouts.__name__ == "ServiceTimeouts"


def test_api_modules_are_available_from_structured_package() -> None:
    from agromech_api.api.auth import LoginRequest, LoginResponse, register_auth_routes
    from agromech_api.api.health import register_health_routes

    assert LoginRequest.__name__ == "LoginRequest"
    assert LoginResponse.__name__ == "LoginResponse"
    assert register_auth_routes.__name__ == "register_auth_routes"
    assert register_health_routes.__name__ == "register_health_routes"


def test_core_and_cross_cutting_modules_are_available_from_structured_packages() -> None:
    from agromech_api.core.config import Settings
    from agromech_api.core.database import create_database_engine
    from agromech_api.core.errors import AppError
    from agromech_api.core.infrastructure import DependencyCheck
    from agromech_api.domain.entities import EntityExtractor
    from agromech_api.domain.model_aliases import normalize_model
    from agromech_api.evaluation.runner import run_evaluation_dataset
    from agromech_api.rag.traces import register_retrieval_trace_routes
    from agromech_api.security.auth import UserContext
    from agromech_api.sessions.history import append_text_session_exchange
    from agromech_api.sessions.routes import register_chat_session_routes

    assert Settings.__name__ == "Settings"
    assert create_database_engine.__name__ == "create_database_engine"
    assert AppError.__name__ == "AppError"
    assert DependencyCheck.__name__ == "DependencyCheck"
    assert EntityExtractor.__name__ == "EntityExtractor"
    assert normalize_model("M 7040") == "M7040"
    assert run_evaluation_dataset.__name__ == "run_evaluation_dataset"
    assert register_retrieval_trace_routes.__name__ == "register_retrieval_trace_routes"
    assert UserContext.__name__ == "UserContext"
    assert append_text_session_exchange.__name__ == "append_text_session_exchange"
    assert register_chat_session_routes.__name__ == "register_chat_session_routes"


def test_document_service_is_available_from_domain_package() -> None:
    from agromech_api.documents.routes import register_document_routes
    from agromech_api.documents.service import create_document_upload

    assert create_document_upload.__name__ == "create_document_upload"
    assert register_document_routes.__name__ == "register_document_routes"


def test_qa_modules_are_available_from_domain_package() -> None:
    from agromech_api.qa.image import answer_image_question
    from agromech_api.qa.image_routes import register_image_qa_routes
    from agromech_api.qa.text import answer_text_question
    from agromech_api.qa.text_routes import register_text_qa_routes

    assert answer_text_question.__name__ == "answer_text_question"
    assert answer_image_question.__name__ == "answer_image_question"
    assert register_text_qa_routes.__name__ == "register_text_qa_routes"
    assert register_image_qa_routes.__name__ == "register_image_qa_routes"
