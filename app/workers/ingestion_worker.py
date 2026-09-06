"""RQ worker entry point for staged document parsing."""

from __future__ import annotations

from app.bootstrap.service_factory import build_document_ingestion_service
from app.repositories.database import get_session_factory


def execute_document_ingestion(document_version_id: str) -> None:
    """Parse one staged source using a worker-owned database Session."""
    db = get_session_factory()()
    try:
        build_document_ingestion_service(db).parse_staged_document(
            document_version_id
        )
    finally:
        db.close()
