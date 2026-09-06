"""Background execution boundary for staged document parsing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fastapi import BackgroundTasks

from app.core.config import Settings, get_settings


class DocumentIngestionQueueUnavailable(RuntimeError):
    """Raised when a staged document cannot be submitted for parsing."""


class DocumentIngestionQueue(Protocol):
    def enqueue(self, document_version_id: str) -> None: ...


def _execute_in_process(document_version_id: str) -> None:
    """Run a local background parse without surfacing post-response failures."""
    from app.core.logging_config import get_logger
    from app.workers.ingestion_worker import execute_document_ingestion

    try:
        execute_document_ingestion(document_version_id)
    except Exception:
        get_logger(__name__).exception(
            "Background document parsing failed for document %s",
            document_version_id,
        )


@dataclass
class InProcessDocumentIngestionQueue:
    background_tasks: BackgroundTasks

    def enqueue(self, document_version_id: str) -> None:
        self.background_tasks.add_task(_execute_in_process, document_version_id)


@dataclass
class RedisDocumentIngestionQueue:
    settings: Settings

    def enqueue(self, document_version_id: str) -> None:
        try:
            from redis import Redis
            from rq import Queue

            connection = Redis.from_url(
                self.settings.redis_url,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
            connection.ping()
            Queue(
                self.settings.document_index_queue_name,
                connection=connection,
            ).enqueue(
                "app.workers.ingestion_worker.execute_document_ingestion",
                document_version_id,
                job_timeout=self.settings.document_index_job_timeout_seconds,
                result_ttl=3600,
                failure_ttl=7 * 24 * 3600,
                meta={
                    "document_version_id": document_version_id,
                    "producer_version": self.settings.worker_build_version,
                },
            )
        except Exception as exc:
            raise DocumentIngestionQueueUnavailable(
                f"Redis/RQ could not accept the document parsing job: {exc}"
            ) from exc


def get_document_ingestion_queue(
    background_tasks: BackgroundTasks,
) -> DocumentIngestionQueue:
    settings = get_settings()
    if settings.analysis_queue_backend == "memory":
        return InProcessDocumentIngestionQueue(background_tasks)
    if settings.analysis_queue_backend != "redis":
        raise DocumentIngestionQueueUnavailable(
            "ANALYSIS_QUEUE_BACKEND must be either 'redis' or the test-only 'memory'"
        )
    return RedisDocumentIngestionQueue(settings)
