from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import rq
from redis import Redis
from rq.job import Job
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.models import (
    Base,
    Document,
    DocumentChunk,
    DocumentIndexDispatchOutbox,
    DocumentVersion,
)
from app.services.indexing_queue import (
    DocumentIndexDispatch,
    DocumentIndexQueueUnavailable,
    RedisDocumentIndexQueue,
    document_index_job_id,
)
from app.services.indexing_reliability_service import IndexingReliabilityService
from app.services.indexing_service import DocumentIndexSubmissionService


class FakeQueue:
    def __init__(self, *, stale_ids: set[str] | None = None):
        self.dispatches: list[DocumentIndexDispatch] = []
        self.stale_ids = stale_ids or set()

    def enqueue(self, dispatch: DocumentIndexDispatch) -> str:
        self.dispatches.append(dispatch)
        return dispatch.job_id

    def stale_document_ids(self, job_ids, *, queued_before):
        return set(job_ids) & self.stale_ids


class UnavailableQueue(FakeQueue):
    def enqueue(self, dispatch: DocumentIndexDispatch) -> str:
        raise DocumentIndexQueueUnavailable("Redis is unavailable")


def settings() -> Settings:
    return Settings(
        _env_file=None,
        analysis_queue_backend="memory",
        document_index_queued_timeout_seconds=60,
        document_index_stale_after_seconds=120,
        document_index_max_attempts=3,
    )


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'indexing-reliability.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def parsed_document(db: Session) -> DocumentVersion:
    version = DocumentVersion(
        document=Document(title="Fleet Manager FS", document_type="FS", system="fleet_manager"),
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
            content_hash="index-reliability-content",
        )
    ]
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def test_maintenance_dispatches_transactional_outbox_with_deterministic_job_id(db: Session):
    version = parsed_document(db)
    DocumentIndexSubmissionService(db).queue_document_version(version.id)
    queue = FakeQueue()

    result = IndexingReliabilityService(db, settings()).tick(queue)

    db.expire_all()
    persisted = db.get(DocumentVersion, version.id)
    outbox = db.scalar(select(DocumentIndexDispatchOutbox))
    expected_job_id = document_index_job_id(version.id, 1)
    assert result["dispatched"] == 1
    assert queue.dispatches == [
        DocumentIndexDispatch(
            document_version_id=version.id,
            dispatch_version=1,
            job_id=expected_job_id,
        )
    ]
    assert persisted.ingestion_status == "index_queued"
    assert persisted.index_job_id == expected_job_id
    assert outbox.status == "dispatched"
    assert outbox.job_id == expected_job_id


def test_maintenance_backfills_legacy_queued_document_without_outbox(db: Session):
    version = parsed_document(db)
    version.ingestion_status = "index_queued"
    db.commit()

    created = IndexingReliabilityService(db, settings()).ensure_pending_dispatches()

    outbox = db.scalar(select(DocumentIndexDispatchOutbox))
    assert created == 1
    assert outbox.document_version_id == version.id
    assert outbox.dispatch_version == 0
    assert outbox.status == "pending"


def test_outbox_stays_pending_when_index_queue_is_unavailable(db: Session):
    version = parsed_document(db)
    DocumentIndexSubmissionService(db).queue_document_version(version.id)
    reliability = IndexingReliabilityService(db, settings())

    with pytest.raises(DocumentIndexQueueUnavailable):
        reliability.dispatch_pending(UnavailableQueue())

    db.expire_all()
    outbox = db.scalar(select(DocumentIndexDispatchOutbox))
    assert outbox.status == "pending"
    assert outbox.publish_attempts == 1
    assert "Redis is unavailable" in outbox.error_message
    assert db.get(DocumentVersion, version.id).index_job_id is None


def test_stale_queued_job_is_reissued_with_a_new_dispatch_version(db: Session):
    version = parsed_document(db)
    DocumentIndexSubmissionService(db).queue_document_version(version.id)
    reliability = IndexingReliabilityService(db, settings())
    reliability.dispatch_pending(FakeQueue())

    recovered = reliability.recover_stale_queued_jobs(FakeQueue(stale_ids={version.id}))
    reliability.dispatch_pending(FakeQueue())

    db.expire_all()
    persisted = db.get(DocumentVersion, version.id)
    outboxes = list(
        db.scalars(
            select(DocumentIndexDispatchOutbox).order_by(
                DocumentIndexDispatchOutbox.dispatch_version
            )
        )
    )
    assert recovered == 1
    assert persisted.ingestion_status == "index_queued"
    assert persisted.index_dispatch_version == 2
    assert persisted.index_job_id == document_index_job_id(version.id, 2)
    assert [item.status for item in outboxes] == ["stale", "dispatched"]


def test_stuck_indexing_job_is_requeued_after_timeout(db: Session):
    version = parsed_document(db)
    DocumentIndexSubmissionService(db).queue_document_version(version.id)
    reliability = IndexingReliabilityService(db, settings())
    reliability.dispatch_pending(FakeQueue())
    version.ingestion_status = "indexing"
    version.index_started_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    db.commit()

    recovered = reliability.recover_stuck_indexing()

    db.expire_all()
    persisted = db.get(DocumentVersion, version.id)
    assert recovered == 1
    assert persisted.ingestion_status == "index_queued"
    assert persisted.index_dispatch_version == 2
    assert persisted.index_job_id is None
    assert persisted.index_started_at is None
    assert db.scalar(
        select(DocumentIndexDispatchOutbox).where(
            DocumentIndexDispatchOutbox.dispatch_version == 2
        )
    ).status == "pending"


def test_recovery_stops_after_configured_attempt_limit(db: Session):
    version = parsed_document(db)
    version.ingestion_status = "indexing"
    version.index_dispatch_version = 3
    version.index_job_id = "expired-job"
    version.index_started_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    db.add(
        DocumentIndexDispatchOutbox(
            document_version_id=version.id,
            dispatch_version=3,
            status="dispatched",
            job_id="expired-job",
        )
    )
    db.commit()

    recovered = IndexingReliabilityService(db, settings()).recover_stuck_indexing()

    db.refresh(version)
    assert recovered == 1
    assert version.ingestion_status == "index_failed"
    assert "Maximum indexing attempts (3) reached" in version.ingestion_error


def test_document_index_job_id_is_rq_compatible_and_deterministic():
    job_id = document_index_job_id("11c24b54-18c8-46c6-b8c6-ddd463d3ea09", 2)
    holder = SimpleNamespace()

    Job.set_id(holder, job_id)

    assert job_id == "document-index-11c24b54-18c8-46c6-b8c6-ddd463d3ea09-2"
    assert holder._id == job_id


def test_redis_index_queue_uses_short_timeouts_ping_and_existing_job_idempotency(
    monkeypatch,
):
    calls = []

    class FakeRedis:
        pings = 0

        def ping(self):
            self.pings += 1

    connection = FakeRedis()

    def from_url(url, **kwargs):
        calls.append((url, kwargs))
        return connection

    monkeypatch.setattr(Redis, "from_url", staticmethod(from_url))
    monkeypatch.setattr(Job, "exists", staticmethod(lambda job_id, connection: True))
    dispatch = DocumentIndexDispatch(
        document_version_id="document-1",
        dispatch_version=2,
        job_id="document-index-document-1-2",
    )

    job_id = RedisDocumentIndexQueue(settings()).enqueue(dispatch)

    assert job_id == dispatch.job_id
    assert calls == [
        (
            settings().redis_url,
            {"socket_connect_timeout": 1, "socket_timeout": 1},
        )
    ]
    assert connection.pings == 1


def test_redis_index_queue_enqueues_expected_worker_payload(monkeypatch):
    captured = {}

    class FakeRedis:
        def ping(self):
            pass

    class FakeQueue:
        def __init__(self, name, *, connection):
            captured["queue"] = (name, connection)

        def enqueue(self, function_path, document_id, dispatch_version, **kwargs):
            captured["enqueue"] = (
                function_path,
                document_id,
                dispatch_version,
                kwargs,
            )
            return SimpleNamespace(id=kwargs["job_id"])

    connection = FakeRedis()
    queue_settings = settings()
    monkeypatch.setattr(
        Redis,
        "from_url",
        staticmethod(lambda _url, **_kwargs: connection),
    )
    monkeypatch.setattr(Job, "exists", staticmethod(lambda job_id, connection: False))
    monkeypatch.setattr(rq, "Queue", FakeQueue)
    dispatch = DocumentIndexDispatch(
        document_version_id="document-2",
        dispatch_version=3,
        job_id="document-index-document-2-3",
    )

    job_id = RedisDocumentIndexQueue(queue_settings).enqueue(dispatch)

    assert job_id == dispatch.job_id
    assert captured["queue"] == (queue_settings.document_index_queue_name, connection)
    assert captured["enqueue"] == (
        "app.workers.indexing_worker.execute_document_index",
        "document-2",
        3,
        {
            "job_id": dispatch.job_id,
            "job_timeout": queue_settings.document_index_job_timeout_seconds,
            "result_ttl": 3600,
            "failure_ttl": 7 * 24 * 3600,
            "meta": {
                "document_version_id": "document-2",
                "dispatch_version": 3,
                "producer_version": queue_settings.worker_build_version,
            },
        },
    )


def test_redis_index_queue_detects_missing_terminal_and_timed_out_jobs(monkeypatch):
    from rq.exceptions import NoSuchJobError

    queued_before = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    old = queued_before - timedelta(seconds=1)
    fresh = queued_before + timedelta(seconds=1)

    class FakeRedis:
        def ping(self):
            pass

    class FakeJob:
        def __init__(self, status, enqueued_at):
            self.status = status
            self.enqueued_at = enqueued_at

        def get_status(self, *, refresh):
            assert refresh is True
            return self.status

    jobs = {
        "failed-job": FakeJob("failed", fresh),
        "canceled-job": FakeJob("canceled", fresh),
        "stopped-job": FakeJob("stopped", fresh),
        "old-queued-job": FakeJob("queued", old.replace(tzinfo=None)),
        "old-deferred-job": FakeJob("deferred", old),
        "old-scheduled-job": FakeJob("scheduled", old),
        "fresh-queued-job": FakeJob("queued", fresh),
        "old-started-job": FakeJob("started", old),
    }

    def fetch(job_id, *, connection):
        if job_id == "missing-job":
            raise NoSuchJobError(job_id)
        return jobs[job_id]

    monkeypatch.setattr(
        Redis,
        "from_url",
        staticmethod(lambda _url, **_kwargs: FakeRedis()),
    )
    monkeypatch.setattr(Job, "fetch", staticmethod(fetch))
    job_ids = {
        "missing": "missing-job",
        "failed": "failed-job",
        "canceled": "canceled-job",
        "stopped": "stopped-job",
        "old-queued": "old-queued-job",
        "old-deferred": "old-deferred-job",
        "old-scheduled": "old-scheduled-job",
        "fresh-queued": "fresh-queued-job",
        "old-started": "old-started-job",
    }

    stale = RedisDocumentIndexQueue(settings()).stale_document_ids(
        job_ids, queued_before=queued_before
    )

    assert stale == {
        "missing",
        "failed",
        "canceled",
        "stopped",
        "old-queued",
        "old-deferred",
        "old-scheduled",
    }


def test_redis_index_queue_translates_connection_errors(monkeypatch):
    queue = RedisDocumentIndexQueue(settings())

    def fail_connection():
        raise OSError("connection refused")

    monkeypatch.setattr(queue, "_connection", fail_connection)
    dispatch = DocumentIndexDispatch(
        document_version_id="document-3",
        dispatch_version=1,
        job_id="document-index-document-3-1",
    )

    with pytest.raises(DocumentIndexQueueUnavailable, match="could not accept"):
        queue.enqueue(dispatch)
    with pytest.raises(DocumentIndexQueueUnavailable, match="could not be inspected"):
        queue.stale_document_ids(
            {"document-3": dispatch.job_id},
            queued_before=datetime.now(timezone.utc),
        )
