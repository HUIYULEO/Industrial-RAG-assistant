"""Transactional Outbox delivery and stale document-index recovery."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.models import DocumentIndexDispatchOutbox, DocumentVersion
from app.services.indexing_queue import (
    DocumentIndexDispatch,
    DocumentIndexQueue,
    document_index_job_id,
)


class IndexingReliabilityService:
    """Publish durable indexing requests and reconcile jobs that cannot finish."""

    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def ensure_pending_dispatches(self) -> int:
        queued = list(
            self.db.scalars(
                select(DocumentVersion).where(DocumentVersion.ingestion_status == "index_queued")
            )
        )
        existing = {
            (document_version_id, dispatch_version)
            for document_version_id, dispatch_version in self.db.execute(
                select(
                    DocumentIndexDispatchOutbox.document_version_id,
                    DocumentIndexDispatchOutbox.dispatch_version,
                ).where(DocumentIndexDispatchOutbox.status.in_(["pending", "dispatched"]))
            )
        }
        created = 0
        for version in queued:
            key = (version.id, version.index_dispatch_version)
            if key in existing:
                continue
            self.db.add(
                DocumentIndexDispatchOutbox(
                    document_version_id=version.id,
                    dispatch_version=version.index_dispatch_version,
                )
            )
            created += 1
        if created:
            self.db.commit()
        return created

    def dispatch_pending(self, queue: DocumentIndexQueue, *, limit: int = 100) -> int:
        rows = list(
            self.db.scalars(
                select(DocumentIndexDispatchOutbox)
                .where(
                    DocumentIndexDispatchOutbox.status == "pending",
                    DocumentIndexDispatchOutbox.available_at <= self._now(),
                )
                .order_by(DocumentIndexDispatchOutbox.created_at, DocumentIndexDispatchOutbox.id)
                .limit(limit)
            )
        )
        dispatched = 0
        for outbox in rows:
            version = self.db.get(DocumentVersion, outbox.document_version_id)
            if (
                version is None
                or version.ingestion_status != "index_queued"
                or version.index_dispatch_version != outbox.dispatch_version
            ):
                outbox.status = "obsolete"
                self.db.commit()
                continue
            dispatch = DocumentIndexDispatch(
                document_version_id=version.id,
                dispatch_version=outbox.dispatch_version,
                job_id=document_index_job_id(version.id, outbox.dispatch_version),
            )
            try:
                job_id = queue.enqueue(dispatch)
            except Exception as exc:
                outbox.publish_attempts += 1
                outbox.error_message = str(exc)
                self.db.commit()
                raise
            now = self._now()
            outbox.status = "dispatched"
            outbox.job_id = job_id
            outbox.publish_attempts += 1
            outbox.error_message = None
            outbox.dispatched_at = now
            version.index_job_id = job_id
            version.ingestion_error = None
            self.db.commit()
            dispatched += 1
        return dispatched

    def recover_stale_queued_jobs(self, queue: DocumentIndexQueue) -> int:
        queued = list(
            self.db.scalars(
                select(DocumentVersion).where(
                    DocumentVersion.ingestion_status == "index_queued",
                    DocumentVersion.index_job_id.is_not(None),
                )
            )
        )
        if not queued:
            return 0
        job_ids = {version.id: version.index_job_id for version in queued if version.index_job_id}
        stale_ids = queue.stale_document_ids(
            job_ids,
            queued_before=self._now()
            - timedelta(seconds=self.settings.document_index_queued_timeout_seconds),
        )
        recovered = 0
        for version in queued:
            if version.id in stale_ids and self._recover_or_fail(
                version,
                expected_status="index_queued",
                reason="The queued indexing job became stale and was recovered.",
            ):
                recovered += 1
        if recovered:
            self.db.commit()
        return recovered

    def recover_stuck_indexing(self) -> int:
        cutoff = self._now() - timedelta(
            seconds=self.settings.document_index_stale_after_seconds
        )
        stuck = list(
            self.db.scalars(
                select(DocumentVersion).where(
                    DocumentVersion.ingestion_status == "indexing",
                    or_(
                        DocumentVersion.index_started_at.is_(None),
                        DocumentVersion.index_started_at < cutoff,
                    ),
                )
            )
        )
        recovered = 0
        for version in stuck:
            if self._recover_or_fail(
                version,
                expected_status="indexing",
                reason="The indexing worker exceeded its execution timeout and was recovered.",
            ):
                recovered += 1
        if recovered:
            self.db.commit()
        return recovered

    def _recover_or_fail(
        self, version: DocumentVersion, *, expected_status: str, reason: str
    ) -> bool:
        current_dispatch_version = version.index_dispatch_version
        self.db.execute(
            update(DocumentIndexDispatchOutbox)
            .where(
                DocumentIndexDispatchOutbox.document_version_id == version.id,
                DocumentIndexDispatchOutbox.dispatch_version == current_dispatch_version,
                DocumentIndexDispatchOutbox.status.in_(["pending", "dispatched"]),
            )
            .values(status="stale", error_message=reason)
        )
        if current_dispatch_version >= self.settings.document_index_max_attempts:
            result = self.db.execute(
                update(DocumentVersion)
                .where(
                    DocumentVersion.id == version.id,
                    DocumentVersion.ingestion_status == expected_status,
                    DocumentVersion.index_dispatch_version == current_dispatch_version,
                )
                .values(
                    ingestion_status="index_failed",
                    ingestion_error=(
                        f"{reason} Maximum indexing attempts "
                        f"({self.settings.document_index_max_attempts}) reached."
                    ),
                    index_job_id=None,
                    index_started_at=None,
                )
            )
            return bool(result.rowcount)

        next_dispatch_version = current_dispatch_version + 1
        result = self.db.execute(
            update(DocumentVersion)
            .where(
                DocumentVersion.id == version.id,
                DocumentVersion.ingestion_status == expected_status,
                DocumentVersion.index_dispatch_version == current_dispatch_version,
            )
            .values(
                ingestion_status="index_queued",
                ingestion_error=reason,
                index_dispatch_version=next_dispatch_version,
                index_job_id=None,
                index_started_at=None,
            )
        )
        if not result.rowcount:
            return False
        self.db.add(
            DocumentIndexDispatchOutbox(
                document_version_id=version.id,
                dispatch_version=next_dispatch_version,
            )
        )
        return True

    def tick(self, queue: DocumentIndexQueue) -> dict[str, int]:
        stuck = self.recover_stuck_indexing()
        stale = self.recover_stale_queued_jobs(queue)
        backfilled = self.ensure_pending_dispatches()
        dispatched = self.dispatch_pending(queue)
        return {
            "stuck_indexing": stuck,
            "stale_jobs": stale,
            "backfilled_outbox": backfilled,
            "dispatched": dispatched,
        }
