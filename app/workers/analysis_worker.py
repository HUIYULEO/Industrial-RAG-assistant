"""Worker entry point. One RQ job evaluates one frozen URS item."""

from __future__ import annotations

import os
import socket
import threading

from app.bootstrap.service_factory import build_coverage_analysis_service
from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.domain.analysis import CURRENT_ANALYSIS_TASK_SCHEMA_VERSION
from app.repositories.database import get_session_factory
from app.services.analysis_reliability_service import AnalysisReliabilityService


logger = get_logger(__name__)


def _heartbeat_loop(
    stop: threading.Event,
    *,
    analysis_run_item_id: str,
    worker_id: str,
    interval_seconds: int,
) -> None:
    """Renew the database lease from an independent session while work runs."""
    while not stop.wait(interval_seconds):
        heartbeat_db = get_session_factory()()
        try:
            if not AnalysisReliabilityService(heartbeat_db, get_settings()).heartbeat(
                analysis_run_item_id, worker_id
            ):
                return
        except Exception:
            heartbeat_db.rollback()
            logger.exception(
                "Unable to renew analysis item lease",
                extra={"analysis_item_id": analysis_run_item_id, "worker_id": worker_id},
            )
        finally:
            heartbeat_db.close()


def execute_analysis_item(
    analysis_run_item_id: str,
    dispatch_version: int = 0,
    task_schema_version: int = CURRENT_ANALYSIS_TASK_SCHEMA_VERSION,
) -> None:
    """Execute and persist one independently retryable analysis item."""
    db = get_session_factory()()
    try:
        settings = get_settings()
        if task_schema_version != CURRENT_ANALYSIS_TASK_SCHEMA_VERSION:
            AnalysisReliabilityService(db, settings).reject_incompatible_dispatch(
                analysis_run_item_id,
                dispatch_version,
                received_schema_version=task_schema_version,
            )
            return
        worker_id = f"{socket.gethostname()}:{os.getpid()}:{settings.worker_build_version}"
        try:
            coverage_service = build_coverage_analysis_service(db)
        except Exception as exc:
            try:
                db.rollback()
                AnalysisReliabilityService(db, settings).fail_worker_initialization(
                    analysis_run_item_id, dispatch_version, exc
                )
            except Exception:
                logger.exception(
                    "Unable to persist analysis worker initialization failure",
                    extra={
                        "analysis_item_id": analysis_run_item_id,
                        "dispatch_version": dispatch_version,
                    },
                )
            logger.exception(
                "Analysis worker initialization failed",
                extra={
                    "analysis_item_id": analysis_run_item_id,
                    "dispatch_version": dispatch_version,
                },
            )
            raise
        heartbeat_stop = threading.Event()
        heartbeat_thread = threading.Thread(
            target=_heartbeat_loop,
            kwargs={
                "stop": heartbeat_stop,
                "analysis_run_item_id": analysis_run_item_id,
                "worker_id": worker_id,
                "interval_seconds": settings.analysis_heartbeat_interval_seconds,
            },
            name=f"analysis-heartbeat-{analysis_run_item_id}",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            coverage_service.execute_item(
                analysis_run_item_id,
                max_attempts=settings.analysis_item_max_attempts,
                retry_delays_seconds=settings.analysis_retry_delays_seconds,
                worker_id=worker_id,
                lease_seconds=settings.analysis_lease_seconds,
                expected_dispatch_version=dispatch_version,
            )
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2)
    finally:
        db.close()
