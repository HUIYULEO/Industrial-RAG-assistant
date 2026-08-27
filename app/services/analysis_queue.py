"""Redis/RQ dispatch for independently executable analysis items."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings, get_settings


class AnalysisQueueUnavailable(RuntimeError):
    """Raised when a requested background task cannot be persisted to Redis."""


class AnalysisQueueTransientError(AnalysisQueueUnavailable):
    """A temporary Redis/RQ outage that may succeed after backoff."""


class AnalysisQueuePermanentError(AnalysisQueueUnavailable):
    """A deterministic payload or configuration error that must not be retried."""


def analysis_job_id(item_id: str, dispatch_version: int) -> str:
    """Return an RQ-compatible deterministic identifier for one dispatch."""
    return f"analysis-{item_id}-{dispatch_version}"


@dataclass(frozen=True)
class AnalysisDispatch:
    item_id: str
    dispatch_version: int
    task_schema_version: int
    job_id: str


class AnalysisQueue(Protocol):
    def enqueue_dispatches(self, dispatches: Iterable[AnalysisDispatch]) -> dict[str, str]: ...

    def stale_item_ids(self, job_ids: Mapping[str, str]) -> set[str]: ...


@dataclass
class RedisAnalysisQueue:
    settings: Settings

    def enqueue_dispatches(self, dispatches: Iterable[AnalysisDispatch]) -> dict[str, str]:
        try:
            from redis import Redis
            from redis.exceptions import RedisError
            from rq import Queue
            from rq.job import Job

            connection = Redis.from_url(self.settings.redis_url)
            connection.ping()
            queue = Queue(self.settings.analysis_queue_name, connection=connection)
            jobs = {}
            for dispatch in dispatches:
                if Job.exists(dispatch.job_id, connection=connection):
                    jobs[dispatch.item_id] = dispatch.job_id
                    continue
                job = queue.enqueue(
                    "app.workers.analysis_worker.execute_analysis_item",
                    dispatch.item_id,
                    dispatch.dispatch_version,
                    dispatch.task_schema_version,
                    job_id=dispatch.job_id,
                    job_timeout=self.settings.analysis_job_timeout_seconds,
                    result_ttl=3600,
                    failure_ttl=7 * 24 * 3600,
                    meta={
                        "analysis_item_id": dispatch.item_id,
                        "dispatch_version": dispatch.dispatch_version,
                        "task_schema_version": dispatch.task_schema_version,
                        "producer_version": self.settings.worker_build_version,
                    },
                )
                jobs[dispatch.item_id] = job.id
            return jobs
        except (ValueError, TypeError, ImportError, AttributeError) as exc:
            raise AnalysisQueuePermanentError(
                f"RQ rejected the analysis dispatch: {exc}"
            ) from exc
        except (RedisError, OSError, TimeoutError) as exc:
            raise AnalysisQueueTransientError(
                f"Redis/RQ is temporarily unavailable: {exc}"
            ) from exc
        except AnalysisQueueUnavailable:
            raise
        except Exception as exc:
            raise AnalysisQueuePermanentError(
                f"Unexpected analysis queue error: {exc}"
            ) from exc

    def stale_item_ids(self, job_ids: Mapping[str, str]) -> set[str]:
        """Return queued DB items whose RQ job can no longer execute.

        A worker can fail before it records an item-level failure (for example,
        an import error during process startup).  Those jobs are retained in
        RQ's failed registry while the durable workboard incorrectly says
        ``queued``.  They are safe to dispatch again.
        """
        try:
            from redis import Redis
            from rq.exceptions import NoSuchJobError
            from rq.job import Job

            connection = Redis.from_url(self.settings.redis_url)
            connection.ping()
            stale: set[str] = set()
            for item_id, job_id in job_ids.items():
                try:
                    job = Job.fetch(job_id, connection=connection)
                    if job.get_status(refresh=True) in {"failed", "canceled", "stopped"}:
                        stale.add(item_id)
                except NoSuchJobError:
                    stale.add(item_id)
            return stale
        except AnalysisQueueUnavailable:
            raise
        except Exception as exc:
            raise AnalysisQueueUnavailable(f"Unable to inspect analysis jobs: {exc}") from exc


class MemoryAnalysisQueue:
    """No-op queue used exclusively by isolated API tests.

    It deliberately does not execute work in the FastAPI process. This keeps
    test and metadata-only environments free of Redis/OpenAI dependencies.
    """

    def enqueue_dispatches(self, dispatches: Iterable[AnalysisDispatch]) -> dict[str, str]:
        return {dispatch.item_id: dispatch.job_id for dispatch in dispatches}

    def stale_item_ids(self, job_ids: Mapping[str, str]) -> set[str]:
        return set()


def get_analysis_queue() -> AnalysisQueue:
    settings = get_settings()
    if settings.analysis_queue_backend == "memory":
        return MemoryAnalysisQueue()
    if settings.analysis_queue_backend != "redis":
        raise AnalysisQueueUnavailable(
            "ANALYSIS_QUEUE_BACKEND must be either 'redis' or the test-only 'memory'"
        )
    return RedisAnalysisQueue(settings)
