"""Evidence-first URS/ES-to-FS/DS candidate finding generation."""

from __future__ import annotations

from datetime import datetime, timezone
from time import sleep
from typing import Protocol

from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.domain.evidence import EvidenceChunk, RetrievalFilters
from app.domain.enums import CoverageStatus
from app.domain.models import AnalysisRun, AnalysisRunItem, FindingEvidence, ReviewFinding, ReviewPackage
from app.services.retrieval_service import RetrievalService


class CandidateJudgment(BaseModel):
    """Constrained LLM output; it can only cite IDs supplied by retrieval."""

    design_status: CoverageStatus
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    gap: str | None = None
    suggested_reviewer_action: str | None = None
    audit_points: list["AuditPointJudgment"] = Field(default_factory=list)


class AuditPoint(BaseModel):
    """One checkable condition, always traceable back to the original URS."""

    point_id: str = Field(min_length=1, max_length=40)
    source_excerpt: str = Field(min_length=1)
    review_point: str = Field(min_length=1)


class AuditPlan(BaseModel):
    audit_points: list[AuditPoint] = Field(min_length=1, max_length=6)


class AuditPointJudgment(AuditPoint):
    design_status: CoverageStatus
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)


class FindingJudge(Protocol):
    def judge(self, *, requirement_code: str, requirement_text: str, evidence: list[EvidenceChunk]) -> CandidateJudgment: ...


class OpenAIDesignFindingJudge:
    """Grounded English judgment adapter; never issues an approval decision."""

    def __init__(self, model: str):
        from langchain_openai import ChatOpenAI

        self._llm = ChatOpenAI(model=model, temperature=0).with_structured_output(CandidateJudgment)
        self._audit_planner = ChatOpenAI(model=model, temperature=0).with_structured_output(AuditPlan)

    def decompose(self, *, requirement_code: str, requirement_text: str) -> list[AuditPoint]:
        """Generate a small set of checkable points without rewriting the URS."""
        prompt = f"""You are preparing an advisory FS/DS coverage review for an engineer.

Original URS: {requirement_code}
{requirement_text}

Return one to six audit points. Each point must be a concrete condition that can
be checked against FS/DS evidence. Keep the original URS unchanged: for every
point, include the exact supporting phrase from it in source_excerpt. Do not
invent conditions, standards, or implementation details. Use one point when
the URS is already atomic. Return English only."""
        return self._audit_planner.invoke(prompt).audit_points

    def judge(
        self,
        *,
        requirement_code: str,
        requirement_text: str,
        evidence: list[EvidenceChunk],
        audit_points: list[AuditPoint] | None = None,
    ) -> CandidateJudgment:
        evidence_text = "\n\n".join(
            f"[chunk_id={item.chunk_id}]\n"
            f"{item.document_title} v{item.version} | {item.document_type} | "
            f"section={item.section or 'not stated'} | page={item.page or 'not stated'}\n"
            f"{item.content}"
            for item in evidence
        )
        audit_point_text = "\n".join(
            f"- {point.point_id}: source phrase={point.source_excerpt}; check={point.review_point}"
            for point in audit_points or []
        ) or "- p1: assess the original requirement as one atomic check."
        prompt = f"""You are assisting an engineer with a supplier design review.
You are not an approver and you must not make a compliance decision.

Requirement: {requirement_code}
{requirement_text}

Review only the supplied FS/DS evidence. Return an English candidate finding.
- covered: evidence explicitly addresses the requirement and key conditions.
- partially_covered: relevant capability is described but an important condition, failure mode, limit, or configuration detail is absent.
- not_evidenced: selected evidence does not establish a response.
- conflicting_evidence: evidence gives incompatible statements.
- not_assessable: requirement is too ambiguous to assess from the evidence.
- review_required: evidence needs engineering interpretation.

Rules:
1. Cite only chunk IDs present below.
2. If no explicit evidence exists, say "No explicit evidence was found in the selected review scope"; do not claim the supplier lacks the capability.
3. Do not use approved, rejected, compliant, non-compliant, or verified.
4. Return one audit_points judgment for every supplied audit point, retaining its point_id, source_excerpt, and review_point.
5. A point marked covered must cite at least one supplied chunk ID.
6. The overall status may be covered only when every audit point is covered with valid evidence. Otherwise use the most conservative applicable status.
7. Give a concise reviewer action when a gap or ambiguity remains.

Audit points:
{audit_point_text}

Evidence:
{evidence_text}"""
        return self._llm.invoke(prompt)


class CoverageAnalysisService:
    """Runs a bounded, deterministic coverage workflow for an existing package."""

    def __init__(self, db: Session, retrieval: RetrievalService, judge: FindingJudge):
        self.db = db
        self.retrieval = retrieval
        self.judge = judge

    def execute(self, analysis_run_id: str) -> AnalysisRun:
        """Synchronous compatibility helper used by tests and local scripts.

        HTTP traffic is dispatched through Redis instead. Every item still uses
        the same durable state transitions as a background worker.
        """
        run = self._get_run(analysis_run_id)
        if run.status == "completed":
            raise ValueError("Analysis run is already completed")
        for item in run.items:
            if item.status != "completed":
                self.execute_item(item.id, max_attempts=1, retry_delays_seconds=[])
        return self._get_run(analysis_run_id)

    def execute_item(
        self,
        analysis_run_item_id: str,
        *,
        max_attempts: int,
        retry_delays_seconds: list[int],
    ) -> AnalysisRun:
        """Evaluate one frozen URS item with durable retry state."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

        item = self._get_item(analysis_run_item_id)
        if item.status == "completed":
            return self._get_run(item.analysis_run_id)

        for local_attempt in range(max_attempts):
            item = self._get_item(analysis_run_item_id)
            item.status = "running"
            item.attempt_count += 1
            item.started_at = datetime.now(timezone.utc)
            item.error_message = None
            item.analysis_run.status = "running"
            item.analysis_run.error_message = None
            self.db.commit()
            try:
                self._evaluate_item(item)
                item = self._get_item(analysis_run_item_id)
                item.status = "completed"
                item.completed_at = datetime.now(timezone.utc)
                item.error_message = None
                self._update_run_status(item.analysis_run_id)
                self.db.commit()
                return self._get_run(item.analysis_run_id)
            except Exception as exc:
                self.db.rollback()
                item = self._get_item(analysis_run_item_id)
                can_retry = self._is_retryable(exc) and local_attempt + 1 < max_attempts
                item.error_message = str(exc)
                if can_retry:
                    item.status = "retrying"
                    self._update_run_status(item.analysis_run_id)
                    self.db.commit()
                    delay = (
                        retry_delays_seconds[min(local_attempt, len(retry_delays_seconds) - 1)]
                        if retry_delays_seconds
                        else 0
                    )
                    if delay > 0:
                        sleep(delay)
                    continue
                item.status = "failed"
                item.completed_at = datetime.now(timezone.utc)
                self._update_run_status(item.analysis_run_id)
                self.db.commit()
                # Keep other queued RQ jobs running and make the partial failure
                # visible to the UI instead of failing the entire worker process.
                return self._get_run(item.analysis_run_id)

        raise RuntimeError("Analysis item retry loop ended unexpectedly")

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, (LookupError, ValueError)):
            return False
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int):
            return status_code in {408, 429} or status_code >= 500
        return True

    def _evaluate_item(self, item: AnalysisRunItem) -> None:
        run = item.analysis_run
        review = run.review_package
        requirement = item.requirement_snapshot
        filters = RetrievalFilters(
            document_version_ids=[link.document_version_id for link in review.document_links],
            system=review.system,
            document_types=["FS", "DS"],
        )
        audit_points = self._audit_points(requirement.requirement_code, requirement.requirement_text)
        evidence = self._retrieve_audit_evidence(requirement.requirement_code, audit_points, filters)
        judgment = self._judge(
            requirement.requirement_code,
            requirement.requirement_text,
            audit_points,
            evidence,
        )
        self._delete_existing_finding(run.id, requirement.id)
        self._save_finding(run.id, requirement.id, judgment, evidence)

    def _audit_points(self, requirement_code: str, requirement_text: str) -> list[AuditPoint]:
        fallback = [
            AuditPoint(
                point_id="p1",
                source_excerpt=requirement_text,
                review_point=requirement_text,
            )
        ]
        decompose = getattr(self.judge, "decompose", None)
        if not callable(decompose):
            return fallback
        points = decompose(requirement_code=requirement_code, requirement_text=requirement_text)
        if not points:
            return fallback
        unique: list[AuditPoint] = []
        seen: set[str] = set()
        for index, point in enumerate(points[:6], start=1):
            point_id = point.point_id.strip() or f"p{index}"
            if point_id in seen:
                point_id = f"p{index}"
            seen.add(point_id)
            unique.append(point.model_copy(update={"point_id": point_id}))
        return unique or fallback

    def _retrieve_audit_evidence(
        self, requirement_code: str, audit_points: list[AuditPoint], filters: RetrievalFilters
    ) -> list[EvidenceChunk]:
        """Search each checkable condition, then retain a small deduplicated evidence set."""
        evidence_by_id: dict[str, EvidenceChunk] = {}
        for point in audit_points:
            query = f"{requirement_code}\n{point.review_point}"
            for chunk in self.retrieval.retrieve(query, filters, limit=6):
                evidence_by_id.setdefault(chunk.chunk_id, chunk)
        return list(evidence_by_id.values())

    def _judge(
        self,
        requirement_code: str,
        requirement_text: str,
        audit_points: list[AuditPoint],
        evidence: list[EvidenceChunk],
    ) -> CandidateJudgment:
        if not evidence:
            return CandidateJudgment(
                design_status=CoverageStatus.NOT_EVIDENCED,
                rationale="No explicit evidence was found in the selected review scope.",
                suggested_reviewer_action="Review the selected FS/DS manually or request a vendor design response.",
                audit_points=[
                    AuditPointJudgment(
                        **point.model_dump(),
                        design_status=CoverageStatus.NOT_EVIDENCED,
                        rationale="No explicit evidence was found in the selected review scope.",
                    )
                    for point in audit_points
                ],
            )
        try:
            judgment = self.judge.judge(
                requirement_code=requirement_code,
                requirement_text=requirement_text,
                evidence=evidence,
                audit_points=audit_points,
            )
        except TypeError:
            # Existing deterministic test doubles and third-party adapters can
            # keep the original protocol while the review flow gains audit points.
            judgment = self.judge.judge(
                requirement_code=requirement_code,
                requirement_text=requirement_text,
                evidence=evidence,
            )
        allowed_ids = {item.chunk_id for item in evidence}
        valid_ids = [item for item in judgment.evidence_chunk_ids if item in allowed_ids]
        point_judgments = self._normalise_audit_points(audit_points, judgment, allowed_ids)
        valid_ids = list(
            dict.fromkeys(
                valid_ids + [chunk_id for point in point_judgments for chunk_id in point.evidence_chunk_ids]
            )
        )
        if judgment.design_status in {
            CoverageStatus.COVERED,
            CoverageStatus.PARTIALLY_COVERED,
            CoverageStatus.CONFLICTING_EVIDENCE,
        } and not valid_ids:
            judgment = CandidateJudgment(
                design_status=CoverageStatus.REVIEW_REQUIRED,
                rationale="The generated assessment did not provide a valid citation from the selected evidence.",
                suggested_reviewer_action="Review the retrieved evidence manually.",
                audit_points=point_judgments,
            )
        if judgment.design_status == CoverageStatus.COVERED and not all(
            point.design_status == CoverageStatus.COVERED and point.evidence_chunk_ids
            for point in point_judgments
        ):
            judgment = judgment.model_copy(
                update={
                    "design_status": CoverageStatus.REVIEW_REQUIRED,
                    "rationale": "Not every checkable condition has sufficient cited evidence; engineering review is required.",
                    "suggested_reviewer_action": "Review the audit points and their cited FS/DS passages manually.",
                }
            )
        if judgment.design_status == CoverageStatus.NOT_EVIDENCED:
            valid_ids = []
        return judgment.model_copy(
            update={"evidence_chunk_ids": valid_ids, "audit_points": point_judgments}
        )

    @staticmethod
    def _normalise_audit_points(
        audit_points: list[AuditPoint], judgment: CandidateJudgment, allowed_ids: set[str]
    ) -> list[AuditPointJudgment]:
        received = {point.point_id: point for point in judgment.audit_points}
        result: list[AuditPointJudgment] = []
        for point in audit_points:
            value = received.get(point.point_id)
            if value is None and len(audit_points) == 1:
                value = AuditPointJudgment(
                    **point.model_dump(),
                    design_status=judgment.design_status,
                    evidence_chunk_ids=judgment.evidence_chunk_ids,
                    rationale=judgment.rationale,
                )
            if value is None:
                value = AuditPointJudgment(
                    **point.model_dump(),
                    design_status=CoverageStatus.REVIEW_REQUIRED,
                    rationale="The generated assessment did not return a judgment for this audit point.",
                )
            valid_ids = [chunk_id for chunk_id in value.evidence_chunk_ids if chunk_id in allowed_ids]
            if value.design_status == CoverageStatus.COVERED and not valid_ids:
                value = value.model_copy(
                    update={
                        "design_status": CoverageStatus.REVIEW_REQUIRED,
                        "rationale": "This audit point was marked covered without a valid citation.",
                        "evidence_chunk_ids": [],
                    }
                )
            else:
                value = value.model_copy(
                    update={
                        "source_excerpt": point.source_excerpt,
                        "review_point": point.review_point,
                        "evidence_chunk_ids": valid_ids,
                    }
                )
            result.append(value)
        return result

    def _save_finding(
        self,
        run_id: str,
        requirement_snapshot_id: str,
        judgment: CandidateJudgment,
        evidence: list[EvidenceChunk],
    ) -> None:
        selected = {item.chunk_id: item for item in evidence if item.chunk_id in judgment.evidence_chunk_ids}
        finding = ReviewFinding(
            analysis_run_id=run_id,
            requirement_snapshot_id=requirement_snapshot_id,
            design_status=judgment.design_status.value,
            rationale=judgment.rationale,
            gap=judgment.gap,
            suggested_reviewer_action=judgment.suggested_reviewer_action,
            audit_points=[point.model_dump(mode="json") for point in judgment.audit_points],
        )
        for item in selected.values():
            finding.evidence.append(
                FindingEvidence(
                    chunk_id=item.chunk_id,
                    document_version_id=item.document_version_id,
                    document_title=item.document_title,
                    version=item.version,
                    page=item.page,
                    section=item.section,
                    excerpt=item.content,
                )
            )
        self.db.add(finding)
        self.db.flush()

    def _delete_existing_finding(self, run_id: str, requirement_snapshot_id: str) -> None:
        finding = self.db.scalar(
            select(ReviewFinding).where(
                ReviewFinding.analysis_run_id == run_id,
                ReviewFinding.requirement_snapshot_id == requirement_snapshot_id,
            )
        )
        if finding is None:
            return
        self.db.execute(delete(FindingEvidence).where(FindingEvidence.finding_id == finding.id))
        self.db.delete(finding)
        self.db.flush()

    def _update_run_status(self, run_id: str) -> None:
        run = self.db.get(AnalysisRun, run_id)
        if run is None:
            raise LookupError("Analysis run not found")
        statuses = list(
            self.db.scalars(
                select(AnalysisRunItem.status).where(AnalysisRunItem.analysis_run_id == run_id)
            )
        )
        active = {"queued", "running", "retrying"}
        if any(status in active for status in statuses):
            run.status = "running" if any(status in {"running", "retrying"} for status in statuses) else "queued"
            run.completed_at = None
            return
        if any(status == "failed" for status in statuses):
            failed_count = sum(status == "failed" for status in statuses)
            run.status = "failed"
            run.error_message = f"{failed_count} analysis item(s) failed; retry failed items to continue."
        else:
            run.status = "completed"
            run.error_message = None
        run.completed_at = datetime.now(timezone.utc)

    def _get_run(self, run_id: str) -> AnalysisRun:
        statement = (
            select(AnalysisRun)
            .options(
                selectinload(AnalysisRun.review_package).selectinload(ReviewPackage.document_links),
                selectinload(AnalysisRun.review_package).selectinload(ReviewPackage.requirement_snapshots),
                selectinload(AnalysisRun.findings).selectinload(ReviewFinding.evidence),
                selectinload(AnalysisRun.items).selectinload(AnalysisRunItem.requirement_snapshot),
            )
            .where(AnalysisRun.id == run_id)
        )
        run = self.db.scalar(statement)
        if run is None:
            raise LookupError("Analysis run not found")
        return run

    def _get_item(self, item_id: str) -> AnalysisRunItem:
        statement = (
            select(AnalysisRunItem)
            .options(
                selectinload(AnalysisRunItem.requirement_snapshot),
                selectinload(AnalysisRunItem.analysis_run)
                .selectinload(AnalysisRun.review_package)
                .selectinload(ReviewPackage.document_links),
            )
            .where(AnalysisRunItem.id == item_id)
        )
        item = self.db.scalar(statement)
        if item is None:
            raise LookupError("Analysis run item not found")
        return item
