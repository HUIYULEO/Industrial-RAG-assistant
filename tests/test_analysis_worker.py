from types import SimpleNamespace

import pytest

from app.workers import analysis_worker


def test_heartbeat_loop_renews_the_lease_with_an_independent_session(monkeypatch):
    calls: dict[str, object] = {}

    class FakeStop:
        def wait(self, interval_seconds):
            calls["interval_seconds"] = interval_seconds
            return False

    class FakeSession:
        def close(self):
            calls["heartbeat_session_closed"] = True

    class FakeReliability:
        def __init__(self, db, settings):
            calls["heartbeat_db"] = db

        def heartbeat(self, item_id, worker_id):
            calls["heartbeat"] = (item_id, worker_id)
            return False

    session = FakeSession()
    monkeypatch.setattr(analysis_worker, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(analysis_worker, "get_settings", lambda: object())
    monkeypatch.setattr(analysis_worker, "AnalysisReliabilityService", FakeReliability)

    analysis_worker._heartbeat_loop(
        FakeStop(),
        analysis_run_item_id="analysis-item-1",
        worker_id="worker-1",
        interval_seconds=30,
    )

    assert calls == {
        "interval_seconds": 30,
        "heartbeat_db": session,
        "heartbeat": ("analysis-item-1", "worker-1"),
        "heartbeat_session_closed": True,
    }


def test_worker_assembles_and_executes_one_analysis_item(monkeypatch):
    calls: dict[str, object] = {}

    class FakeSession:
        def close(self):
            calls["closed"] = True

    class FakeCoverageService:
        def execute_item(
            self,
            item_id,
            *,
            max_attempts,
            retry_delays_seconds,
            worker_id,
            lease_seconds,
            expected_dispatch_version,
        ):
            calls["item_id"] = item_id
            calls["max_attempts"] = max_attempts
            calls["retry_delays_seconds"] = retry_delays_seconds
            calls["lease_seconds"] = lease_seconds
            calls["dispatch_version"] = expected_dispatch_version

    session = FakeSession()
    monkeypatch.setattr(analysis_worker, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        analysis_worker,
        "get_settings",
        lambda: SimpleNamespace(
            analysis_item_max_attempts=3,
            analysis_retry_delays_seconds=[1, 5],
            analysis_lease_seconds=360,
            analysis_heartbeat_interval_seconds=30,
            worker_build_version="test",
        ),
    )
    monkeypatch.setattr(analysis_worker, "build_coverage_analysis_service", lambda db: FakeCoverageService())

    analysis_worker.execute_analysis_item("analysis-item-1")

    assert calls == {
        "item_id": "analysis-item-1",
        "max_attempts": 3,
        "retry_delays_seconds": [1, 5],
        "lease_seconds": 360,
        "dispatch_version": 0,
        "closed": True,
    }
    assert not hasattr(analysis_worker, "initialise_database")


def test_worker_factory_failure_marks_matching_dispatch_failed_and_reraises(monkeypatch):
    calls: dict[str, object] = {}

    class FakeSession:
        def rollback(self):
            calls["rolled_back"] = True

        def close(self):
            calls["closed"] = True

    class FakeReliability:
        def __init__(self, db, settings):
            calls["reliability_db"] = db

        def fail_worker_initialization(self, item_id, dispatch_version, error):
            calls["failed"] = (item_id, dispatch_version, str(error))
            return True

    session = FakeSession()
    monkeypatch.setattr(analysis_worker, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        analysis_worker,
        "get_settings",
        lambda: SimpleNamespace(worker_build_version="test"),
    )
    monkeypatch.setattr(analysis_worker, "AnalysisReliabilityService", FakeReliability)

    def fail_factory(_db):
        raise RuntimeError("provider configuration is invalid")

    monkeypatch.setattr(analysis_worker, "build_coverage_analysis_service", fail_factory)

    with pytest.raises(RuntimeError, match="provider configuration is invalid"):
        analysis_worker.execute_analysis_item("analysis-item-1", dispatch_version=2)

    assert calls == {
        "rolled_back": True,
        "reliability_db": session,
        "failed": ("analysis-item-1", 2, "provider configuration is invalid"),
        "closed": True,
    }


def test_worker_factory_failure_keeps_the_original_error_if_persistence_also_fails(monkeypatch):
    class FakeSession:
        def rollback(self):
            pass

        def close(self):
            pass

    class FailingReliability:
        def __init__(self, _db, _settings):
            pass

        def fail_worker_initialization(self, _item_id, _dispatch_version, _error):
            raise OSError("database unavailable")

    monkeypatch.setattr(analysis_worker, "get_session_factory", lambda: lambda: FakeSession())
    monkeypatch.setattr(
        analysis_worker,
        "get_settings",
        lambda: SimpleNamespace(worker_build_version="test"),
    )
    monkeypatch.setattr(analysis_worker, "AnalysisReliabilityService", FailingReliability)

    def fail_factory(_db):
        raise RuntimeError("provider configuration is invalid")

    monkeypatch.setattr(analysis_worker, "build_coverage_analysis_service", fail_factory)

    with pytest.raises(RuntimeError, match="provider configuration is invalid"):
        analysis_worker.execute_analysis_item("analysis-item-1")
