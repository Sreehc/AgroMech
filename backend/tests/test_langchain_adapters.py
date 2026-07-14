from __future__ import annotations

from langchain_core.documents import Document
from sqlalchemy import create_engine

from agromech_api.rag.langchain.adapters import (
    AgroMechTextRetriever,
    AgroMechVisualPageRetriever,
    ProviderEmbeddings,
    build_answer_chain,
)


class FakeProvider:
    provider = "fake"
    model = "fake-embedding"

    def embed(self, text: str) -> list[float]:
        return [float(len(text)), 1.0]


def test_provider_embeddings_exposes_langchain_and_project_interfaces() -> None:
    embeddings = ProviderEmbeddings(FakeProvider())

    assert embeddings.embed_query("abc") == [3.0, 1.0]
    assert embeddings.embed_documents(["a", "abcd"]) == [[1.0, 1.0], [4.0, 1.0]]
    assert embeddings.embed("abc") == [3.0, 1.0]
    assert embeddings.provider == "fake"
    assert embeddings.model == "fake-embedding"


def test_provider_embeddings_wraps_project_embedding_provider() -> None:
    class Provider:
        provider = "local"
        model = "deterministic"

        def embed(self, text: str) -> list[float]:
            return [float(len(text))]

    embeddings = ProviderEmbeddings(Provider())

    assert embeddings.embed_query("abc") == [3.0]
    assert embeddings.embed_documents(["a", "abcd"]) == [[1.0], [4.0]]


def test_text_retriever_returns_langchain_documents_and_original_payload() -> None:
    engine = create_engine("sqlite:///:memory:")

    def retrieve_payload(**kwargs):
        return {
            "status": "ok",
            "final_evidence": [
                {
                    "chunk_id": "chunk-1",
                    "document_id": "doc-1",
                    "chunk_type": "text",
                    "content": "replace hydraulic filter",
                    "source_locator": {"page": 3},
                    "score": 0.9,
                }
            ],
        }

    retriever = AgroMechTextRetriever(
        engine=engine,
        retrieve_payload_fn=retrieve_payload,
        filters={"model": "M7040"},
        trace_id="trace-1",
    )

    documents = retriever.invoke("filter")
    payload = retriever.retrieve_payload("filter")

    assert documents == [
        Document(
            page_content="replace hydraulic filter",
            metadata={
                "evidence_type": "text",
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "chunk_type": "text",
                "source_locator": {"page": 3},
                "score": 0.9,
            },
        )
    ]
    assert payload["status"] == "ok"


def test_text_retriever_forwards_original_question_and_rewrite_trace() -> None:
    captured = {}

    def retrieve_payload(**kwargs):
        captured.update(kwargs)
        return {"status": "ok", "final_evidence": []}

    retriever = AgroMechTextRetriever(
        engine=None,
        retrieve_payload_fn=retrieve_payload,
        original_question="M7040 的 E01 怎么修？",
        query_rewrite={"provider": "bailian", "fallback": False},
    )
    retriever.retrieve_payload("M7040 E01 hydraulic pump")

    assert captured["question"] == "M7040 E01 hydraulic pump"
    assert captured["original_question"] == "M7040 的 E01 怎么修？"
    assert captured["query_rewrite"] == {"provider": "bailian", "fallback": False}


def test_visual_retriever_returns_visual_page_documents_and_original_payload() -> None:
    engine = create_engine("sqlite:///:memory:")

    def retrieve_payload(**kwargs):
        return {
            "status": "ok",
            "final_evidence": [
                {
                    "evidence_type": "visual_page",
                    "asset_id": "asset-1",
                    "document_id": "doc-1",
                    "page_number": 7,
                    "ocr_text": "belt routing diagram",
                    "image_uri": "file:///tmp/page-7.png",
                    "score": 0.8,
                }
            ],
        }

    retriever = AgroMechVisualPageRetriever(
        engine=engine,
        retrieve_payload_fn=retrieve_payload,
        filters={},
        trace_id="trace-1",
    )

    documents = retriever.invoke("图中皮带位置")
    payload = retriever.retrieve_payload("图中皮带位置")

    assert documents[0].page_content == "belt routing diagram"
    assert documents[0].metadata["evidence_type"] == "visual_page"
    assert documents[0].metadata["asset_id"] == "asset-1"
    assert payload["final_evidence"][0]["page_number"] == 7


def test_answer_chain_invokes_generator_through_lcel() -> None:
    class FakeGenerator:
        def generate(self, **kwargs):
            return {
                "answer": f"answer: {kwargs['question']}",
                "sections": {"conclusion": "ok"},
            }

    chain = build_answer_chain(FakeGenerator())

    result = chain.invoke(
        {
            "question": "how to inspect",
            "citations": [],
            "safety_warnings": [],
            "uncertainty": {"level": "low", "reasons": []},
            "filters": {},
        }
    )

    assert result["answer"] == "answer: how to inspect"
    assert result["sections"] == {"conclusion": "ok"}
