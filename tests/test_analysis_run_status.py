"""The single run-summary rule shared by the worker, maintenance, and read paths."""

from datetime import datetime, timezone

import pytest

from app.domain.analysis import apply_run_status, derive_run_status
from app.domain.models import AnalysisRun


NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("statuses", "expected_status", "settled"),
    [
        (["running", "queued"], "running", False),
        (["retrying", "completed"], "running", False),
        (["queued", "completed"], "queued", False),
        (["completed", "completed"], "completed", True),
        (["failed", "completed"], "failed", True),
    ],
)
def test_active_items_outrank_settled_ones(statuses, expected_status, settled):
    status, _, completed_at = derive_run_status(statuses, NOW)

    assert status == expected_status
    assert (completed_at is not None) is settled


def test_failed_summary_reports_how_many_items_need_a_retry():
    status, error_message, completed_at = derive_run_status(["failed", "failed", "completed"], NOW)

    assert status == "failed"
    assert error_message == "2 analysis item(s) failed; retry failed items to continue."
    assert completed_at == NOW


def test_a_run_returning_to_work_drops_its_previous_failure_message():
    run = AnalysisRun(
        review_package_id="review",
        status="failed",
        error_message="1 analysis item(s) failed; retry failed items to continue.",
        completed_at=NOW,
    )

    assert apply_run_status(run, ["queued", "completed"], NOW) is True
    assert run.status == "queued"
    assert run.error_message is None
    assert run.completed_at is None


def test_an_unchanged_summary_reports_no_change_so_frequent_reads_avoid_a_write():
    run = AnalysisRun(review_package_id="review", status="completed", completed_at=NOW)

    # A later call supplies a different clock reading; only the presence of a
    # completion time matters, otherwise every progress poll would commit.
    later = datetime(2026, 8, 26, 12, 5, tzinfo=timezone.utc)

    assert apply_run_status(run, ["completed"], later) is False
    assert run.completed_at == NOW
