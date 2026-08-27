from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from rq.job import Job
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.analysis import ANALYSIS_OUTBOX_MAX_PUBLISH_ATTEMPTS
from app.domain.models import (
    AnalysisAttempt,
    AnalysisDispatchOutbox,
    AnalysisRun,
    AnalysisRunItem,
    Base,
)
from app.services.analysis_queue import (
    AnalysisQueuePermanentError,
    AnalysisQueueTransientError,
    AnalysisQueueUnavailable,
    AnalysisDispatch,
    RedisAnalysisQueue,
    analysis_job_id,
)
from app.services.analysis_reliability_service import AnalysisReliabilityService
from app.services.coverage_service import CoverageAnalysisService


class FakeQueue:
    def __init__(self):
        self.dispatches = []

    def enqueue_dispatches(self, dispatches):
        values = list(dispatches)
        self.dispatches.extend(values)
        return {dispatch.item_id: dispatch.job_id for dispatch in values}

    def stale_item_ids(self, job_ids):
        return set()


class UnavailableQueue(FakeQueue):
    def enqueue_dispatches(self, dispatches):
        raise AnalysisQueueUnavailable("Redis is unavailable")


class PermanentlyInvalidQueue(FakeQueue):
    def enqueue_dispatches(self, dispatches):
        values = list(dispatches)
        self.dispatches.extend(values)
        raise AnalysisQueuePermanentError('RQ rejected the analysis dispatch: id must not contain ":"')


def settings() -> Settings:
    return Settings(
        _env_file=None,
        analysis_queue_backend="memory",
        analysis_item_max_attempts=3,
        analysis_lease_seconds=60,
    )


def database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'reliability.db'}")
    Base.metadata.create_all(engine)
    return engine


def create_item(db: Session, *, status: str = "queued") -> AnalysisRunItem:
    run = AnalysisRun(review_package_id="review-1", status=status)
    item = AnalysisRunItem(
        analysis_run=run,
        requirement_snapshot_id="requirement-1",
        status=status,
    )
    db.add(run)
    db.commit()
    db.refresh(item)
    return item


def test_only_one_worker_can_claim_the_same_item(tmp_path):
    engine = database(tmp_path)
    first = Session(engine)
    item = create_item(first)
    item_id = item.id

    first_claim = CoverageAnalysisService(first, retrieval=None, judge=None)._claim_attempt(
        item_id,
        worker_id="worker-1",
        max_attempts=3,
        lease_seconds=60,
        expected_dispatch_version=0,
    )
    second = Session(engine)
    second_claim = CoverageAnalysisService(second, retrieval=None, judge=None)._claim_attempt(
        item_id,
        worker_id="worker-2",
        max_attempts=3,
        lease_seconds=60,
        expected_dispatch_version=0,
    )

    assert first_claim is not None
    assert second_claim is None
    assert first.get(AnalysisRunItem, item_id).attempt_count == 1
    assert len(first.scalars(select(AnalysisAttempt)).all()) == 1
    first.close()
    second.close()


def test_expired_running_lease_is_requeued_through_outbox(tmp_path):
    engine = database(tmp_path)
    db = Session(engine)
    item = create_item(db, status="running")
    item.attempt_count = 1
    item.job_id = "old-job"
    item.lease_owner = "dead-worker"
    item.heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    item.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=4)
    db.add(
        AnalysisAttempt(
            analysis_run_item_id=item.id,
            attempt_number=1,
            worker_id="dead-worker",
            status="running",
        )
    )
    db.commit()

    queue = FakeQueue()
    result = AnalysisReliabilityService(db, settings()).tick(queue)
    db.expire_all()
    recovered = db.get(AnalysisRunItem, item.id)
    attempt = db.scalar(select(AnalysisAttempt).where(AnalysisAttempt.analysis_run_item_id == item.id))

    assert result["expired_leases"] == 1
    assert result["dispatched"] == 1
    assert recovered.status == "queued"
    assert recovered.dispatch_version == 1
    assert recovered.job_id == analysis_job_id(item.id, 1)
    assert recovered.lease_owner is None
    assert attempt.status == "timed_out"
    db.close()


def test_outbox_remains_pending_when_redis_is_unavailable(tmp_path):
    engine = database(tmp_path)
    db = Session(engine)
    item = create_item(db)
    reliability = AnalysisReliabilityService(db, settings())
    reliability.ensure_pending_dispatches()

    with pytest.raises(AnalysisQueueUnavailable):
        reliability.dispatch_pending(UnavailableQueue())

    db.expire_all()
    outbox = db.scalar(select(AnalysisDispatchOutbox))
    assert outbox.status == "pending"
    assert outbox.publish_attempts == 1
    assert "Redis is unavailable" in outbox.error_message
    assert db.get(AnalysisRunItem, item.id).job_id is None
    assert "Dispatch delayed (1/8)" in db.get(AnalysisRunItem, item.id).error_message
    db.close()


def test_permanent_dispatch_error_is_blocked_after_one_attempt(tmp_path):
    engine = database(tmp_path)
    db = Session(engine)
    item = create_item(db)
    reliability = AnalysisReliabilityService(db, settings())
    reliability.ensure_pending_dispatches()
    queue = PermanentlyInvalidQueue()

    with pytest.raises(AnalysisQueuePermanentError):
        reliability.dispatch_pending(queue)

    db.expire_all()
    outbox = db.scalar(select(AnalysisDispatchOutbox))
    persisted_item = db.get(AnalysisRunItem, item.id)
    assert len(queue.dispatches) == 1
    assert outbox.status == "blocked"
    assert outbox.publish_attempts == 1
    assert persisted_item.status == "failed"
    assert "Dispatch blocked" in persisted_item.error_message
    assert db.get(AnalysisRun, persisted_item.analysis_run_id).status == "failed"
    db.close()


def test_transient_dispatch_error_moves_to_blocked_at_retry_limit(tmp_path):
    engine = database(tmp_path)
    db = Session(engine)
    item = create_item(db)
    reliability = AnalysisReliabilityService(db, settings())
    reliability.ensure_pending_dispatches()
    outbox = db.scalar(select(AnalysisDispatchOutbox))
    outbox.publish_attempts = ANALYSIS_OUTBOX_MAX_PUBLISH_ATTEMPTS - 1
    db.commit()

    with pytest.raises(AnalysisQueueUnavailable):
        reliability.dispatch_pending(UnavailableQueue())

    db.expire_all()
    outbox = db.get(AnalysisDispatchOutbox, outbox.id)
    persisted_item = db.get(AnalysisRunItem, item.id)
    assert outbox.status == "blocked"
    assert outbox.publish_attempts == ANALYSIS_OUTBOX_MAX_PUBLISH_ATTEMPTS
    assert persisted_item.status == "failed"
    assert "failed 8 times" in persisted_item.error_message
    db.close()


def test_transient_batch_failure_only_counts_the_row_actually_attempted(tmp_path):
    engine = database(tmp_path)
    db = Session(engine)
    create_item(db)
    create_item(db)
    reliability = AnalysisReliabilityService(db, settings())
    reliability.ensure_pending_dispatches()

    with pytest.raises(AnalysisQueueUnavailable):
        reliability.dispatch_pending(UnavailableQueue())

    db.expire_all()
    outboxes = list(db.scalars(select(AnalysisDispatchOutbox)))
    assert sorted(row.publish_attempts for row in outboxes) == [0, 1]
    assert all(row.status == "pending" for row in outboxes)
    db.close()


def test_deterministic_job_id_uses_rq_compatible_characters():
    job_id = analysis_job_id("11c24b54-18c8-46c6-b8c6-ddd463d3ea09", 2)
    holder = SimpleNamespace()

    Job.set_id(holder, job_id)

    assert job_id == "analysis-11c24b54-18c8-46c6-b8c6-ddd463d3ea09-2"
    assert holder._id == job_id


def test_real_rq_path_classifies_invalid_job_id_as_permanent(monkeypatch):
    class FakeRedis:
        def ping(self):
            return True

        def exists(self, _key):
            return 0

    monkeypatch.setattr(Redis, "from_url", staticmethod(lambda _url: FakeRedis()))
    dispatch = AnalysisDispatch(
        item_id="item-1",
        dispatch_version=0,
        task_schema_version=1,
        job_id="analysis:item-1:0",
    )

    with pytest.raises(AnalysisQueuePermanentError, match='id must not contain'):
        RedisAnalysisQueue(settings()).enqueue_dispatches([dispatch])


def test_redis_connection_failure_is_classified_as_transient(monkeypatch):
    class UnreachableRedis:
        def ping(self):
            raise RedisConnectionError("connection refused")

    monkeypatch.setattr(Redis, "from_url", staticmethod(lambda _url: UnreachableRedis()))

    with pytest.raises(AnalysisQueueTransientError, match="temporarily unavailable"):
        RedisAnalysisQueue(settings()).enqueue_dispatches([])
