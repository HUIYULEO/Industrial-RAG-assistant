"""Document registration, ingestion, visual evidence, and indexing endpoints."""

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.auth import require_authenticated_user
from app.api.dependencies import (
    CurrentUser,
    DbSession,
    DocumentIndexingDependency,
    DocumentIngestionDependency,
    VisualEvidenceDependency,
    VisualInterpreterDependency,
)
from app.api.presenters import document_response, figure_response
from app.api.schemas import (
    DocumentArchiveRequest,
    DocumentChunkResponse,
    DocumentChunkContextResponse,
    DocumentCreate,
    DocumentFigureResponse,
    DocumentVersionResponse,
)
from app.core.config import get_settings
from app.domain.models import ReviewPackageDocument
from app.services.review_service import ReviewService

router = APIRouter(tags=["documents"], dependencies=[Depends(require_authenticated_user)])


@router.post("/documents", response_model=DocumentVersionResponse, status_code=status.HTTP_201_CREATED)
def create_document(payload: DocumentCreate, db: DbSession):
    service = ReviewService(db)
    try:
        item = service.create_document(
            title=payload.title,
            document_type=payload.document_type,
            system=payload.system,
            vendor=payload.vendor,
            version=payload.version,
            status=payload.status,
            file_name=payload.file_name,
            file_hash=payload.file_hash,
            source_url=str(payload.source_url) if payload.source_url else None,
            supersedes_version_id=payload.supersedes_version_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Document version already exists") from exc
    return document_response(item)


@router.get("/documents", response_model=list[DocumentVersionResponse])
def list_documents(db: DbSession):
    return [document_response(item) for item in ReviewService(db).list_document_versions()]


@router.post("/documents/{document_version_id}/archive", response_model=DocumentVersionResponse)
def archive_document(document_version_id: str, payload: DocumentArchiveRequest, db: DbSession, user: CurrentUser):
    try:
        item = ReviewService(db).archive_document_version(
            document_version_id=document_version_id,
            reason=payload.reason,
            archived_by_user_id=user.id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return document_response(item)


@router.post("/documents/{document_version_id}/upload", response_model=DocumentVersionResponse)
async def upload_document(
    document_version_id: str,
    db: DbSession,
    ingestion: DocumentIngestionDependency,
    file: UploadFile = File(...),
    pdf_password: str | None = Form(default=None),
):
    settings = get_settings()
    content = await file.read()
    if len(content) > settings.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_upload_size_mb} MB limit")
    try:
        item = ingestion.upload_and_parse(
            document_version_id, file.filename or "", content, pdf_password=pdf_password
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return document_response(item)


@router.post("/documents/{document_version_id}/reparse", response_model=DocumentVersionResponse)
def reparse_document(
    document_version_id: str,
    db: DbSession,
    ingestion: DocumentIngestionDependency,
    pdf_password: str | None = Form(default=None),
):
    try:
        if db.scalar(select(ReviewPackageDocument.id).where(ReviewPackageDocument.document_version_id == document_version_id)):
            raise ValueError("A document version in a frozen Review Package cannot be reparsed")
        item = ingestion.reparse_stored_document(
            document_version_id, pdf_password=pdf_password
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return document_response(item)


@router.get("/documents/{document_version_id}/chunks", response_model=list[DocumentChunkResponse])
def list_document_chunks(document_version_id: str, ingestion: DocumentIngestionDependency):
    try:
        chunks = ingestion.list_chunks(document_version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [
        DocumentChunkResponse(
            id=chunk.id,
            chunk_index=chunk.chunk_index,
            page=chunk.page,
            section=chunk.section,
            content=chunk.content,
        )
        for chunk in chunks
    ]


@router.get(
    "/documents/{document_version_id}/chunks/{chunk_id}/context",
    response_model=DocumentChunkContextResponse,
)
def read_document_chunk_context(
    document_version_id: str, chunk_id: str, ingestion: DocumentIngestionDependency
):
    """Read a citation in its source order without exposing another version's content."""
    try:
        chunks = ingestion.get_chunk_context(document_version_id, chunk_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return DocumentChunkContextResponse(
        document_version_id=document_version_id,
        requested_chunk_id=chunk_id,
        chunks=[
            DocumentChunkResponse(
                id=chunk.id,
                chunk_index=chunk.chunk_index,
                page=chunk.page,
                section=chunk.section,
                content=chunk.content,
            )
            for chunk in chunks
        ],
    )


@router.get("/documents/{document_version_id}/source")
def read_original_pdf_source(document_version_id: str, ingestion: DocumentIngestionDependency):
    """Serve an uploaded PDF only after the normal authenticated API check."""
    try:
        source_path = ingestion.get_pdf_source_path(document_version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(source_path, media_type="application/pdf", headers={"Content-Disposition": "inline"})


@router.get("/documents/{document_version_id}/figures", response_model=list[DocumentFigureResponse])
def list_document_figures(document_version_id: str, visual_evidence: VisualEvidenceDependency):
    try:
        figures = visual_evidence.list_figures(document_version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [figure_response(item) for item in figures]


@router.get("/documents/{document_version_id}/figures/{figure_id}/asset")
def get_document_figure_asset(document_version_id: str, figure_id: str, visual_evidence: VisualEvidenceDependency):
    try:
        figure = visual_evidence.get_figure(document_version_id, figure_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    image_path = Path(figure.image_path)
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="The rendered visual evidence asset is unavailable")
    return FileResponse(image_path, media_type="image/png", filename=f"visual-evidence-page-{figure.page}.png")


@router.post("/documents/{document_version_id}/figures/analyse", response_model=list[DocumentFigureResponse])
def analyse_document_figures(
    document_version_id: str,
    visual_evidence: VisualEvidenceDependency,
    visual_interpreter: VisualInterpreterDependency,
):
    settings = get_settings()
    if not settings.enable_visual_analysis:
        raise HTTPException(
            status_code=403,
            detail="Visual-model analysis is disabled. Diagram pages remain available as source evidence.",
        )
    try:
        figures = visual_evidence.analyse_figures(document_version_id, visual_interpreter)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Visual analysis failed: {exc}") from exc
    return [figure_response(item) for item in figures]


@router.post("/documents/{document_version_id}/index", response_model=DocumentVersionResponse)
def index_document(document_version_id: str, indexing: DocumentIndexingDependency):
    try:
        item = indexing.index_document_version(document_version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Document indexing failed: {exc}") from exc
    return document_response(item)
