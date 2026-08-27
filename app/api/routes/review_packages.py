"""Frozen review-package lifecycle endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError

from app.api.auth import require_authenticated_user
from app.api.dependencies import CurrentUser, DbSession, scoped_review_service
from app.api.schemas import ReviewPackageCreate, ReviewPackageResponse
from app.domain.models import ReviewPackage

router = APIRouter(tags=["review-packages"], dependencies=[Depends(require_authenticated_user)])


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
