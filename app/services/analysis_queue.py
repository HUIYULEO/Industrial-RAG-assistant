"""Redis/RQ dispatch for independently executable analysis items."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings, get_settings


class AnalysisQueueUnavailable(RuntimeError):
    """Raised when a requested background task cannot be persisted to Redis."""


class AnalysisQueue(Protocol):
    def enqueue_items(self, item_ids: Iterable[str]) -> dict[str, str]: ...


@dataclass
class RedisAnalysisQueue:
    settings: Settings

    def enqueue_items(self, item_ids: Iterable[str]) -> dict[str, str]:
        try:
            from redis import Redis
            from rq import Queue

            connection = Redis.from_url(self.settings.redis_url)
            connection.ping()
            queue = Queue(self.settings.analysis_queue_name, connection=connection)
            jobs = {}
            for item_id in item_ids:
                job = queue.enqueue(
                    "app.workers.analysis_worker.execute_analysis_item",
                    item_id,
                    job_timeout=self.settings.analysis_job_timeout_seconds,
                    result_ttl=3600,
                    failure_ttl=7 * 24 * 3600,
                )
                jobs[item_id] = job.id
            return jobs
        except Exception as exc:
            raise AnalysisQueueUnavailable(f"Unable to enqueue analysis items: {exc}") from exc


class MemoryAnalysisQueue:
    """No-op queue used exclusively by isolated API tests.

    It deliberately does not execute work in the FastAPI process. This keeps
    test and metadata-only environments free of Redis/OpenAI dependencies.
    """

    def enqueue_items(self, item_ids: Iterable[str]) -> dict[str, str]:
        return {item_id: f"memory-{item_id}" for item_id in item_ids}


def get_analysis_queue() -> AnalysisQueue:
    settings = get_settings()
    if settings.analysis_queue_backend == "memory":
        return MemoryAnalysisQueue()
    if settings.analysis_queue_backend != "redis":
        raise AnalysisQueueUnavailable(
            "ANALYSIS_QUEUE_BACKEND must be either 'redis' or the test-only 'memory'"
        )
    return RedisAnalysisQueue(settings)
