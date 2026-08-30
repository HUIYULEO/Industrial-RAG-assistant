"""Redis/RQ boundary for document-indexing jobs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from app.core.config import Settings, get_settings


class DocumentIndexQueueUnavailable(RuntimeError):
    """Raised when an indexing job cannot be persisted to the queue."""


def document_index_job_id(document_version_id: str, dispatch_version: int) -> str:
    """Return an RQ-compatible deterministic ID for an indexing dispatch."""
    return f"document-index-{document_version_id}-{dispatch_version}"


@dataclass(frozen=True)
class DocumentIndexDispatch:
    document_version_id: str
    dispatch_version: int
    job_id: str


class DocumentIndexQueue(Protocol):
    def enqueue(self, dispatch: DocumentIndexDispatch) -> str: ...

    def stale_document_ids(
        self, job_ids: Mapping[str, str], *, queued_before: datetime
    ) -> set[str]: ...


@dataclass
class RedisDocumentIndexQueue:
    settings: Settings

    def _connection(self):
        from redis import Redis

        connection = Redis.from_url(
            self.settings.redis_url,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        connection.ping()
        return connection

    def enqueue(self, dispatch: DocumentIndexDispatch) -> str:
        try:
            from rq import Queue
            from rq.job import Job

            connection = self._connection()
            if Job.exists(dispatch.job_id, connection=connection):
                return dispatch.job_id
            job = Queue(self.settings.document_index_queue_name, connection=connection).enqueue(
                "app.workers.indexing_worker.execute_document_index",
                dispatch.document_version_id,
                dispatch.dispatch_version,
                job_id=dispatch.job_id,
                job_timeout=self.settings.document_index_job_timeout_seconds,
                result_ttl=3600,
                failure_ttl=7 * 24 * 3600,
                meta={
                    "document_version_id": dispatch.document_version_id,
                    "dispatch_version": dispatch.dispatch_version,
                    "producer_version": self.settings.worker_build_version,
                },
            )
            return job.id
        except Exception as exc:
            raise DocumentIndexQueueUnavailable(
                f"Redis/RQ could not accept the document indexing job: {exc}"
            ) from exc

    def stale_document_ids(
        self, job_ids: Mapping[str, str], *, queued_before: datetime
    ) -> set[str]:
        try:
            from rq.exceptions import NoSuchJobError
            from rq.job import Job

            connection = self._connection()
            stale: set[str] = set()
            for document_id, job_id in job_ids.items():
                try:
                    job = Job.fetch(job_id, connection=connection)
                    job_status = job.get_status(refresh=True)
                    if job_status in {"failed", "canceled", "stopped"}:
                        stale.add(document_id)
                        continue
                    enqueued_at = job.enqueued_at
                    if enqueued_at is not None and job_status in {
                        "queued",
                        "deferred",
                        "scheduled",
                    }:
                        if enqueued_at.tzinfo is None:
                            enqueued_at = enqueued_at.replace(tzinfo=timezone.utc)
                        if enqueued_at < queued_before:
                            stale.add(document_id)
                except NoSuchJobError:
                    stale.add(document_id)
            return stale
        except Exception as exc:
            raise DocumentIndexQueueUnavailable(
                f"Redis/RQ indexing jobs could not be inspected: {exc}"
            ) from exc


class MemoryDocumentIndexQueue:
    """Non-executing queue used by isolated API tests."""

    def enqueue(self, dispatch: DocumentIndexDispatch) -> str:
        return dispatch.job_id

    def stale_document_ids(
        self, job_ids: Mapping[str, str], *, queued_before: datetime
    ) -> set[str]:
        return set()


def get_document_index_queue() -> DocumentIndexQueue:
    settings = get_settings()
    if settings.analysis_queue_backend == "memory":
        return MemoryDocumentIndexQueue()
    if settings.analysis_queue_backend != "redis":
        raise DocumentIndexQueueUnavailable(
            "ANALYSIS_QUEUE_BACKEND must be either 'redis' or the test-only 'memory'"
        )
    return RedisDocumentIndexQueue(settings)
