"""Application service for immutable design-review scope management."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.domain.enums import DocumentStatus, DocumentType
from app.domain.models import (
    Document,
    DocumentVersion,
    Requirement,
    RequirementBaseline,
    ReviewPackage,
    ReviewPackageDocument,
    ReviewPackageRequirement,
    AnalysisRun,
    AnalysisRunItem,
    ReviewFinding,
)


class ReviewService:
    """Owns business rules independent of HTTP or a particular vector database."""

    REQUIRED_CSV_COLUMNS = {"requirement_code", "requirement_text"}
    URS_TABLE_COLUMNS = {
        "source_row": ("序号", "编号", "no", "number", "item", "id"),
        "requirement_system": ("系统", "system"),
        "requirement_text": ("requirement", "requirement text", "需求", "需求描述", "要求"),
        "rationale_impact": (
            "reasonal/impact",
            "rationale/impact",
            "rationale",
            "reason",
            "impact",
            "理由/影响",
            "合理性/影响",
        ),
        "is_critical": ("是否critical", "critical", "is critical", "关键", "是否关键"),
        "priority": ("priority", "优先级"),
        "category": ("category", "类别", "分类"),
    }

    def __init__(
        self,
        db: Session,
        *,
        owner_user_id: str | None = None,
        organization_id: str | None = None,
    ):
        self.db = db
        self.owner_user_id = owner_user_id
        self.organization_id = organization_id

    def _require_review_scope(self) -> tuple[str, str]:
        if not self.owner_user_id or not self.organization_id:
            raise PermissionError("A user and organization are required for review workspace access")
        return self.owner_user_id, self.organization_id

    def create_document(
        self,
        *,
        title: str,
        document_type: DocumentType,
        system: str,
        vendor: str | None,
        version: str,
        status: DocumentStatus,
        file_name: str | None = None,
        file_hash: str | None = None,
        source_url: str | None = None,
        supersedes_version_id: str | None = None,
    ) -> DocumentVersion:
        document: Document | None = None
        if supersedes_version_id:
            previous = self.db.get(DocumentVersion, supersedes_version_id)
            if previous is None:
                raise ValueError("supersedes_version_id does not exist")
            previous_document = previous.document
            if (
                previous_document.title != title
                or previous_document.document_type != document_type.value
                or previous_document.system != system
            ):
                raise ValueError(
                    "A replacement version must keep the same title, document type, and system as the superseded version"
                )
            previous.status = DocumentStatus.SUPERSEDED.value
            document = previous_document
        else:
            document = Document(
                title=title,
                document_type=document_type.value,
                system=system,
                vendor=vendor,
            )
        version_record = DocumentVersion(
            document=document,
            version=version,
            status=status.value,
            file_name=file_name,
            file_hash=file_hash,
            source_url=source_url,
            supersedes_version_id=supersedes_version_id,
        )
        self.db.add(version_record)
        self.db.commit()
        self.db.refresh(version_record)
        return version_record

    def list_document_versions(self) -> list[DocumentVersion]:
        statement = select(DocumentVersion).options(selectinload(DocumentVersion.document)).order_by(
            DocumentVersion.created_at.desc()
        )
        return list(self.db.scalars(statement))

    def create_baseline(self, *, name: str, system: str, description: str | None) -> RequirementBaseline:
        baseline = RequirementBaseline(name=name, system=system, description=description)
        self.db.add(baseline)
        self.db.commit()
        self.db.refresh(baseline)
        return baseline

    def list_baselines(self) -> list[RequirementBaseline]:
        return list(
            self.db.scalars(select(RequirementBaseline).order_by(RequirementBaseline.created_at.desc()))
        )

    @staticmethod
    def _normalise_column_name(value: str) -> str:
        return "".join(value.strip().lower().replace("_", " ").split())

    @classmethod
    def _table_value(cls, row: dict[str, str | None], aliases: tuple[str, ...]) -> str:
        normalised = {cls._normalise_column_name(key): (value or "").strip() for key, value in row.items()}
        for alias in aliases:
            if value := normalised.get(cls._normalise_column_name(alias)):
                return value
        return ""

    @staticmethod
    def _critical_value(value: str, row_number: int) -> bool:
        normalized = value.strip().lower()
        if normalized in {"", "no", "n", "false", "0", "否", "非关键", "non-critical"}:
            return False
        if normalized in {"yes", "y", "true", "1", "是", "关键", "critical"}:
            return True
        raise ValueError(
            f"Row {row_number} has an unrecognised critical value '{value}'. Use Yes/No, True/False, or 是/否"
        )

    @staticmethod
    def _requirement_code(source_row: str, row_number: int) -> str:
        label = source_row.strip().upper().replace(" ", "-")
        if not label:
            return f"URS-{row_number - 1:03d}"
        if label.startswith("URS-"):
            return label
        if label.isdigit():
            return f"URS-{int(label):03d}"
        return f"URS-{label}"

    def create_baseline_from_csv(
        self,
        *,
        file_name: str,
        content: bytes,
        name: str | None = None,
        system: str | None = None,
    ) -> tuple[RequirementBaseline, list[Requirement]]:
        """Create a baseline from the familiar controlled URS/ES table structure."""
        try:
            reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        except UnicodeDecodeError as exc:
            raise ValueError("CSV must be UTF-8 encoded") from exc
        if not reader.fieldnames:
            raise ValueError("The URS/ES table must include a header row")

        available = {self._normalise_column_name(column) for column in reader.fieldnames}
        requirement_aliases = self.URS_TABLE_COLUMNS["requirement_text"]
        if not any(self._normalise_column_name(alias) in available for alias in requirement_aliases):
            raise ValueError("The URS/ES table needs a Requirement or 需求 column")

        rows = list(reader)
        if not rows:
            raise ValueError("The URS/ES table does not contain any requirements")

        imported_rows: list[dict[str, str | bool]] = []
        systems: set[str] = set()
        requirement_aliases = self.URS_TABLE_COLUMNS["requirement_system"]
        for row_number, row in enumerate(rows, start=2):
            requirement_text = self._table_value(row, self.URS_TABLE_COLUMNS["requirement_text"])
            if not requirement_text:
                raise ValueError(f"Row {row_number} requires a Requirement value")
            requirement_system = self._table_value(row, requirement_aliases) or (system or "").strip()
            if not requirement_system:
                raise ValueError(f"Row {row_number} requires a System value")
            systems.add(requirement_system)
            source_row = self._table_value(row, self.URS_TABLE_COLUMNS["source_row"])
            imported_rows.append(
                {
                    "source_row": source_row,
                    "requirement_code": self._requirement_code(source_row, row_number),
                    "requirement_text": requirement_text,
                    "requirement_system": requirement_system,
                    "rationale_impact": self._table_value(row, self.URS_TABLE_COLUMNS["rationale_impact"]),
                    "is_critical": self._critical_value(
                        self._table_value(row, self.URS_TABLE_COLUMNS["is_critical"]), row_number
                    ),
                    "priority": self._table_value(row, self.URS_TABLE_COLUMNS["priority"]),
                    "category": self._table_value(row, self.URS_TABLE_COLUMNS["category"]),
                }
            )

        codes = [str(row["requirement_code"]) for row in imported_rows]
        if len(codes) != len(set(codes)):
            raise ValueError("URS/ES sequence values must be unique within the uploaded table")

        baseline_system = (system or "").strip() or (next(iter(systems)) if len(systems) == 1 else "multi_system")
        baseline = RequirementBaseline(
            name=(name or "").strip() or Path(file_name).stem,
            system=baseline_system,
            description=f"Imported from controlled requirement table: {file_name}",
        )
        self.db.add(baseline)
        self.db.flush()
        requirements = [
            Requirement(
                baseline_id=baseline.id,
                requirement_code=str(row["requirement_code"]),
                requirement_text=str(row["requirement_text"]),
                source_row=str(row["source_row"]) or None,
                requirement_system=str(row["requirement_system"]),
                rationale_impact=str(row["rationale_impact"]) or None,
                is_critical=bool(row["is_critical"]),
                priority=str(row["priority"]) or None,
                category=str(row["category"]) or None,
            )
            for row in imported_rows
        ]
        self.db.add_all(requirements)
        self.db.commit()
        self.db.refresh(baseline)
        for requirement in requirements:
            self.db.refresh(requirement)
        return baseline, requirements

    def import_requirements_csv(self, baseline_id: str, content: bytes) -> list[Requirement]:
        baseline = self.db.get(RequirementBaseline, baseline_id)
        if baseline is None:
            raise LookupError("Requirement baseline not found")

        try:
            reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        except UnicodeDecodeError as exc:
            raise ValueError("CSV must be UTF-8 encoded") from exc

        headers = set(reader.fieldnames or [])
        missing = self.REQUIRED_CSV_COLUMNS - headers
        if missing:
            raise ValueError(f"CSV is missing required columns: {', '.join(sorted(missing))}")

        imported: list[Requirement] = []
        known_codes = set(
            self.db.scalars(select(Requirement.requirement_code).where(Requirement.baseline_id == baseline_id))
        )
        for row_number, row in enumerate(reader, start=2):
            code = (row.get("requirement_code") or "").strip()
            text = (row.get("requirement_text") or "").strip()
            if not code or not text:
                raise ValueError(f"Row {row_number} requires requirement_code and requirement_text")
            if code in known_codes:
                raise ValueError(f"Duplicate requirement_code '{code}' in baseline")
            known_codes.add(code)
            requirement = Requirement(
                baseline_id=baseline_id,
                requirement_code=code,
                requirement_text=text,
                source_row=(row.get("source_row") or "").strip() or None,
                requirement_system=(row.get("system") or "").strip() or None,
                rationale_impact=(row.get("rationale_impact") or "").strip() or None,
                is_critical=(row.get("is_critical") or "").strip().lower() in {"yes", "true", "1", "是"},
                priority=(row.get("priority") or "").strip() or None,
                category=(row.get("category") or "").strip() or None,
                source_section=(row.get("source_section") or "").strip() or None,
            )
            self.db.add(requirement)
            imported.append(requirement)

        self.db.commit()
        for requirement in imported:
            self.db.refresh(requirement)
        return imported

    def list_requirements(self, baseline_id: str) -> list[Requirement]:
        if self.db.get(RequirementBaseline, baseline_id) is None:
            raise LookupError("Requirement baseline not found")
        return list(
            self.db.scalars(
                select(Requirement)
                .where(Requirement.baseline_id == baseline_id)
                .order_by(Requirement.requirement_code)
            )
        )

    def create_review_package(
        self,
        *,
        name: str,
        system: str,
        requirement_baseline_id: str,
        design_document_version_ids: list[str],
    ) -> ReviewPackage:
        if self.db.get(RequirementBaseline, requirement_baseline_id) is None:
            raise LookupError("Requirement baseline not found")
        requirements = self.list_requirements(requirement_baseline_id)
        if not requirements:
            raise ValueError("A review package requires at least one imported requirement")
        if not design_document_version_ids:
            raise ValueError("At least one supplier FS or DS version is required")

        versions = list(
            self.db.scalars(
                select(DocumentVersion)
                .options(selectinload(DocumentVersion.document))
                .where(DocumentVersion.id.in_(design_document_version_ids))
            )
        )
        if len(versions) != len(set(design_document_version_ids)):
            raise LookupError("One or more document versions were not found")
        invalid = [item.id for item in versions if item.document.document_type not in {"FS", "DS"}]
        if invalid:
            raise ValueError("Review packages currently accept only FS and DS document versions")

        owner_user_id, organization_id = self._require_review_scope()
        review = ReviewPackage(
            owner_user_id=owner_user_id,
            organization_id=organization_id,
            name=name,
            system=system,
            requirement_baseline_id=requirement_baseline_id,
        )
        for version in versions:
            review.document_links.append(ReviewPackageDocument(document_version_id=version.id, role="design_evidence"))
        for requirement in requirements:
            review.requirement_snapshots.append(
                ReviewPackageRequirement(
                    requirement_id=requirement.id,
                    requirement_code=requirement.requirement_code,
                    requirement_text=requirement.requirement_text,
                    source_row=requirement.source_row,
                    requirement_system=requirement.requirement_system,
                    rationale_impact=requirement.rationale_impact,
                    is_critical=requirement.is_critical,
                    priority=requirement.priority,
                    category=requirement.category,
                )
            )
        self.db.add(review)
        self.db.commit()
        self.db.refresh(review)
        return review

    def get_review_package(self, review_id: str) -> ReviewPackage:
        owner_user_id, organization_id = self._require_review_scope()
        statement = (
            select(ReviewPackage)
            .options(
                selectinload(ReviewPackage.document_links),
                selectinload(ReviewPackage.requirement_snapshots),
            )
            .where(
                ReviewPackage.id == review_id,
                ReviewPackage.owner_user_id == owner_user_id,
                ReviewPackage.organization_id == organization_id,
            )
        )
        review = self.db.scalar(statement)
        if review is None:
            raise LookupError("Review package not found")
        return review

    def list_review_packages(self) -> list[ReviewPackage]:
        owner_user_id, organization_id = self._require_review_scope()
        statement = select(ReviewPackage).options(
            selectinload(ReviewPackage.document_links),
            selectinload(ReviewPackage.requirement_snapshots),
        ).where(
            ReviewPackage.owner_user_id == owner_user_id,
            ReviewPackage.organization_id == organization_id,
        ).order_by(ReviewPackage.created_at.desc())
        return list(self.db.scalars(statement))

    def create_analysis_run(self, review_id: str) -> AnalysisRun:
        review = self.get_review_package(review_id)
        if not review.requirement_snapshots:
            raise ValueError("Review package has no frozen requirements")
        run = AnalysisRun(review_package_id=review.id, status="queued")
        run.items = [
            AnalysisRunItem(requirement_snapshot_id=requirement.id, status="queued")
            for requirement in review.requirement_snapshots
        ]
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def list_analysis_runs(self, review_id: str) -> list[AnalysisRun]:
        """Return durable audit runs for a Review Package, newest first."""
        self.get_review_package(review_id)
        return list(
            self.db.scalars(
                select(AnalysisRun)
                .where(AnalysisRun.review_package_id == review_id)
                .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
            )
        )

    def get_analysis_run(self, run_id: str) -> AnalysisRun:
        owner_user_id, organization_id = self._require_review_scope()
        run = self.db.scalar(
            select(AnalysisRun)
            .options(selectinload(AnalysisRun.items).selectinload(AnalysisRunItem.requirement_snapshot))
            .join(ReviewPackage, AnalysisRun.review_package_id == ReviewPackage.id)
            .where(
                AnalysisRun.id == run_id,
                ReviewPackage.owner_user_id == owner_user_id,
                ReviewPackage.organization_id == organization_id,
            )
        )
        if run is None:
            raise LookupError("Analysis run not found")
        return run

    def set_analysis_item_job_ids(self, run_id: str, job_ids: dict[str, str]) -> AnalysisRun:
        run = self.get_analysis_run(run_id)
        for item in run.items:
            if item.id in job_ids:
                item.job_id = job_ids[item.id]
        self.db.commit()
        return self.get_analysis_run(run_id)

    def mark_analysis_run_enqueue_failed(self, run_id: str, message: str) -> AnalysisRun:
        run = self.get_analysis_run(run_id)
        run.status = "failed"
        run.error_message = message
        for item in run.items:
            if item.status == "queued":
                item.status = "failed"
                item.error_message = message
        self.db.commit()
        return self.get_analysis_run(run_id)

    def retry_failed_analysis_items(self, run_id: str) -> list[AnalysisRunItem]:
        run = self.get_analysis_run(run_id)
        failed = [item for item in run.items if item.status == "failed"]
        if not failed:
            raise ValueError("Analysis run has no failed items to retry")
        run.status = "queued"
        run.error_message = None
        run.completed_at = None
        for item in failed:
            item.status = "queued"
            item.job_id = None
            item.error_message = None
            item.started_at = None
            item.completed_at = None
        self.db.commit()
        return failed

    def list_findings(self, run_id: str) -> list[ReviewFinding]:
        self.get_analysis_run(run_id)
        return list(
            self.db.scalars(
                select(ReviewFinding)
                .options(
                    selectinload(ReviewFinding.requirement_snapshot),
                    selectinload(ReviewFinding.evidence),
                )
                .where(ReviewFinding.analysis_run_id == run_id)
                .order_by(ReviewFinding.created_at, ReviewFinding.id)
            )
        )
