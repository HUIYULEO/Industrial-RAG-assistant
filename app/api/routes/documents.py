"""Document registration, ingestion, visual evidence, and indexing endpoints."""

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.api.auth import require_admin_user, require_authenticated_user
from app.api.dependencies import (
    CurrentUser,
    DbSession,
    DocumentIndexSubmissionDependency,
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
from app.core.logging_config import get_logger
from app.services.review_service import ReviewService

router = APIRouter(tags=["documents"], dependencies=[Depends(require_authenticated_user)])
logger = get_logger(__name__)


@router.post(
    "/documents",
    response_model=DocumentVersionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_user)],
)
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


@router.post(
    "/documents/{document_version_id}/archive",
    response_model=DocumentVersionResponse,
    dependencies=[Depends(require_admin_user)],
)
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


@router.post(
    "/documents/{document_version_id}/upload",
    response_model=DocumentVersionResponse,
    dependencies=[Depends(require_admin_user)],
)
async def upload_document(
    document_version_id: str,
    ingestion: DocumentIngestionDependency,
    file: UploadFile = File(...),
    pdf_password: str | None = Form(default=None),
):
    settings = get_settings()
    max_upload_bytes = settings.max_upload_size_mb * 1024 * 1024
    content = await file.read(max_upload_bytes + 1)
    if len(content) > max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_upload_size_mb} MB limit")
    try:
        item = await run_in_threadpool(
            ingestion.upload_and_parse,
            document_version_id,
            file.filename or "",
            content,
            pdf_password=pdf_password,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return document_response(item)


@router.post(
    "/documents/{document_version_id}/reparse",
    response_model=DocumentVersionResponse,
    dependencies=[Depends(require_admin_user)],
)
def reparse_document(
    document_version_id: str,
    ingestion: DocumentIngestionDependency,
    pdf_password: str | None = Form(default=None),
):
    try:
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
            element_type=chunk.element_type,
            source_metadata=chunk.source_metadata,
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
                element_type=chunk.element_type,
                source_metadata=chunk.source_metadata,
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


@router.post(
    "/documents/{document_version_id}/figures/analyse",
    response_model=list[DocumentFigureResponse],
    dependencies=[Depends(require_admin_user)],
)
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
        logger.exception(
            "Visual analysis failed for document %s", document_version_id
        )
        raise HTTPException(
            status_code=502,
            detail="Visual analysis is temporarily unavailable.",
        ) from exc
    return [figure_response(item) for item in figures]


@router.post(
    "/documents/{document_version_id}/index",
    response_model=DocumentVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_admin_user)],
)
def index_document(document_version_id: str, indexing: DocumentIndexSubmissionDependency):
    try:
        item = indexing.queue_document_version(document_version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        status_code = 409 if "already queued or running" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Document indexing submission failed for document %s", document_version_id
        )
        raise HTTPException(
            status_code=503,
            detail="Document indexing is temporarily unavailable.",
        ) from exc
    return document_response(item)
