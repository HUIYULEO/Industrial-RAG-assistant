"""Asynchronous requirement-analysis and traceability-matrix endpoints."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.api.auth import require_authenticated_user
from app.api.dependencies import CurrentUser, DbSession, scoped_review_service
from app.api.schemas import (
    AnalysisRunItemResponse,
    AnalysisRunCreate,
    AnalysisRunProgressResponse,
    AnalysisRunResponse,
    AuditPointResponse,
    FindingEvidenceResponse,
    FindingResponse,
    MatrixRowResponse,
)
from app.core.config import get_settings
from app.domain.enums import coverage_status_definition
from app.domain.models import AnalysisRun, DocumentVersion, ReviewFinding
from app.repositories.database import get_session_factory
from app.services.matrix_export_service import MatrixExportService
from app.services.review_service import ReviewService

router = APIRouter(tags=["analysis-runs"], dependencies=[Depends(require_authenticated_user)])


def analysis_run_response(item: AnalysisRun) -> AnalysisRunResponse:
    return AnalysisRunResponse(
        id=item.id,
        review_package_id=item.review_package_id,
        status=item.status,
        strategy=item.strategy,
        strategy_version=item.strategy_version,
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


def _load_analysis_progress(analysis_run_id: str, user: CurrentUser) -> AnalysisRunProgressResponse:
    session = get_session_factory()()
    try:
        return analysis_progress_response(
            scoped_review_service(session, user).get_analysis_run(analysis_run_id)
        )
    finally:
        session.close()


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
    audit_points = [
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
        for point in item.audit_points or []
    ]
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


@router.post("/review-packages/{review_id}/analyses", response_model=AnalysisRunResponse, status_code=status.HTTP_202_ACCEPTED)
def create_analysis_run(
    review_id: str,
    db: DbSession,
    user: CurrentUser,
    payload: AnalysisRunCreate | None = None,
):
    service = scoped_review_service(db, user)
    try:
        item = service.create_analysis_run(review_id, strategy=(payload or AnalysisRunCreate()).strategy)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return analysis_run_response(item)


@router.get("/review-packages/{review_id}/analyses", response_model=list[AnalysisRunResponse])
def list_analysis_runs(review_id: str, db: DbSession, user: CurrentUser):
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


@router.post(
    "/analysis-runs/{analysis_run_id}/execute",
    response_model=AnalysisRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def execute_analysis_run(analysis_run_id: str, db: DbSession, user: CurrentUser):
    service = scoped_review_service(db, user)
    try:
        item = service.get_analysis_run(analysis_run_id)
        if item.status == "completed":
            raise ValueError("Analysis run is already completed")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return analysis_run_response(item)


@router.post("/analysis-runs/{analysis_run_id}/retry", response_model=AnalysisRunResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_failed_analysis_items(analysis_run_id: str, db: DbSession, user: CurrentUser):
    service = scoped_review_service(db, user)
    try:
        service.retry_failed_analysis_items(analysis_run_id)
        item = service.get_analysis_run(analysis_run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return analysis_run_response(item)


@router.get("/analysis-runs/{analysis_run_id}/progress", response_model=AnalysisRunProgressResponse)
def get_analysis_run_progress(analysis_run_id: str, db: DbSession, user: CurrentUser):
    try:
        return analysis_progress_response(scoped_review_service(db, user).get_analysis_run(analysis_run_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/analysis-runs/{analysis_run_id}/events")
async def stream_analysis_run_progress(
    analysis_run_id: str, request: Request, user: CurrentUser
):
    try:
        await run_in_threadpool(_load_analysis_progress, analysis_run_id, user)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    poll_seconds = get_settings().analysis_progress_poll_seconds

    async def events():
        previous_payload: str | None = None
        terminal = {"completed", "failed"}
        while not await request.is_disconnected():
            progress = await run_in_threadpool(_load_analysis_progress, analysis_run_id, user)
            payload = progress.model_dump_json()
            if payload != previous_payload:
                yield f"event: progress\ndata: {payload}\n\n"
                previous_payload = payload
            if progress.status in terminal:
                yield f"event: complete\ndata: {payload}\n\n"
                return
            yield ": heartbeat\n\n"
            await asyncio.sleep(poll_seconds)

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
    documents = [db.get(DocumentVersion, link.document_version_id) for link in review.document_links]
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
