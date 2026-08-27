"""Continuous Outbox dispatcher and orphan-task reconciler."""

from __future__ import annotations

import signal
from threading import Event

from app.core.config import get_settings
from app.core.logging_config import get_logger, setup_logging
from app.repositories.database import get_session_factory, initialise_database
from app.services.analysis_queue import (
    AnalysisQueuePermanentError,
    AnalysisQueueUnavailable,
    get_analysis_queue,
)
from app.services.analysis_reliability_service import AnalysisReliabilityService

setup_logging()
logger = get_logger(__name__)


def run() -> None:
    initialise_database()
    settings = get_settings()
    stopped = Event()

    def stop(*_):
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logger.info("Analysis maintenance loop started")
    while not stopped.is_set():
        db = get_session_factory()()
        try:
            result = AnalysisReliabilityService(db, settings).tick(get_analysis_queue())
            if any(result.values()):
                logger.info("Analysis maintenance cycle repaired work", extra={"maintenance": result})
        except AnalysisQueuePermanentError as exc:
            logger.error("Analysis queue dispatch blocked: %s", exc)
        except AnalysisQueueUnavailable as exc:
            logger.warning("Analysis queue maintenance deferred: %s", exc)
        except Exception:
            logger.exception("Analysis maintenance cycle failed")
        finally:
            db.close()
        stopped.wait(settings.analysis_maintenance_poll_seconds)
    logger.info("Analysis maintenance loop stopped")


if __name__ == "__main__":
    run()
