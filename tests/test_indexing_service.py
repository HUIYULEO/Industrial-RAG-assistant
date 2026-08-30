import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.domain.models import (
    Base,
    Document,
    DocumentChunk,
    DocumentIndexDispatchOutbox,
    DocumentVersion,
)
from app.services.indexing_service import DocumentIndexSubmissionService, DocumentIndexingService
from app.workers import indexing_worker


class FakeEmbeddings:
    def __init__(self, failures: list[Exception] | None = None):
        self.failures = failures or []
        self.calls: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        if self.failures:
            raise self.failures.pop(0)
        return [[float(index)] for index, _ in enumerate(texts)]


class RateLimitedError(Exception):
    status_code = 429


class FakeRepository:
    def __init__(self):
        self.records: list[dict] = []

    def replace_document_version(self, records: list[dict]) -> None:
        self.records = records


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def parsed_document(db: Session) -> DocumentVersion:
    document = Document(title="Fleet Manager FS", document_type="FS", system="fleet_manager")
    version = DocumentVersion(
        document=document,
        version="1.0",
        status="draft",
        ingestion_status="parsed_pending_index",
        chunk_count=1,
    )
    version.chunks = [
        DocumentChunk(
            chunk_index=0,
            page=1,
            content="The WCS dispatches tasks.",
            content_hash="test-content-hash",
        )
    ]
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def make_service(*, embeddings: FakeEmbeddings, **kwargs) -> DocumentIndexingService:
    return DocumentIndexingService(
        db=None,  # type: ignore[arg-type]
        embeddings=embeddings,
        repository=None,  # type: ignore[arg-type]
        token_counter=len,
        **kwargs,
    )


def test_embedding_texts_are_split_by_token_budget_in_input_order():
    embeddings = FakeEmbeddings()
    service = make_service(
        embeddings=embeddings,
        batch_token_budget=6,
        tokens_per_minute=60,
    )

    vectors = service._embed_texts(["abc", "de", "fghi", "j"])

    assert embeddings.calls == [["abc", "de"], ["fghi", "j"]]
    assert vectors == [[0.0], [1.0], [0.0], [1.0]]


def test_embedding_requests_wait_when_the_rolling_token_budget_is_full():
    embeddings = FakeEmbeddings()
    now = [0.0]
    waits: list[float] = []

    def sleeper(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    service = make_service(
        embeddings=embeddings,
        batch_token_budget=5,
        tokens_per_minute=8,
        clock=lambda: now[0],
        sleeper=sleeper,
    )

    service._embed_texts(["aaaaa", "bbbbb"])

    assert waits == [60.0]
    assert embeddings.calls == [["aaaaa"], ["bbbbb"]]


def test_embedding_rate_limit_is_retried_with_backoff():
    embeddings = FakeEmbeddings(failures=[RateLimitedError("slow down")])
    waits: list[float] = []
    service = make_service(
        embeddings=embeddings,
        batch_token_budget=10,
        tokens_per_minute=100,
        max_retries=2,
        retry_base_delay_seconds=1.5,
        sleeper=waits.append,
    )

    vectors = service._embed_texts(["test"])

    assert vectors == [[0.0]]
    assert embeddings.calls == [["test"], ["test"]]
    assert waits == [1.5]


def test_index_submission_persists_queued_state_without_embedding(db: Session):
    version = parsed_document(db)

    queued = DocumentIndexSubmissionService(db).queue_document_version(version.id)

    assert queued.ingestion_status == "index_queued"
    assert queued.ingestion_error is None
    assert queued.index_dispatch_version == 1
    outbox = db.scalar(select(DocumentIndexDispatchOutbox))
    assert outbox is not None
    assert outbox.document_version_id == version.id
    assert outbox.dispatch_version == 1
    assert outbox.status == "pending"
    assert outbox.job_id is None


def test_index_submission_rejects_duplicate_active_job(db: Session):
    version = parsed_document(db)
    service = DocumentIndexSubmissionService(db)
    service.queue_document_version(version.id)

    with pytest.raises(ValueError, match="already queued or running"):
        service.queue_document_version(version.id)

    assert len(list(db.scalars(select(DocumentIndexDispatchOutbox)))) == 1


def test_worker_moves_queued_document_to_indexed(db: Session):
    version = parsed_document(db)
    DocumentIndexSubmissionService(db).queue_document_version(version.id)
    embeddings = FakeEmbeddings()
    repository = FakeRepository()
    service = DocumentIndexingService(db, embeddings, repository)

    indexed = service.index_document_version(version.id)

    assert indexed.ingestion_status == "indexed"
    assert indexed.ingestion_error is None
    assert embeddings.calls == [["The WCS dispatches tasks."]]
    assert repository.records[0]["document_version_id"] == version.id


def test_worker_persists_index_failure_for_refresh_recovery(db: Session):
    version = parsed_document(db)
    DocumentIndexSubmissionService(db).queue_document_version(version.id)
    service = DocumentIndexingService(
        db,
        FakeEmbeddings(failures=[RuntimeError("embedding provider unavailable")]),
        FakeRepository(),
    )

    with pytest.raises(RuntimeError, match="embedding provider unavailable"):
        service.index_document_version(version.id)

    db.refresh(version)
    assert version.ingestion_status == "index_failed"
    assert version.ingestion_error == "embedding provider unavailable"


def test_stale_worker_dispatch_does_not_embed_a_recovered_document(db: Session):
    version = parsed_document(db)
    DocumentIndexSubmissionService(db).queue_document_version(version.id)
    version.index_dispatch_version = 2
    version.ingestion_status = "index_queued"
    db.commit()
    embeddings = FakeEmbeddings()
    repository = FakeRepository()

    result = DocumentIndexingService(db, embeddings, repository).index_document_version(
        version.id,
        expected_dispatch_version=1,
    )

    assert result.ingestion_status == "index_queued"
    assert result.index_dispatch_version == 2
    assert embeddings.calls == []
    assert repository.records == []


def test_index_worker_uses_session_without_initialising_database(monkeypatch):
    calls: dict[str, object] = {}

    class FakeSession:
        def close(self):
            calls["closed"] = True

    class FakeIndexingService:
        def index_document_version(self, document_version_id, expected_dispatch_version):
            calls["execution"] = (document_version_id, expected_dispatch_version)

    session = FakeSession()
    monkeypatch.setattr(indexing_worker, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        indexing_worker,
        "build_document_indexing_service",
        lambda db: FakeIndexingService(),
    )

    indexing_worker.execute_document_index("document-version-1", 2)

    assert calls == {"execution": ("document-version-1", 2), "closed": True}
    assert not hasattr(indexing_worker, "initialise_database")
