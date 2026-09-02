"""Pydantic request and response contracts for review-workspace APIs."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

from app.domain.enums import DocumentStatus, DocumentType


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    document_type: DocumentType
    system: str = Field(min_length=1, max_length=150)
    vendor: str | None = Field(default=None, max_length=150)
    version: str = Field(min_length=1, max_length=80)
    status: DocumentStatus = DocumentStatus.DRAFT
    file_name: str | None = None
    file_hash: str | None = Field(default=None, min_length=64, max_length=64)
    source_url: HttpUrl | None = None
    supersedes_version_id: str | None = None


class DocumentArchiveRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class DocumentVersionResponse(BaseModel):
    id: str
    document_id: str
    title: str
    document_type: DocumentType
    system: str
    vendor: str | None
    version: str
    status: DocumentStatus
    file_name: str | None
    source_url: str | None
    ingestion_status: str
    ingestion_error: str | None
    page_count: int | None
    chunk_count: int
    supersedes_version_id: str | None
    archived_at: datetime | None
    archived_by_user_id: str | None
    archived_reason: str | None
    created_at: datetime | None


class RequirementBaselineCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    system: str = Field(min_length=1, max_length=150)
    description: str | None = None


class RequirementBaselineResponse(BaseModel):
    id: str
    name: str
    system: str
    description: str | None
    created_at: datetime | None


class RequirementResponse(BaseModel):
    id: str
    requirement_code: str
    requirement_text: str
    source_row: str | None
    requirement_system: str | None
    rationale_impact: str | None
    is_critical: bool
    priority: str | None
    category: str | None
    source_section: str | None


class RequirementImportResponse(BaseModel):
    imported_count: int
    requirements: list[RequirementResponse]


class RequirementBaselineImportResponse(BaseModel):
    baseline: RequirementBaselineResponse
    imported_count: int
    requirements: list[RequirementResponse]


class ReviewPackageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    system: str = Field(min_length=1, max_length=150)
    requirement_baseline_id: str
    design_document_version_ids: list[str] = Field(min_length=1)


class ReviewPackageResponse(BaseModel):
    id: str
    owner_user_id: str
    organization_id: str
    name: str
    system: str
    requirement_baseline_id: str
    design_document_version_ids: list[str]
    requirement_count: int
    created_at: datetime | None


class AnalysisRunResponse(BaseModel):
    id: str
    review_package_id: str
    status: str
    strategy: Literal["original", "decomposed"]
    strategy_version: str
    error_message: str | None
    created_at: datetime | None
    completed_at: datetime | None


class AnalysisRunCreate(BaseModel):
    strategy: Literal["original", "decomposed"] = "decomposed"


class AnalysisRunItemResponse(BaseModel):
    id: str
    requirement_code: str
    status: str
    attempt_count: int
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None


class AnalysisRunProgressResponse(AnalysisRunResponse):
    total_items: int
    queued_items: int
    running_items: int
    completed_items: int
    failed_items: int
    items: list[AnalysisRunItemResponse]


class DocumentChunkResponse(BaseModel):
    id: str
    chunk_index: int
    page: int
    section: str | None
    element_type: str
    source_metadata: dict | None
    content: str


class DocumentChunkContextResponse(BaseModel):
    """The cited passage plus adjacent source passages for in-app reading."""

    document_version_id: str
    requested_chunk_id: str
    chunks: list[DocumentChunkResponse]


class DocumentFigureResponse(BaseModel):
    id: str
    page: int
    section: str | None
    image_available: bool
    analysis_status: str
    analysis_error: str | None
    diagram_type: str | None
    visible_labels: list[str]
    candidate_description: str | None
    candidate_relationships: list[str]
    citation_chunk_id: str | None


class FindingEvidenceResponse(BaseModel):
    chunk_id: str
    document_version_id: str
    document_title: str
    version: str
    page: int | None
    section: str | None
    excerpt: str


class AuditPointResponse(BaseModel):
    point_id: str
    source_excerpt: str
    review_point: str
    design_status: str
    status_definition: str
    rationale: str
    evidence: list[FindingEvidenceResponse]


class FindingResponse(BaseModel):
    id: str
    requirement_code: str
    requirement_text: str
    design_status: str
    rationale: str
    gap: str | None
    suggested_reviewer_action: str | None
    evidence: list[FindingEvidenceResponse]
    audit_points: list[AuditPointResponse] = Field(default_factory=list)


class MatrixRowResponse(BaseModel):
    """Exactly one report row for every frozen URS entry."""

    requirement_code: str
    requirement_text: str
    rationale_impact: str | None
    is_critical: bool
    priority: str | None
    category: str | None
    analysis_status: str
    technical_error: str | None
    design_status: str | None
    status_definition: str | None
    rationale: str | None
    gap: str | None
    suggested_reviewer_action: str | None
    evidence: list[FindingEvidenceResponse] = Field(default_factory=list)
    audit_points: list[AuditPointResponse] = Field(default_factory=list)


class ReviewChatHistoryMessage(BaseModel):
    """Conversation context supplied by the current user's browser only."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ReviewChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    review_package_id: str
    # The client retains more messages for display but sends only a bounded
    # recent window to make follow-up questions understandable.
    conversation_history: list[ReviewChatHistoryMessage] = Field(default_factory=list, max_length=12)


class ReviewChatCitation(BaseModel):
    chunk_id: str
    document_version_id: str
    document_title: str
    version: str
    page: int | None
    section: str | None
    excerpt: str


class ReviewChatResponse(BaseModel):
    answer: str
    retrieval_query: str
    limitations: str | None
    citations: list[ReviewChatCitation]


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=512)


class RegisterRequest(BaseModel):
    display_name: str = Field(min_length=2, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=512)
    department: str = Field(min_length=2, max_length=50)


class AuthUserResponse(BaseModel):
    id: str
    organization_id: str
    email: str
    display_name: str
    role: str


class AuthConfigResponse(BaseModel):
    authentication_required: bool
    self_registration_enabled: bool
    visual_analysis_enabled: bool
    departments: list[str]


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUserResponse
