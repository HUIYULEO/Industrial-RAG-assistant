"""Redis/RQ boundary for document-indexing jobs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings, get_settings


class DocumentIndexQueueUnavailable(RuntimeError):
    """Raised when an indexing job cannot be persisted to the queue."""


class DocumentIndexQueue(Protocol):
    def enqueue(self, document_version_id: str) -> str: ...


@dataclass
class RedisDocumentIndexQueue:
    settings: Settings

    def enqueue(self, document_version_id: str) -> str:
        try:
            from redis import Redis
            from rq import Queue

            connection = Redis.from_url(
                self.settings.redis_url,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
            connection.ping()
            job = Queue(self.settings.document_index_queue_name, connection=connection).enqueue(
                "app.workers.indexing_worker.execute_document_index",
                document_version_id,
                job_timeout=self.settings.document_index_job_timeout_seconds,
                result_ttl=3600,
                failure_ttl=7 * 24 * 3600,
                meta={
                    "document_version_id": document_version_id,
                    "producer_version": self.settings.worker_build_version,
                },
            )
            return job.id
        except Exception as exc:
            raise DocumentIndexQueueUnavailable(
                f"Redis/RQ could not accept the document indexing job: {exc}"
            ) from exc


class MemoryDocumentIndexQueue:
    """Non-executing queue used by isolated API tests."""

    def enqueue(self, document_version_id: str) -> str:
        return f"memory-index-{document_version_id}"


def get_document_index_queue() -> DocumentIndexQueue:
    settings = get_settings()
    if settings.analysis_queue_backend == "memory":
        return MemoryDocumentIndexQueue()
    if settings.analysis_queue_backend != "redis":
        raise DocumentIndexQueueUnavailable(
            "ANALYSIS_QUEUE_BACKEND must be either 'redis' or the test-only 'memory'"
        )
    return RedisDocumentIndexQueue(settings)
