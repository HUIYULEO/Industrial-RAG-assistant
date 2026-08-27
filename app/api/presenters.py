"""Stable HTTP response mappings for persisted review-workspace records."""

from app.api.schemas import DocumentFigureResponse, DocumentVersionResponse
from app.domain.models import DocumentFigure, DocumentVersion


def document_response(item: DocumentVersion) -> DocumentVersionResponse:
    return DocumentVersionResponse(
        id=item.id,
        document_id=item.document_id,
        title=item.document.title,
        document_type=item.document.document_type,
        system=item.document.system,
        vendor=item.document.vendor,
        version=item.version,
        status=item.status,
        file_name=item.file_name,
        source_url=item.source_url,
        storage_path=item.storage_path,
        ingestion_status=item.ingestion_status,
        ingestion_error=item.ingestion_error,
        page_count=item.page_count,
        chunk_count=item.chunk_count,
        supersedes_version_id=item.supersedes_version_id,
        archived_at=item.archived_at,
        archived_by_user_id=item.archived_by_user_id,
        archived_reason=item.archived_reason,
        created_at=item.created_at,
    )


def figure_response(item: DocumentFigure) -> DocumentFigureResponse:
    return DocumentFigureResponse(
        id=item.id,
        page=item.page,
        section=item.section,
        image_available=bool(item.image_path),
        analysis_status=item.analysis_status,
        analysis_error=item.analysis_error,
        diagram_type=item.diagram_type,
        visible_labels=item.visible_labels or [],
        candidate_description=item.candidate_description,
        candidate_relationships=item.candidate_relationships or [],
        citation_chunk_id=item.citation_chunk_id,
    )
