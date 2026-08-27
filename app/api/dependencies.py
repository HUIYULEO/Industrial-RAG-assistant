"""Shared HTTP-layer dependencies and scoped service factories."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.auth import require_authenticated_user
from app.bootstrap.service_factory import (
    build_document_indexing_service,
    build_document_ingestion_service,
    build_design_review_chat_service,
    build_visual_evidence_service,
    build_visual_interpreter,
)
from app.repositories.database import get_db
from app.services.analysis_queue import AnalysisQueue, get_analysis_queue
from app.services.auth_service import AuthenticatedUser
from app.services.design_review_chat_service import DesignReviewChatService
from app.services.indexing_service import DocumentIndexingService
from app.services.ingestion_service import DocumentIngestionService
from app.services.review_service import ReviewService
from app.services.visual_evidence_service import VisualEvidenceService, VisualInterpreter

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[AuthenticatedUser, Depends(require_authenticated_user)]
AnalysisQueueDependency = Annotated[AnalysisQueue, Depends(get_analysis_queue)]


def get_document_ingestion_service(db: DbSession) -> DocumentIngestionService:
    return build_document_ingestion_service(db)


def get_document_indexing_service(db: DbSession) -> DocumentIndexingService:
    return build_document_indexing_service(db)


def get_visual_evidence_service(db: DbSession) -> VisualEvidenceService:
    return build_visual_evidence_service(db)


DocumentIngestionDependency = Annotated[DocumentIngestionService, Depends(get_document_ingestion_service)]
DocumentIndexingDependency = Annotated[DocumentIndexingService, Depends(get_document_indexing_service)]
VisualEvidenceDependency = Annotated[VisualEvidenceService, Depends(get_visual_evidence_service)]
VisualInterpreterDependency = Annotated[VisualInterpreter, Depends(build_visual_interpreter)]
DesignReviewChatDependency = Annotated[DesignReviewChatService, Depends(build_design_review_chat_service)]


def scoped_review_service(db: Session, user: AuthenticatedUser) -> ReviewService:
    """Create a review service restricted to the authenticated user's tenant."""
    return ReviewService(db, owner_user_id=user.id, organization_id=user.organization_id)
