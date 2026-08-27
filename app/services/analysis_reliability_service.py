"""Durable dispatch, reconciliation, and lease recovery for analysis work."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.analysis import (
    ANALYSIS_OUTBOX_MAX_PUBLISH_ATTEMPTS,
    ANALYSIS_OUTBOX_RETRY_DELAYS_SECONDS,
    CURRENT_ANALYSIS_TASK_SCHEMA_VERSION,
    apply_run_status,
)
from app.domain.models import AnalysisAttempt, AnalysisDispatchOutbox, AnalysisRun, AnalysisRunItem
from app.services.analysis_queue import (
    AnalysisDispatch,
    AnalysisQueue,
    AnalysisQueuePermanentError,
    AnalysisQueueUnavailable,
    analysis_job_id,
)


def renew_analysis_lease(
    db: Session, item_id: str, worker_id: str, lease_seconds: int
) -> bool:
    """Extend one item's lease, reporting whether this worker still owns it.

    The executing worker and its heartbeat thread both renew the same lease.
    Neither commits here: each caller applies its own policy for a lost lease.
    """
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(AnalysisRunItem)
        .where(
            AnalysisRunItem.id == item_id,
            AnalysisRunItem.status == "running",
            AnalysisRunItem.lease_owner == worker_id,
        )
        .values(
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
        )
    )
    return bool(result.rowcount)


class AnalysisReliabilityService:
    """Keep PostgreSQL task state and Redis/RQ delivery convergent."""

    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def ensure_pending_dispatches(self, *, limit: int = 200) -> int:
        """Backfill an Outbox row for legacy queued items without a live job."""
        items = list(
            self.db.scalars(
                select(AnalysisRunItem)
                .where(AnalysisRunItem.status == "queued", AnalysisRunItem.job_id.is_(None))
                .limit(limit)
            )
        )
        created = 0
        for item in items:
            existing = self.db.scalar(
                select(AnalysisDispatchOutbox.id).where(
                    AnalysisDispatchOutbox.analysis_run_item_id == item.id,
                    AnalysisDispatchOutbox.dispatch_version == item.dispatch_version,
                )
            )
            if existing is None:
                self.db.add(
                    AnalysisDispatchOutbox(
                        analysis_run_item_id=item.id,
                        dispatch_version=item.dispatch_version,
                        task_schema_version=CURRENT_ANALYSIS_TASK_SCHEMA_VERSION,
                    )
                )
                created += 1
        if created:
            self.db.commit()
        return created

    def dispatch_pending(self, queue: AnalysisQueue, *, limit: int = 100) -> int:
        """Publish committed Outbox rows with deterministic, idempotent job IDs."""
        now = self._now()
        statement = (
            select(AnalysisDispatchOutbox)
            .where(
                AnalysisDispatchOutbox.status == "pending",
                AnalysisDispatchOutbox.available_at <= now,
            )
            .order_by(AnalysisDispatchOutbox.created_at, AnalysisDispatchOutbox.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list(self.db.scalars(statement))
        if not rows:
            return 0
        dispatched = 0
        affected_runs: set[str] = set()
        deferred_error: AnalysisQueueUnavailable | None = None
        for index, row in enumerate(rows):
            dispatch = AnalysisDispatch(
                item_id=row.analysis_run_item_id,
                dispatch_version=row.dispatch_version,
                task_schema_version=row.task_schema_version,
                job_id=analysis_job_id(row.analysis_run_item_id, row.dispatch_version),
            )
            try:
                job_ids = queue.enqueue_dispatches([dispatch])
            except AnalysisQueuePermanentError as exc:
                self._block_dispatch(row, str(exc), now, affected_runs)
                deferred_error = exc
                continue
            except AnalysisQueueUnavailable as exc:
                row.publish_attempts += 1
                if row.publish_attempts >= ANALYSIS_OUTBOX_MAX_PUBLISH_ATTEMPTS:
                    self._block_dispatch(
                        row,
                        (
                            f"Redis/RQ delivery failed {row.publish_attempts} times; "
                            f"last error: {exc}"
                        ),
                        now,
                        affected_runs,
                        increment_attempt=False,
                    )
                else:
                    row.error_message = str(exc)
                    retry_at = now + timedelta(seconds=self._outbox_retry_delay(row.publish_attempts))
                    row.available_at = retry_at
                    item = self.db.get(AnalysisRunItem, row.analysis_run_item_id)
                    if item is not None and item.status == "queued":
                        item.error_message = (
                            f"Dispatch delayed ({row.publish_attempts}/"
                            f"{ANALYSIS_OUTBOX_MAX_PUBLISH_ATTEMPTS}): {exc}"
                        )
                    # This Redis failure is a circuit-breaker signal for this
                    # maintenance cycle. Defer unattempted rows without falsely
                    # incrementing their individual counters.
                    for untouched in rows[index + 1 :]:
                        untouched.available_at = retry_at
                deferred_error = exc
                break

            job_id = job_ids.get(row.analysis_run_item_id)
            if not job_id:
                exc = AnalysisQueuePermanentError(
                    "RQ did not return a job ID for the dispatched analysis item"
                )
                self._block_dispatch(row, str(exc), now, affected_runs)
                deferred_error = exc
                continue
            if job_id != dispatch.job_id:
                exc = AnalysisQueuePermanentError(
                    f"RQ returned unexpected job ID {job_id!r}; expected {dispatch.job_id!r}"
                )
                self._block_dispatch(row, str(exc), now, affected_runs)
                deferred_error = exc
                continue
            result = self.db.execute(
                update(AnalysisRunItem)
                .where(
                    AnalysisRunItem.id == row.analysis_run_item_id,
                    AnalysisRunItem.status == "queued",
                    AnalysisRunItem.dispatch_version == row.dispatch_version,
                    AnalysisRunItem.job_id.is_(None),
                )
                .values(job_id=job_id, error_message=None)
            )
            row.status = "dispatched"
            row.job_id = job_id
            row.publish_attempts += 1
            row.error_message = None
            row.dispatched_at = now
            if result.rowcount:
                dispatched += 1
        if affected_runs:
            self._reconcile_runs(affected_runs, now)
        self.db.commit()
        if deferred_error is not None:
            raise deferred_error
        return dispatched

    @staticmethod
    def _outbox_retry_delay(attempt_number: int) -> int:
        index = min(attempt_number - 1, len(ANALYSIS_OUTBOX_RETRY_DELAYS_SECONDS) - 1)
        return ANALYSIS_OUTBOX_RETRY_DELAYS_SECONDS[index]

    def _block_dispatch(
        self,
        row: AnalysisDispatchOutbox,
        message: str,
        now: datetime,
        affected_runs: set[str],
        *,
        increment_attempt: bool = True,
    ) -> None:
        if increment_attempt:
            row.publish_attempts += 1
        row.status = "blocked"
        row.error_message = message
        item = self.db.get(AnalysisRunItem, row.analysis_run_item_id)
        if (
            item is None
            or item.status != "queued"
            or item.dispatch_version != row.dispatch_version
        ):
            return
        item.status = "failed"
        item.job_id = None
        item.error_message = f"Dispatch blocked: {message}"
        item.completed_at = now
        affected_runs.add(item.analysis_run_id)

    def recover_stale_queued_jobs(self, queue: AnalysisQueue, *, limit: int = 200) -> int:
        """Schedule a new dispatch when a queued item's RQ job is terminal or missing."""
        items = list(
            self.db.scalars(
                select(AnalysisRunItem)
                .where(AnalysisRunItem.status == "queued", AnalysisRunItem.job_id.is_not(None))
                .limit(limit)
            )
        )
        job_ids = {item.id: item.job_id for item in items if item.job_id}
        if not job_ids:
            return 0
        stale_ids = queue.stale_item_ids(job_ids)
        recovered = 0
        for item in items:
            if item.id not in stale_ids or not item.job_id:
                continue
            old_job_id = item.job_id
            result = self.db.execute(
                update(AnalysisRunItem)
                .where(
                    AnalysisRunItem.id == item.id,
                    AnalysisRunItem.status == "queued",
                    AnalysisRunItem.job_id == old_job_id,
                )
                .values(
                    job_id=None,
                    dispatch_version=AnalysisRunItem.dispatch_version + 1,
                    error_message=None,
                )
                .returning(AnalysisRunItem.dispatch_version)
            )
            next_version = result.scalar_one_or_none()
            if next_version is None:
                continue
            self.db.add(
                AnalysisDispatchOutbox(
                    analysis_run_item_id=item.id,
                    dispatch_version=next_version,
                    task_schema_version=CURRENT_ANALYSIS_TASK_SCHEMA_VERSION,
                )
            )
            recovered += 1
        if recovered:
            self.db.commit()
        return recovered

    def recover_expired_leases(self, *, limit: int = 200) -> int:
        """Requeue or permanently fail work whose worker lease expired."""
        now = self._now()
        items = list(
            self.db.scalars(
                select(AnalysisRunItem)
                .where(
                    AnalysisRunItem.status == "running",
                    AnalysisRunItem.lease_expires_at.is_not(None),
                    AnalysisRunItem.lease_expires_at < now,
                )
                .limit(limit)
            )
        )
        affected_runs: set[str] = set()
        recovered = 0
        for item in items:
            old_owner = item.lease_owner
            old_expiry = item.lease_expires_at
            terminal = item.attempt_count >= self.settings.analysis_item_max_attempts
            values = {
                "lease_owner": None,
                "lease_expires_at": None,
                "heartbeat_at": None,
                "completed_at": now if terminal else None,
                "status": "failed" if terminal else "queued",
                "error_message": (
                    "Worker lease expired after the maximum number of attempts."
                    if terminal
                    else "Worker lease expired; the item was queued for recovery."
                ),
                "job_id": None,
            }
            if not terminal:
                values["dispatch_version"] = AnalysisRunItem.dispatch_version + 1
            result = self.db.execute(
                update(AnalysisRunItem)
                .where(
                    AnalysisRunItem.id == item.id,
                    AnalysisRunItem.status == "running",
                    AnalysisRunItem.lease_owner == old_owner,
                    AnalysisRunItem.lease_expires_at == old_expiry,
                )
                .values(**values)
                .returning(AnalysisRunItem.dispatch_version)
            )
            next_version = result.scalar_one_or_none()
            if next_version is None:
                continue
            self.db.execute(
                update(AnalysisAttempt)
                .where(
                    AnalysisAttempt.analysis_run_item_id == item.id,
                    AnalysisAttempt.attempt_number == item.attempt_count,
                    AnalysisAttempt.status == "running",
                )
                .values(
                    status="timed_out",
                    error_class="WorkerLeaseExpired",
                    error_message=values["error_message"],
                    completed_at=now,
                )
            )
            if not terminal:
                self.db.add(
                    AnalysisDispatchOutbox(
                        analysis_run_item_id=item.id,
                        dispatch_version=next_version,
                        task_schema_version=CURRENT_ANALYSIS_TASK_SCHEMA_VERSION,
                    )
                )
            affected_runs.add(item.analysis_run_id)
            recovered += 1
        if recovered:
            self._reconcile_runs(affected_runs, now)
            self.db.commit()
        return recovered

    def heartbeat(self, item_id: str, worker_id: str) -> bool:
        renewed = renew_analysis_lease(
            self.db, item_id, worker_id, self.settings.analysis_lease_seconds
        )
        self.db.commit()
        return renewed

    def reject_incompatible_dispatch(
        self, item_id: str, dispatch_version: int, *, received_schema_version: int
    ) -> bool:
        message = (
            f"Task schema version {received_schema_version} is not supported by worker "
            f"{self.settings.worker_build_version}; expected {CURRENT_ANALYSIS_TASK_SCHEMA_VERSION}."
        )
        now = self._now()
        run_id = self.db.scalar(
            select(AnalysisRunItem.analysis_run_id).where(AnalysisRunItem.id == item_id)
        )
        result = self.db.execute(
            update(AnalysisRunItem)
            .where(
                AnalysisRunItem.id == item_id,
                AnalysisRunItem.status == "queued",
                AnalysisRunItem.dispatch_version == dispatch_version,
            )
            .values(status="failed", error_message=message, completed_at=now, job_id=None)
        )
        if result.rowcount and run_id:
            self._reconcile_runs({run_id}, now)
        self.db.commit()
        return bool(result.rowcount)

    def tick(self, queue: AnalysisQueue) -> dict[str, int]:
        """Run one bounded maintenance cycle."""
        expired = self.recover_expired_leases()
        stale = self.recover_stale_queued_jobs(queue)
        backfilled = self.ensure_pending_dispatches()
        dispatched = self.dispatch_pending(queue)
        return {
            "expired_leases": expired,
            "stale_jobs": stale,
            "backfilled_outbox": backfilled,
            "dispatched": dispatched,
        }

    def _reconcile_runs(self, run_ids: set[str], now: datetime) -> None:
        for run_id in run_ids:
            run = self.db.get(AnalysisRun, run_id)
            if run is None:
                continue
            statuses = list(
                self.db.scalars(
                    select(AnalysisRunItem.status).where(AnalysisRunItem.analysis_run_id == run_id)
                )
            )
            apply_run_status(run, statuses, now)
