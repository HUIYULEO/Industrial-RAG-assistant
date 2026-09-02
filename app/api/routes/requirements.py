"""Controlled requirement-baseline import and query endpoints."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.exc import IntegrityError

from app.api.auth import require_admin_user, require_authenticated_user
from app.api.dependencies import DbSession
from app.api.schemas import (
    RequirementBaselineCreate,
    RequirementBaselineImportResponse,
    RequirementBaselineResponse,
    RequirementImportResponse,
    RequirementResponse,
)
from app.core.config import get_settings
from app.domain.models import Requirement, RequirementBaseline
from app.services.review_service import ReviewService

router = APIRouter(tags=["requirement-baselines"], dependencies=[Depends(require_authenticated_user)])


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


def read_csv_upload(file: UploadFile) -> bytes:
    settings = get_settings()
    max_upload_bytes = settings.max_upload_size_mb * 1024 * 1024
    content = file.file.read(max_upload_bytes + 1)
    if len(content) > max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {settings.max_upload_size_mb} MB limit",
        )
    return content


@router.post(
    "/requirement-baselines",
    response_model=RequirementBaselineResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_user)],
)
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
    dependencies=[Depends(require_admin_user)],
)
def import_requirement_baseline(
    db: DbSession,
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    system: str | None = Form(default=None),
):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="For now, import a UTF-8 CSV export of the URS or ES table")
    try:
        baseline, requirements = ReviewService(db).create_baseline_from_csv(
            file_name=file.filename,
            content=read_csv_upload(file),
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
    dependencies=[Depends(require_admin_user)],
)
def import_requirements(baseline_id: str, db: DbSession, file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="A CSV file is required")
    try:
        requirements = ReviewService(db).import_requirements_csv(
            baseline_id, read_csv_upload(file)
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RequirementImportResponse(
        imported_count=len(requirements),
        requirements=[requirement_response(item) for item in requirements],
    )


@router.get("/requirement-baselines/{baseline_id}/requirements", response_model=list[RequirementResponse])
def list_requirements(baseline_id: str, db: DbSession):
    try:
        requirements = ReviewService(db).list_requirements(baseline_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [requirement_response(item) for item in requirements]
