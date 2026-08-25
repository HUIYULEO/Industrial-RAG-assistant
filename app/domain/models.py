"""SQLAlchemy models for document versions and design-review scope."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, false, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Organization(Base):
    """Tenant boundary for users and their private review workspaces."""

    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    users: Mapped[list[User]] = relationship(back_populates="organization")
    review_packages: Mapped[list[ReviewPackage]] = relationship(back_populates="organization")


class User(Base):
    """Local identity record; production deployments may replace it with corporate SSO."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False, default="engineer")
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization: Mapped[Organization] = relationship(back_populates="users")
    review_packages: Mapped[list[ReviewPackage]] = relationship(back_populates="owner")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    system: Mapped[str] = mapped_column(String(150), nullable=False)
    vendor: Mapped[str | None] = mapped_column(String(150))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    versions: Mapped[list[DocumentVersion]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version", name="uq_document_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    file_name: Mapped[str | None] = mapped_column(String(300))
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    storage_path: Mapped[str | None] = mapped_column(String(1000))
    ingestion_status: Mapped[str] = mapped_column(String(40), nullable=False, default="registered")
    ingestion_error: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    supersedes_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_versions.id"), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by_user_id: Mapped[str | None] = mapped_column(String(36))
    archived_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="versions", foreign_keys=[document_id])
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document_version", cascade="all, delete-orphan"
    )
    figures: Mapped[list[DocumentFigure]] = relationship(
        back_populates="document_version", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    """Parsed citable content; Milvus stores its vector index, not the source of truth."""

    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("document_version_id", "chunk_index", name="uq_version_chunk_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document_version: Mapped[DocumentVersion] = relationship(back_populates="chunks")


class DocumentFigure(Base):
    """A rendered PDF page selected as visual evidence for engineering review."""

    __tablename__ = "document_figures"
    __table_args__ = (UniqueConstraint("document_version_id", "page", name="uq_version_figure_page"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), nullable=False)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(String(500))
    image_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    image_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_status: Mapped[str] = mapped_column(String(30), nullable=False, default="extracted")
    analysis_error: Mapped[str | None] = mapped_column(Text)
    diagram_type: Mapped[str | None] = mapped_column(String(120))
    visible_labels: Mapped[list[str] | None] = mapped_column(JSON)
    candidate_description: Mapped[str | None] = mapped_column(Text)
    candidate_relationships: Mapped[list[str] | None] = mapped_column(JSON)
    citation_chunk_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document_version: Mapped[DocumentVersion] = relationship(back_populates="figures")


class RequirementBaseline(Base):
    __tablename__ = "requirement_baselines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    system: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    requirements: Mapped[list[Requirement]] = relationship(
        back_populates="baseline", cascade="all, delete-orphan"
    )


class Requirement(Base):
    __tablename__ = "requirements"
    __table_args__ = (UniqueConstraint("baseline_id", "requirement_code", name="uq_baseline_requirement"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    baseline_id: Mapped[str] = mapped_column(ForeignKey("requirement_baselines.id"), nullable=False)
    requirement_code: Mapped[str] = mapped_column(String(100), nullable=False)
    requirement_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_row: Mapped[str | None] = mapped_column(String(50))
    requirement_system: Mapped[str | None] = mapped_column(String(150))
    rationale_impact: Mapped[str | None] = mapped_column(Text)
    is_critical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    priority: Mapped[str | None] = mapped_column(String(40))
    category: Mapped[str | None] = mapped_column(String(100))
    source_section: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    baseline: Mapped[RequirementBaseline] = relationship(back_populates="requirements")


class ReviewPackage(Base):
    __tablename__ = "review_packages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    system: Mapped[str] = mapped_column(String(150), nullable=False)
    requirement_baseline_id: Mapped[str] = mapped_column(
        ForeignKey("requirement_baselines.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    owner: Mapped[User] = relationship(back_populates="review_packages")
    organization: Mapped[Organization] = relationship(back_populates="review_packages")

    document_links: Mapped[list[ReviewPackageDocument]] = relationship(
        back_populates="review_package", cascade="all, delete-orphan"
    )
    requirement_snapshots: Mapped[list[ReviewPackageRequirement]] = relationship(
        back_populates="review_package", cascade="all, delete-orphan"
    )
    analysis_runs: Mapped[list[AnalysisRun]] = relationship(
        back_populates="review_package", cascade="all, delete-orphan"
    )


class ReviewPackageDocument(Base):
    __tablename__ = "review_package_documents"
    __table_args__ = (UniqueConstraint("review_package_id", "document_version_id", name="uq_review_document"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    review_package_id: Mapped[str] = mapped_column(ForeignKey("review_packages.id"), nullable=False)
    document_version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False)

    review_package: Mapped[ReviewPackage] = relationship(back_populates="document_links")


class ReviewPackageRequirement(Base):
    """Immutable copy of a baseline item as it was when a review was created."""

    __tablename__ = "review_package_requirements"
    __table_args__ = (UniqueConstraint("review_package_id", "requirement_id", name="uq_review_requirement"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    review_package_id: Mapped[str] = mapped_column(ForeignKey("review_packages.id"), nullable=False)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.id"), nullable=False)
    requirement_code: Mapped[str] = mapped_column(String(100), nullable=False)
    requirement_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_row: Mapped[str | None] = mapped_column(String(50))
    requirement_system: Mapped[str | None] = mapped_column(String(150))
    rationale_impact: Mapped[str | None] = mapped_column(Text)
    is_critical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    priority: Mapped[str | None] = mapped_column(String(40))
    category: Mapped[str | None] = mapped_column(String(100))

    review_package: Mapped[ReviewPackage] = relationship(back_populates="requirement_snapshots")


class AnalysisRun(Base):
    """A repeatable candidate-finding run for one frozen review package."""

    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    review_package_id: Mapped[str] = mapped_column(ForeignKey("review_packages.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    review_package: Mapped[ReviewPackage] = relationship(back_populates="analysis_runs")
    findings: Mapped[list[ReviewFinding]] = relationship(
        back_populates="analysis_run", cascade="all, delete-orphan"
    )
    items: Mapped[list[AnalysisRunItem]] = relationship(
        back_populates="analysis_run", cascade="all, delete-orphan"
    )


class AnalysisRunItem(Base):
    """Durable state for one independently processed frozen URS item."""

    __tablename__ = "analysis_run_items"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "requirement_snapshot_id", name="uq_run_requirement_item"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    analysis_run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False)
    requirement_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("review_package_requirements.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    job_id: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    analysis_run: Mapped[AnalysisRun] = relationship(back_populates="items")
    requirement_snapshot: Mapped[ReviewPackageRequirement] = relationship()


class ReviewFinding(Base):
    """A candidate finding, never an automated approval or compliance decision."""

    __tablename__ = "review_findings"
    __table_args__ = (UniqueConstraint("analysis_run_id", "requirement_snapshot_id", name="uq_run_finding"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    analysis_run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False)
    requirement_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("review_package_requirements.id"), nullable=False
    )
    design_status: Mapped[str] = mapped_column(String(40), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    gap: Mapped[str | None] = mapped_column(Text)
    suggested_reviewer_action: Mapped[str | None] = mapped_column(Text)
    # Structured, source-traceable checks generated for a composite URS.  A
    # JSON representation keeps the report immutable together with the parent
    # finding while the visible matrix remains one row per original URS.
    audit_points: Mapped[list[dict] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    analysis_run: Mapped[AnalysisRun] = relationship(back_populates="findings")
    requirement_snapshot: Mapped[ReviewPackageRequirement] = relationship()
    evidence: Mapped[list[FindingEvidence]] = relationship(
        back_populates="finding", cascade="all, delete-orphan"
    )


class FindingEvidence(Base):
    """Frozen citation selected for a candidate finding at analysis time."""

    __tablename__ = "finding_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    finding_id: Mapped[str] = mapped_column(ForeignKey("review_findings.id"), nullable=False)
    chunk_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_title: Mapped[str] = mapped_column(String(512), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(512))
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)

    finding: Mapped[ReviewFinding] = relationship(back_populates="evidence")
