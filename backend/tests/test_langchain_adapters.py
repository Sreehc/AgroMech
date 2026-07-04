from __future__ import annotations

from langchain_core.documents import Document
from sqlalchemy import create_engine

from agromech_api.rag.langchain.adapters import (
    AgroMechTextRetriever,
    AgroMechVisualPageRetriever,
    ProviderEmbeddings,
    ZvecLangChainVectorStore,
    build_answer_chain,
)
from agromech_api.integrations.vectorstores.zvec import ZvecVectorStore


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


def test_zvec_langchain_vectorstore_wraps_zvec_queries(tmp_path) -> None:
    native_store = ZvecVectorStore.from_path(tmp_path / "zvec", expected_dimension=2)
    embeddings = ProviderEmbeddings(FakeProvider())
    documents = {
        "chunk-a": Document(page_content="aaa", metadata={"chunk_id": "chunk-a"}),
        "chunk-b": Document(page_content="bbbbbb", metadata={"chunk_id": "chunk-b"}),
    }
    vector_store = ZvecLangChainVectorStore(
        native_store,
        collection="agromech_text_chunks",
        embedding=embeddings,
        document_lookup=lambda chunk_id: documents[chunk_id],
    )

    ids = vector_store.add_texts(["aaa", "bbbbbb"], ids=["chunk-a", "chunk-b"])
    results = vector_store.similarity_search("bbbbbb", k=1)

    assert ids == ["zvec://agromech_text_chunks/chunk-a", "zvec://agromech_text_chunks/chunk-b"]
    assert results == [documents["chunk-b"]]
    assert vector_store.query(collection="agromech_text_chunks", embedding=[6.0, 1.0], limit=1)[0]["chunk_id"] == "chunk-b"


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
