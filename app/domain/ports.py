"""Interfaces the application layer needs from evidence-index infrastructure."""

from collections.abc import Sequence
from typing import Protocol

from app.domain.evidence import EvidenceChunk, RetrievalFilters


class DocumentChunkIndex(Protocol):
    """Replace and search citable chunks for one frozen document version."""

    def replace_document_version(self, records: Sequence[dict]) -> None: ...

    def hybrid_search(
        self,
        *,
        query_text: str,
        query_vector: list[float],
        filters: RetrievalFilters,
        limit: int,
    ) -> list[EvidenceChunk]: ...


class EvidenceCitation(Protocol):
    """Citation data needed by an exported traceability matrix."""

    document_title: str
    version: str
    section: str | None
    page: int | None
    excerpt: str


class AuditPoint(Protocol):
    """One reviewable point nested in a traceability-matrix row."""

    point_id: str
    review_point: str
    design_status: str
    rationale: str
    evidence: Sequence[EvidenceCitation]


class TraceabilityMatrixRow(Protocol):
    """Presentation-neutral matrix data accepted by spreadsheet export."""

    requirement_code: str
    requirement_text: str
    rationale_impact: str | None
    is_critical: bool
    priority: str | None
    analysis_status: str
    technical_error: str | None
    design_status: str | None
    status_definition: str | None
    rationale: str | None
    gap: str | None
    suggested_reviewer_action: str | None
    evidence: Sequence[EvidenceCitation]
    audit_points: Sequence[AuditPoint]
