from app.domain.evidence import RetrievalFilters
from app.repositories.milvus_repository import MilvusChunkRepository


class FakeMilvusClient:
    def __init__(self, *, failure: Exception | None = None):
        self.failure = failure
        self.closed = False

    def has_collection(self, collection_name: str) -> bool:
        return True

    def hybrid_search(self, **kwargs):
        if self.failure:
            raise self.failure
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

    def delete(self, **kwargs):
        if self.failure:
            raise self.failure

    def insert(self, **kwargs):
        if self.failure:
            raise self.failure

    def close(self):
        self.closed = True


def test_hybrid_search_uses_the_schema_primary_key_for_citations(monkeypatch):
    repository = MilvusChunkRepository(uri="http://milvus.test")
    client = FakeMilvusClient()
    monkeypatch.setattr(repository, "_client", lambda: client)

    evidence = repository.hybrid_search(
        query_text="emergency stop",
        query_vector=[0.1] * 1536,
        filters=RetrievalFilters(document_version_ids=["version-001"]),
        limit=1,
    )

    assert evidence[0].chunk_id == "chunk-001"
    assert evidence[0].section == "Safety"
    assert client.closed is True


def test_replace_document_version_closes_client_on_success_and_failure(monkeypatch):
    repository = MilvusChunkRepository(uri="http://milvus.test")
    records = [{"document_version_id": "version-001"}]
    success_client = FakeMilvusClient()
    monkeypatch.setattr(repository, "_client", lambda: success_client)

    repository.replace_document_version(records)

    assert success_client.closed is True

    failed_client = FakeMilvusClient(failure=RuntimeError("insert failed"))
    monkeypatch.setattr(repository, "_client", lambda: failed_client)

    import pytest

    with pytest.raises(RuntimeError, match="insert failed"):
        repository.replace_document_version(records)
    assert failed_client.closed is True


def test_hybrid_search_closes_client_on_failure(monkeypatch):
    import pytest

    repository = MilvusChunkRepository(uri="http://milvus.test")
    client = FakeMilvusClient(failure=RuntimeError("search failed"))
    monkeypatch.setattr(repository, "_client", lambda: client)

    with pytest.raises(RuntimeError, match="search failed"):
        repository.hybrid_search(
            query_text="emergency stop",
            query_vector=[0.1] * 1536,
            filters=RetrievalFilters(document_version_ids=["version-001"]),
            limit=1,
        )
    assert client.closed is True
