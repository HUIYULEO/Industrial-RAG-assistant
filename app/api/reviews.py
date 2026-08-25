"""Document, requirement-baseline, and review-package endpoints."""

import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import (
    DocumentCreate,
    DocumentArchiveRequest,
    DocumentVersionResponse,
    RequirementBaselineCreate,
    RequirementBaselineImportResponse,
    RequirementBaselineResponse,
    RequirementImportResponse,
    RequirementResponse,
    AnalysisRunResponse,
    AnalysisRunItemResponse,
    AnalysisRunProgressResponse,
    DocumentChunkResponse,
    DocumentFigureResponse,
    FindingEvidenceResponse,
    FindingResponse,
    AuditPointResponse,
    MatrixRowResponse,
    ReviewChatCitation,
    ReviewChatRequest,
    ReviewChatResponse,
    ReviewPackageCreate,
    ReviewPackageResponse,
)
from app.domain.models import (
    AnalysisRun,
    DocumentFigure,
    DocumentVersion,
    Requirement,
    RequirementBaseline,
    ReviewFinding,
    ReviewPackage,
    ReviewPackageDocument,
)
from app.domain.enums import coverage_status_definition
from app.repositories.database import get_db, get_session_factory
from app.core.config import get_settings
from app.services.ingestion_service import DocumentIngestionService
from app.services.embedding_service import ConfiguredEmbeddingService
from app.services.indexing_service import DocumentIndexingService
from app.repositories.milvus_repository import MilvusChunkRepository
from app.services.retrieval_service import MilvusRetrievalService
from app.services.design_review_chat_service import ConfiguredGroundedAnswerGenerator, ConfiguredQueryNormalizer, DesignReviewChatService
from app.services.review_service import ReviewService
from app.services.visual_evidence_service import ConfiguredVisualInterpreter, VisualEvidenceService
from app.services.analysis_queue import AnalysisQueue, AnalysisQueueUnavailable, get_analysis_queue
from app.services.matrix_export_service import MatrixExportService
from app.services.model_provider import create_chat_model
from app.api.auth import require_authenticated_user
from app.services.auth_service import AuthenticatedUser

router = APIRouter(tags=["design-review"], dependencies=[Depends(require_authenticated_user)])
DbSession = Annotated[Session, Depends(get_db)]
AnalysisQueueDependency = Annotated[AnalysisQueue, Depends(get_analysis_queue)]
CurrentUser = Annotated[AuthenticatedUser, Depends(require_authenticated_user)]


def scoped_review_service(db: Session, user: AuthenticatedUser) -> ReviewService:
    return ReviewService(db, owner_user_id=user.id, organization_id=user.organization_id)


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


def baseline_response(item: RequirementBaseline) -> RequirementBaselineResponse:
    return RequirementBaselineResponse(
        id=item.id,
        name=item.name,
        system=item.system,
        description=item.description,
        created_at=item.created_at,
    )


def requirement_response(item: Requirement) -> RequirementResponse:
    return RequirementResponse(
        id=item.id,
        requirement_code=item.requirement_code,
        requirement_text=item.requirement_text,
        source_row=item.source_row,
        requirement_system=item.requirement_system,
        rationale_impact=item.rationale_impact,
        is_critical=item.is_critical,
        priority=item.priority,
        category=item.category,
        source_section=item.source_section,
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


def review_response(item: ReviewPackage) -> ReviewPackageResponse:
    return ReviewPackageResponse(
        id=item.id,
        owner_user_id=item.owner_user_id,
        organization_id=item.organization_id,
        name=item.name,
        system=item.system,
        requirement_baseline_id=item.requirement_baseline_id,
        design_document_version_ids=[link.document_version_id for link in item.document_links],
        requirement_count=len(item.requirement_snapshots),
        created_at=item.created_at,
    )


def analysis_run_response(item: AnalysisRun) -> AnalysisRunResponse:
    return AnalysisRunResponse(
        id=item.id,
        review_package_id=item.review_package_id,
        status=item.status,
        error_message=item.error_message,
        created_at=item.created_at,
        completed_at=item.completed_at,
    )


def analysis_progress_response(item: AnalysisRun) -> AnalysisRunProgressResponse:
    items = sorted(item.items, key=lambda value: value.requirement_snapshot.requirement_code)
    statuses = [value.status for value in items]
    return AnalysisRunProgressResponse(
        **analysis_run_response(item).model_dump(),
        total_items=len(items),
        queued_items=sum(value == "queued" for value in statuses),
        running_items=sum(value in {"running", "retrying"} for value in statuses),
        completed_items=sum(value == "completed" for value in statuses),
        failed_items=sum(value == "failed" for value in statuses),
        items=[
            AnalysisRunItemResponse(
                id=value.id,
                requirement_code=value.requirement_snapshot.requirement_code,
                status=value.status,
                attempt_count=value.attempt_count,
                error_message=value.error_message,
                started_at=value.started_at,
                completed_at=value.completed_at,
            )
            for value in items
        ],
    )


def enqueue_analysis_items(service: ReviewService, run: AnalysisRun, queue: AnalysisQueue) -> AnalysisRun:
    queued_items = [item for item in run.items if item.status == "queued" and not item.job_id]
    if not queued_items:
        return run
    job_ids = queue.enqueue_items(item.id for item in queued_items)
    return service.set_analysis_item_job_ids(run.id, job_ids)


def finding_response(item: ReviewFinding) -> FindingResponse:
    evidence = [
        FindingEvidenceResponse(
            chunk_id=evidence.chunk_id,
            document_version_id=evidence.document_version_id,
            document_title=evidence.document_title,
            version=evidence.version,
            page=evidence.page,
            section=evidence.section,
            excerpt=evidence.excerpt,
        )
        for evidence in item.evidence
    ]
    evidence_by_id = {value.chunk_id: value for value in evidence}
    audit_points = []
    for point in item.audit_points or []:
        audit_points.append(
            AuditPointResponse(
                point_id=point["point_id"],
                source_excerpt=point["source_excerpt"],
                review_point=point["review_point"],
                design_status=point["design_status"],
                status_definition=coverage_status_definition(point["design_status"]),
                rationale=point["rationale"],
                evidence=[
                    evidence_by_id[chunk_id]
                    for chunk_id in point.get("evidence_chunk_ids", [])
                    if chunk_id in evidence_by_id
                ],
            )
        )
    requirement = item.requirement_snapshot
    return FindingResponse(
        id=item.id,
        requirement_code=requirement.requirement_code,
        requirement_text=requirement.requirement_text,
        design_status=item.design_status,
        rationale=item.rationale,
        gap=item.gap,
        suggested_reviewer_action=item.suggested_reviewer_action,
        evidence=evidence,
        audit_points=audit_points,
    )


def matrix_row_response(item, findings_by_requirement_id: dict[str, ReviewFinding]) -> MatrixRowResponse:
    """Render an item even when the worker has not produced a finding."""
    requirement = item.requirement_snapshot
    finding = findings_by_requirement_id.get(requirement.id)
    if finding is None:
        return MatrixRowResponse(
            requirement_code=requirement.requirement_code,
            requirement_text=requirement.requirement_text,
            rationale_impact=requirement.rationale_impact,
            is_critical=requirement.is_critical,
            priority=requirement.priority,
            category=requirement.category,
            analysis_status=item.status,
            technical_error=item.error_message,
            design_status=None,
            status_definition=None,
            rationale=None,
            gap=None,
            suggested_reviewer_action=None,
        )
    response = finding_response(finding)
    return MatrixRowResponse(
        requirement_code=requirement.requirement_code,
        requirement_text=requirement.requirement_text,
        rationale_impact=requirement.rationale_impact,
        is_critical=requirement.is_critical,
        priority=requirement.priority,
        category=requirement.category,
        analysis_status=item.status,
        technical_error=item.error_message,
        design_status=response.design_status,
        status_definition=coverage_status_definition(response.design_status),
        rationale=response.rationale,
        gap=response.gap,
        suggested_reviewer_action=response.suggested_reviewer_action,
        evidence=response.evidence,
        audit_points=response.audit_points,
    )


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
def archive_document(
    document_version_id: str,
    payload: DocumentArchiveRequest,
    db: DbSession,
    user: CurrentUser,
):
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
    file: UploadFile = File(...),
    pdf_password: str | None = Form(default=None),
):
    """Store and parse a PDF, DOCX, or CSV source before separate vector indexing."""
    settings = get_settings()
    content = await file.read()
    if len(content) > settings.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_upload_size_mb} MB limit")
    try:
        item = DocumentIngestionService(db, settings.data_dir).upload_and_parse(
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
    pdf_password: str | None = Form(default=None),
):
    """Regenerate chunks from the stored source; call /index afterwards to rebuild vectors."""
    settings = get_settings()
    try:
        if db.scalar(
            select(ReviewPackageDocument.id).where(
                ReviewPackageDocument.document_version_id == document_version_id
            )
        ):
            raise ValueError("A document version in a frozen Review Package cannot be reparsed")
        item = DocumentIngestionService(db, settings.data_dir).reparse_stored_document(
            document_version_id, pdf_password=pdf_password
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return document_response(item)


@router.get("/documents/{document_version_id}/chunks", response_model=list[DocumentChunkResponse])
def list_document_chunks(document_version_id: str, db: DbSession):
    try:
        chunks = DocumentIngestionService(db, get_settings().data_dir).list_chunks(document_version_id)
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


@router.get("/documents/{document_version_id}/figures", response_model=list[DocumentFigureResponse])
def list_document_figures(document_version_id: str, db: DbSession):
    try:
        figures = VisualEvidenceService(db, get_settings().data_dir).list_figures(document_version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [figure_response(item) for item in figures]


@router.get("/documents/{document_version_id}/figures/{figure_id}/asset")
def get_document_figure_asset(document_version_id: str, figure_id: str, db: DbSession):
    try:
        figure = VisualEvidenceService(db, get_settings().data_dir).get_figure(document_version_id, figure_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    image_path = Path(figure.image_path)
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="The rendered visual evidence asset is unavailable")
    return FileResponse(image_path, media_type="image/png", filename=f"visual-evidence-page-{figure.page}.png")


@router.post(
    "/documents/{document_version_id}/figures/analyse",
    response_model=list[DocumentFigureResponse],
)
def analyse_document_figures(document_version_id: str, db: DbSession):
    """Create unverified, citable visual interpretations after reviewer request."""
    settings = get_settings()
    if not settings.enable_visual_analysis:
        raise HTTPException(
            status_code=403,
            detail="Visual-model analysis is disabled. Diagram pages remain available as source evidence.",
        )
    try:
        figures = VisualEvidenceService(db, settings.data_dir).analyse_figures(
            document_version_id,
            ConfiguredVisualInterpreter(create_chat_model(settings)),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Visual analysis failed: {exc}") from exc
    return [figure_response(item) for item in figures]


@router.post("/documents/{document_version_id}/index", response_model=DocumentVersionResponse)
def index_document(document_version_id: str, db: DbSession):
    """Create dense and BM25 indexes after an engineer has inspected parse output."""
    settings = get_settings()
    try:
        item = DocumentIndexingService(
            db,
            ConfiguredEmbeddingService(settings),
            MilvusChunkRepository(
                uri=settings.milvus_uri,
                collection_name=settings.milvus_collection,
                dimension=settings.embedding_dimensions,
            ),
            batch_token_budget=settings.embedding_batch_token_budget,
            tokens_per_minute=settings.embedding_tokens_per_minute,
            max_retries=settings.embedding_batch_max_retries,
            retry_base_delay_seconds=settings.embedding_retry_base_delay_seconds,
        ).index_document_version(document_version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Document indexing failed: {exc}") from exc
    return document_response(item)


@router.post("/requirement-baselines", response_model=RequirementBaselineResponse, status_code=status.HTTP_201_CREATED)
def create_requirement_baseline(payload: RequirementBaselineCreate, db: DbSession):
    try:
        item = ReviewService(db).create_baseline(**payload.model_dump())
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Requirement baseline name already exists") from exc
    return baseline_response(item)


@router.get("/requirement-baselines", response_model=list[RequirementBaselineResponse])
def list_requirement_baselines(db: DbSession):
    return [baseline_response(item) for item in ReviewService(db).list_baselines()]


@router.post(
    "/requirement-baselines/import",
    response_model=RequirementBaselineImportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_requirement_baseline(
    db: DbSession,
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    system: str | None = Form(default=None),
):
    """Create a baseline directly from a controlled URS/ES CSV table."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="For now, import a UTF-8 CSV export of the URS or ES table")
    try:
        baseline, requirements = ReviewService(db).create_baseline_from_csv(
            file_name=file.filename,
            content=await file.read(),
            name=name,
            system=system,
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A baseline with that document name already exists") from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RequirementBaselineImportResponse(
        baseline=baseline_response(baseline),
        imported_count=len(requirements),
        requirements=[requirement_response(item) for item in requirements],
    )


@router.post(
    "/requirement-baselines/{baseline_id}/requirements/import",
    response_model=RequirementImportResponse,
)
async def import_requirements(baseline_id: str, db: DbSession, file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="A CSV file is required")
    try:
        requirements = ReviewService(db).import_requirements_csv(baseline_id, await file.read())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RequirementImportResponse(
        imported_count=len(requirements), requirements=[requirement_response(item) for item in requirements]
    )


@router.get("/requirement-baselines/{baseline_id}/requirements", response_model=list[RequirementResponse])
def list_requirements(baseline_id: str, db: DbSession):
    try:
        requirements = ReviewService(db).list_requirements(baseline_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [requirement_response(item) for item in requirements]


@router.post("/review-packages", response_model=ReviewPackageResponse, status_code=status.HTTP_201_CREATED)
def create_review_package(payload: ReviewPackageCreate, db: DbSession, user: CurrentUser):
    service = scoped_review_service(db, user)
    try:
        item = service.create_review_package(**payload.model_dump())
        item = service.get_review_package(item.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Review package name already exists") from exc
    return review_response(item)


@router.get("/review-packages/{review_id}", response_model=ReviewPackageResponse)
def get_review_package(review_id: str, db: DbSession, user: CurrentUser):
    try:
        item = scoped_review_service(db, user).get_review_package(review_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return review_response(item)


@router.get("/review-packages", response_model=list[ReviewPackageResponse])
def list_review_packages(db: DbSession, user: CurrentUser):
    return [review_response(item) for item in scoped_review_service(db, user).list_review_packages()]


@router.post(
    "/review-packages/{review_id}/analyses",
    response_model=AnalysisRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_analysis_run(review_id: str, db: DbSession, queue: AnalysisQueueDependency, user: CurrentUser):
    """Create one durable item per URS entry and dispatch them to Redis/RQ."""
    service = scoped_review_service(db, user)
    try:
        item = service.create_analysis_run(review_id)
        item = enqueue_analysis_items(service, service.get_analysis_run(item.id), queue)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AnalysisQueueUnavailable as exc:
        # Keep a durable failed record so the reviewer can diagnose or retry the
        # run after Redis is restored instead of silently losing their request.
        if "item" in locals():
            service.mark_analysis_run_enqueue_failed(item.id, str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return analysis_run_response(item)


@router.get("/review-packages/{review_id}/analyses", response_model=list[AnalysisRunResponse])
def list_analysis_runs(review_id: str, db: DbSession, user: CurrentUser):
    """List previous durable URS reviews so work can resume after a later sign-in."""
    try:
        return [analysis_run_response(item) for item in scoped_review_service(db, user).list_analysis_runs(review_id)]
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/analysis-runs/{analysis_run_id}", response_model=AnalysisRunResponse)
def get_analysis_run(analysis_run_id: str, db: DbSession, user: CurrentUser):
    try:
        item = scoped_review_service(db, user).get_analysis_run(analysis_run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return analysis_run_response(item)


@router.post("/analysis-runs/{analysis_run_id}/execute", response_model=AnalysisRunResponse)
def execute_analysis_run(analysis_run_id: str, db: DbSession, queue: AnalysisQueueDependency, user: CurrentUser):
    """Compatibility endpoint: enqueue any un-dispatched queued items."""
    service = scoped_review_service(db, user)
    try:
        item = service.get_analysis_run(analysis_run_id)
        if item.status == "completed":
            raise ValueError("Analysis run is already completed")
        item = enqueue_analysis_items(service, item, queue)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AnalysisQueueUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return analysis_run_response(item)


@router.post("/analysis-runs/{analysis_run_id}/retry", response_model=AnalysisRunResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_failed_analysis_items(analysis_run_id: str, db: DbSession, queue: AnalysisQueueDependency, user: CurrentUser):
    """Requeue only terminally failed URS items; completed evidence remains intact."""
    service = scoped_review_service(db, user)
    try:
        service.retry_failed_analysis_items(analysis_run_id)
        item = enqueue_analysis_items(service, service.get_analysis_run(analysis_run_id), queue)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AnalysisQueueUnavailable as exc:
        service.mark_analysis_run_enqueue_failed(analysis_run_id, str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return analysis_run_response(item)


@router.get("/analysis-runs/{analysis_run_id}/progress", response_model=AnalysisRunProgressResponse)
def get_analysis_run_progress(analysis_run_id: str, db: DbSession, user: CurrentUser):
    try:
        return analysis_progress_response(scoped_review_service(db, user).get_analysis_run(analysis_run_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/analysis-runs/{analysis_run_id}/events")
async def stream_analysis_run_progress(analysis_run_id: str, request: Request, db: DbSession, user: CurrentUser):
    """Send durable progress snapshots as SSE; clients may reconnect safely."""
    try:
        analysis_progress_response(scoped_review_service(db, user).get_analysis_run(analysis_run_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def events():
        previous_payload: str | None = None
        terminal = {"completed", "failed"}
        while not await request.is_disconnected():
            session = get_session_factory()()
            try:
                progress = analysis_progress_response(
                    scoped_review_service(session, user).get_analysis_run(analysis_run_id)
                )
                payload = progress.model_dump_json()
            finally:
                session.close()
            if payload != previous_payload:
                yield f"event: progress\ndata: {payload}\n\n"
                previous_payload = payload
            if progress.status in terminal:
                yield f"event: complete\ndata: {payload}\n\n"
                return
            yield ": heartbeat\n\n"
            await asyncio.sleep(get_settings().analysis_progress_poll_seconds)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/analysis-runs/{analysis_run_id}/findings", response_model=list[FindingResponse])
def list_findings(analysis_run_id: str, db: DbSession, user: CurrentUser):
    try:
        findings = scoped_review_service(db, user).list_findings(analysis_run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [finding_response(item) for item in findings]


@router.get("/analysis-runs/{analysis_run_id}/matrix", response_model=list[MatrixRowResponse])
def get_traceability_matrix(analysis_run_id: str, db: DbSession, user: CurrentUser):
    """Return a complete traceability matrix, including worker failures and pending rows."""
    service = scoped_review_service(db, user)
    try:
        run = service.get_analysis_run(analysis_run_id)
        findings = service.list_findings(analysis_run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    findings_by_requirement_id = {finding.requirement_snapshot_id: finding for finding in findings}
    return [
        matrix_row_response(item, findings_by_requirement_id)
        for item in sorted(run.items, key=lambda value: value.requirement_snapshot.requirement_code)
    ]


@router.get("/analysis-runs/{analysis_run_id}/export.xlsx")
def export_traceability_matrix(analysis_run_id: str, db: DbSession, user: CurrentUser):
    """Export the complete matrix with immutable review scope metadata."""
    service = scoped_review_service(db, user)
    try:
        run = service.get_analysis_run(analysis_run_id)
        review = service.get_review_package(run.review_package_id)
        findings = service.list_findings(analysis_run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    findings_by_requirement_id = {finding.requirement_snapshot_id: finding for finding in findings}
    rows = [
        matrix_row_response(item, findings_by_requirement_id)
        for item in sorted(run.items, key=lambda value: value.requirement_snapshot.requirement_code)
    ]
    documents = [
        db.get(DocumentVersion, link.document_version_id)
        for link in review.document_links
    ]
    workbook = MatrixExportService().build(
        review=review,
        run=run,
        rows=rows,
        documents=[item for item in documents if item is not None],
    )
    filename = f"{review.name[:80].replace('/', '-')}-traceability-matrix.xlsx"
    return StreamingResponse(
        iter([workbook]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/design-review/chat", response_model=ReviewChatResponse)
def design_review_chat(payload: ReviewChatRequest, db: DbSession, user: CurrentUser):
    """Ask Chinese or English questions against only one frozen review scope."""
    settings = get_settings()
    try:
        review = scoped_review_service(db, user).get_review_package(payload.review_package_id)
        embeddings = ConfiguredEmbeddingService(settings)
        chat_model = create_chat_model(settings)
        answer, citations, retrieval_query = DesignReviewChatService(
            retrieval=MilvusRetrievalService(
                repository=MilvusChunkRepository(
                    uri=settings.milvus_uri,
                    collection_name=settings.milvus_collection,
                    dimension=settings.embedding_dimensions,
                ),
                embeddings=embeddings,
            ),
            normalizer=ConfiguredQueryNormalizer(chat_model),
            generator=ConfiguredGroundedAnswerGenerator(chat_model),
        ).answer(
            question=payload.question,
            document_version_ids=[link.document_version_id for link in review.document_links],
            system=review.system,
            conversation_history=[(message.role, message.content) for message in payload.conversation_history],
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Design review chat failed: {exc}") from exc
    return ReviewChatResponse(
        answer=answer.answer,
        retrieval_query=retrieval_query,
        limitations=answer.limitations,
        citations=[
            ReviewChatCitation(
                chunk_id=item.chunk_id,
                document_version_id=item.document_version_id,
                document_title=item.document_title,
                version=item.version,
                page=item.page,
                section=item.section,
                excerpt=item.content,
            )
            for item in citations
        ],
    )
