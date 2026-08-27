"""Versioned contracts and shared run-state rules for analysis producers and workers."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # keeps this contract module free of a runtime ORM dependency
    from app.domain.models import AnalysisRun

CURRENT_ANALYSIS_TASK_SCHEMA_VERSION = 1

# Outbox delivery is infrastructure work, separate from LLM execution attempts.
# A short Redis outage is retried with increasing delays. After the final
# attempt the item becomes a visible, manually retryable failure instead of
# remaining silently queued forever.
ANALYSIS_OUTBOX_MAX_PUBLISH_ATTEMPTS = 8
ANALYSIS_OUTBOX_RETRY_DELAYS_SECONDS = (2, 5, 15, 30, 60, 300, 900)

# An item in one of these states still has work outstanding, so its run cannot
# be summarised as settled.
ACTIVE_ITEM_STATUSES = frozenset({"queued", "running", "retrying"})


def derive_run_status(statuses: list[str], now: datetime) -> tuple[str, str | None, datetime | None]:
    """Derive one run summary from the durable states of its items.

    The worker, the maintenance loop, and the HTTP read path all write
    ``AnalysisRun.status``.  They previously each carried their own copy of this
    rule with slightly different semantics, so the same item states could
    produce three different summaries depending on which writer ran last.
    """
    if any(status in {"running", "retrying"} for status in statuses):
        return "running", None, None
    if any(status == "queued" for status in statuses):
        return "queued", None, None
    if any(status == "failed" for status in statuses):
        failed = sum(status == "failed" for status in statuses)
        return "failed", f"{failed} analysis item(s) failed; retry failed items to continue.", now
    return "completed", None, now


def apply_run_status(run: AnalysisRun, statuses: list[str], now: datetime) -> bool:
    """Write the derived summary onto ``run`` and report whether it changed.

    Callers commit their own transaction.  The return value lets a frequent
    reader skip a write when the stored summary is already correct: ``now``
    differs on every call, so ``completed_at`` is compared only for presence.
    """
    status, error_message, completed_at = derive_run_status(statuses, now)
    if (
        run.status == status
        and run.error_message == error_message
        and (run.completed_at is None) == (completed_at is None)
    ):
        return False
    run.status = status
    run.error_message = error_message
    run.completed_at = completed_at
    return True
