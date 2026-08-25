"""Vendor-document evidence contracts shared by retrieval and review services."""

from dataclasses import dataclass, field

from app.domain.enums import DESIGN_DOCUMENT_TYPES


@dataclass(frozen=True)
class RetrievalFilters:
    """Immutable scope; a review must never silently include a different version."""

    document_version_ids: list[str]
    system: str | None = None
    document_types: list[str] = field(default_factory=lambda: sorted(DESIGN_DOCUMENT_TYPES))


@dataclass(frozen=True)
class EvidenceChunk:
    """One citable source passage returned from the knowledge index."""

    chunk_id: str
    document_version_id: str
    document_title: str
    document_type: str
    version: str
    page: int | None
    section: str | None
    content: str
    dense_score: float | None = None
    keyword_score: float | None = None
    fused_score: float | None = None
