"""RQ worker entry point for one document-version indexing job."""

from __future__ import annotations

from app.bootstrap.service_factory import build_document_indexing_service
from app.domain.models import DocumentVersion
from app.repositories.database import get_session_factory


def execute_document_index(document_version_id: str, dispatch_version: int = 0) -> None:
    """Embed parsed chunks and persist the resulting vector index."""
    db = get_session_factory()()
    try:
        try:
            build_document_indexing_service(db).index_document_version(
                document_version_id,
                expected_dispatch_version=dispatch_version,
            )
        except Exception as exc:
            # Configuration/provider construction can fail before the indexing
            # service claims the queued row. Persist that failure as well so a
            # refresh never leaves a dead RQ job looking permanently queued.
            db.rollback()
            version = db.get(DocumentVersion, document_version_id)
            if (
                version is not None
                and version.ingestion_status in {"index_queued", "indexing"}
                and version.index_dispatch_version == dispatch_version
            ):
                version.ingestion_status = "index_failed"
                version.ingestion_error = str(exc)
                version.index_job_id = None
                version.index_started_at = None
                db.commit()
            raise
    finally:
        db.close()
