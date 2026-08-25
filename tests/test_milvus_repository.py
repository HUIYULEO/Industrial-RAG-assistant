from app.domain.evidence import RetrievalFilters
from app.repositories.milvus_repository import MilvusChunkRepository


class FakeMilvusClient:
    def has_collection(self, collection_name: str) -> bool:
        return True

    def hybrid_search(self, **kwargs):
        return [[{
            "chunk_id": "chunk-001",
            "distance": 0.42,
            "entity": {
                "chunk_id": "chunk-001",
                "document_version_id": "version-001",
                "document_title": "AMR Functional Specification",
                "document_type": "FS",
                "version": "1.0",
                "page": 12,
                "section": "Safety",
                "content": "The AMR shall enter a safe state after an emergency stop.",
            },
        }]]


def test_hybrid_search_uses_the_schema_primary_key_for_citations(monkeypatch):
    repository = MilvusChunkRepository(uri="http://milvus.test")
    monkeypatch.setattr(repository, "_client", lambda: FakeMilvusClient())

    evidence = repository.hybrid_search(
        query_text="emergency stop",
        query_vector=[0.1] * 1536,
        filters=RetrievalFilters(document_version_ids=["version-001"]),
        limit=1,
    )

    assert evidence[0].chunk_id == "chunk-001"
    assert evidence[0].section == "Safety"
