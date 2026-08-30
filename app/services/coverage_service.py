"""Evidence-first URS/ES-to-design-specification candidate finding generation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from inspect import Parameter, signature
from time import perf_counter, sleep
from typing import Any, Protocol

from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from app.domain.analysis import apply_run_status
from app.domain.evidence import EvidenceChunk, RetrievalFilters
from app.domain.enums import DESIGN_DOCUMENT_TYPES, CoverageStatus
from app.domain.models import (
    AnalysisAttempt,
    AnalysisRun,
    AnalysisRunItem,
    FindingEvidence,
    ReviewFinding,
    ReviewPackage,
)
from app.services.analysis_reliability_service import renew_analysis_lease
from app.services.retrieval_service import RetrievalService


class LeaseLostError(RuntimeError):
    """Raised when another worker or the watchdog owns the analysis item."""


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
    audit_points: list[AuditPoint] = Field(min_length=1, max_length=3)


class AuditPointJudgment(AuditPoint):
    design_status: CoverageStatus
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)


class FindingJudge(Protocol):
    """Minimum contract a third-party design-finding judge must satisfy.

    An implementation may additionally accept ``audit_points`` in ``judge`` and
    expose ``decompose`` (see :class:`AuditPointPlanner`).  Both are detected
    per implementation, so an adapter written against this signature alone keeps
    working.
    """

    def judge(self, *, requirement_code: str, requirement_text: str, evidence: list[EvidenceChunk]) -> CandidateJudgment: ...


class AuditPointPlanner(Protocol):
    """Optional capability: split one URS into separately checkable points."""

    def decompose(self, *, requirement_code: str, requirement_text: str) -> list[AuditPoint]: ...


def _accepts_audit_points(judge: FindingJudge) -> bool:
    """Report whether this judge's ``judge`` takes the ``audit_points`` extension.

    The signature is inspected rather than probed by calling and catching
    ``TypeError``: a probe cannot tell an unsupported keyword apart from a
    ``TypeError`` raised inside the implementation, and silently swallowing the
    latter hides real adapter faults behind a second, weaker call.
    """
    try:
        parameters = signature(judge.judge).parameters
    except (TypeError, ValueError):  # C-implemented or otherwise opaque callables
        return True
    if "audit_points" in parameters:
        return True
    return any(item.kind is Parameter.VAR_KEYWORD for item in parameters.values())


class ConfiguredDesignFindingJudge:
    """Grounded judgment adapter; never issues an approval decision."""

    def __init__(self, model: Any):
        self._llm = model.with_structured_output(CandidateJudgment)
        self._audit_planner = model.with_structured_output(AuditPlan)
        self.model_info = {
            "provider_adapter": type(model).__name__,
            "model": getattr(model, "model_name", None) or getattr(model, "model", None),
        }

    def decompose(self, *, requirement_code: str, requirement_text: str) -> list[AuditPoint]:
        """Generate a small set of checkable points without rewriting the URS."""
        prompt = f"""You are preparing an advisory design-specification coverage review for an engineer.

Original URS: {requirement_code}
{requirement_text}

Return one to three unique audit points. Use exactly one point when the URS is
already atomic. Use two points only for two independent obligations under the
same actor. A compound URS covering safety, status reporting, and records may
use three points, never more. When more conditions exist, combine conditions
that need the same evidence instead of truncating them.

Every point must retain the original actor, action, object, and any condition or
limit that qualifies that action. Never split a qualifier into a standalone
requirement. source_excerpt must be an exact, contiguous excerpt copied from the
Original URS. Do not invent standards, functions, responsibilities, translations,
or paraphrased duplicates. Return English only."""
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

Review only the supplied design-specification evidence. Return an English candidate finding.
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
        worker_id: str | None = None,
        lease_seconds: int = 360,
        expected_dispatch_version: int | None = None,
    ) -> AnalysisRun:
        """Evaluate one frozen URS item after atomically acquiring its lease."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        worker_id = worker_id or f"inline:{analysis_run_item_id}"

        while True:
            claim = self._claim_attempt(
                analysis_run_item_id,
                worker_id=worker_id,
                max_attempts=max_attempts,
                lease_seconds=lease_seconds,
                expected_dispatch_version=expected_dispatch_version,
            )
            if claim is None:
                item = self._get_item(analysis_run_item_id)
                return self._get_run(item.analysis_run_id)
            run_id, cycle_attempt_number, attempt_number = claim
            try:
                item = self._get_item(analysis_run_item_id)
                self._evaluate_item(item, worker_id=worker_id, lease_seconds=lease_seconds)
                now = datetime.now(timezone.utc)
                result = self.db.execute(
                    update(AnalysisRunItem)
                    .where(
                        AnalysisRunItem.id == analysis_run_item_id,
                        AnalysisRunItem.status == "running",
                        AnalysisRunItem.lease_owner == worker_id,
                    )
                    .values(
                        status="completed",
                        completed_at=now,
                        error_message=None,
                        lease_owner=None,
                        lease_expires_at=None,
                        heartbeat_at=None,
                    )
                )
                if not result.rowcount:
                    self.db.rollback()
                    return self._get_run(run_id)
                self._finish_attempt(
                    analysis_run_item_id,
                    attempt_number,
                    status="completed",
                    completed_at=now,
                )
                self._update_run_status(run_id)
                self.db.commit()
                return self._get_run(run_id)
            except LeaseLostError:
                self.db.rollback()
                return self._get_run(run_id)
            except Exception as exc:
                self.db.rollback()
                can_retry = self._is_retryable(exc) and cycle_attempt_number < max_attempts
                now = datetime.now(timezone.utc)
                self._finish_attempt(
                    analysis_run_item_id,
                    attempt_number,
                    status="failed",
                    completed_at=now,
                    error_class=exc.__class__.__name__,
                    error_message=str(exc),
                )
                if can_retry:
                    self.db.execute(
                        update(AnalysisRunItem)
                        .where(
                            AnalysisRunItem.id == analysis_run_item_id,
                            AnalysisRunItem.status == "running",
                            AnalysisRunItem.lease_owner == worker_id,
                        )
                        .values(
                            error_message=str(exc),
                            heartbeat_at=now,
                            lease_expires_at=now + timedelta(seconds=lease_seconds),
                        )
                    )
                    self.db.commit()
                    delay = (
                        retry_delays_seconds[
                            min(cycle_attempt_number - 1, len(retry_delays_seconds) - 1)
                        ]
                        if retry_delays_seconds
                        else 0
                    )
                    if delay > 0:
                        sleep(delay)
                    continue
                self.db.execute(
                    update(AnalysisRunItem)
                    .where(
                        AnalysisRunItem.id == analysis_run_item_id,
                        AnalysisRunItem.status == "running",
                        AnalysisRunItem.lease_owner == worker_id,
                    )
                    .values(
                        status="failed",
                        error_message=str(exc),
                        completed_at=now,
                        lease_owner=None,
                        lease_expires_at=None,
                        heartbeat_at=None,
                    )
                )
                self._update_run_status(run_id)
                self.db.commit()
                return self._get_run(run_id)

    def _claim_attempt(
        self,
        item_id: str,
        *,
        worker_id: str,
        max_attempts: int,
        lease_seconds: int,
        expected_dispatch_version: int | None,
    ) -> tuple[str, int, int] | None:
        now = datetime.now(timezone.utc)
        ownership = or_(
            AnalysisRunItem.status.in_(["queued", "retrying"]),
            (AnalysisRunItem.status == "running") & (AnalysisRunItem.lease_owner == worker_id),
            (AnalysisRunItem.status == "running") & (AnalysisRunItem.lease_expires_at < now),
        )
        conditions = [
            AnalysisRunItem.id == item_id,
            AnalysisRunItem.attempt_count < max_attempts,
            ownership,
        ]
        if expected_dispatch_version is not None:
            conditions.append(AnalysisRunItem.dispatch_version == expected_dispatch_version)
        result = self.db.execute(
            update(AnalysisRunItem)
            .where(*conditions)
            .values(
                status="running",
                attempt_count=AnalysisRunItem.attempt_count + 1,
                started_at=now,
                completed_at=None,
                error_message=None,
                lease_owner=worker_id,
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )
            .returning(AnalysisRunItem.analysis_run_id, AnalysisRunItem.attempt_count)
        )
        row = result.one_or_none()
        if row is None:
            self.db.rollback()
            return None
        run_id, cycle_attempt_number = row
        previous_attempt_number = self.db.scalar(
            select(func.max(AnalysisAttempt.attempt_number)).where(
                AnalysisAttempt.analysis_run_item_id == item_id
            )
        )
        attempt_number = (previous_attempt_number or 0) + 1
        self.db.add(
            AnalysisAttempt(
                analysis_run_item_id=item_id,
                attempt_number=attempt_number,
                worker_id=worker_id,
                status="running",
            )
        )
        self.db.execute(
            update(AnalysisRun)
            .where(AnalysisRun.id == run_id)
            .values(status="running", error_message=None, completed_at=None)
        )
        self.db.commit()
        return run_id, cycle_attempt_number, attempt_number

    def _finish_attempt(
        self,
        item_id: str,
        attempt_number: int,
        *,
        status: str,
        completed_at: datetime,
        error_class: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.db.execute(
            update(AnalysisAttempt)
            .where(
                AnalysisAttempt.analysis_run_item_id == item_id,
                AnalysisAttempt.attempt_number == attempt_number,
                AnalysisAttempt.status == "running",
            )
            .values(
                status=status,
                completed_at=completed_at,
                error_class=error_class,
                error_message=error_message,
            )
        )

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, (LookupError, ValueError)):
            return False
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int):
            return status_code in {408, 429} or status_code >= 500
        return True

    def _evaluate_item(self, item: AnalysisRunItem, *, worker_id: str, lease_seconds: int) -> None:
        started_at = perf_counter()
        run = item.analysis_run
        review = run.review_package
        requirement = item.requirement_snapshot
        filters = RetrievalFilters(
            document_version_ids=[link.document_version_id for link in review.document_links],
            system=review.system,
            document_types=sorted(DESIGN_DOCUMENT_TYPES),
        )
        if run.strategy == "original":
            audit_points = self._original_audit_point(requirement.requirement_text)
        else:
            audit_points = self._audit_points(requirement.requirement_code, requirement.requirement_text)
        self._renew_lease(item.id, worker_id, lease_seconds)
        evidence, retrieval_trace = self._retrieve_evidence(
            strategy=run.strategy,
            requirement_code=requirement.requirement_code,
            requirement_text=requirement.requirement_text,
            audit_points=audit_points,
            filters=filters,
        )
        self._renew_lease(item.id, worker_id, lease_seconds)
        judgment = self._judge(
            requirement.requirement_code,
            requirement.requirement_text,
            audit_points,
            evidence,
        )
        self._renew_lease(item.id, worker_id, lease_seconds)
        owned = self.db.scalar(
            select(AnalysisRunItem)
            .where(
                AnalysisRunItem.id == item.id,
                AnalysisRunItem.status == "running",
                AnalysisRunItem.lease_owner == worker_id,
            )
            .with_for_update()
        )
        if owned is None:
            raise LeaseLostError("The analysis item lease was transferred to another worker")
        owned.analysis_trace = {
            "strategy": run.strategy,
            "strategy_version": run.strategy_version,
            "retrieval": retrieval_trace,
            "judge_input_chunk_ids": [chunk.chunk_id for chunk in evidence],
            "judge_evidence_chunk_ids": judgment.evidence_chunk_ids,
            "retrieval_adapter": type(self.retrieval).__name__,
            "judge_adapter": type(self.judge).__name__,
            "model_info": getattr(
                self.judge,
                "model_info",
                {"provider_adapter": type(self.judge).__name__, "model": None},
            ),
            "duration_ms": round((perf_counter() - started_at) * 1000, 3),
        }
        self._delete_existing_finding(run.id, requirement.id)
        self._save_finding(run.id, requirement.id, judgment, evidence)

    def _renew_lease(self, item_id: str, worker_id: str, lease_seconds: int) -> None:
        """Renew the lease mid-evaluation; a lost lease aborts this attempt."""
        if not renew_analysis_lease(self.db, item_id, worker_id, lease_seconds):
            self.db.rollback()
            raise LeaseLostError("The analysis item lease is no longer owned by this worker")
        self.db.commit()

    def _audit_points(self, requirement_code: str, requirement_text: str) -> list[AuditPoint]:
        fallback = [
            AuditPoint(
                point_id="p1",
                source_excerpt=requirement_text,
                review_point=requirement_text,
            )
        ]
        # decompose is an optional capability; a third-party judge that only
        # implements FindingJudge assesses the requirement as one atomic point.
        decompose = getattr(self.judge, "decompose", None)
        if not callable(decompose):
            return fallback
        points = decompose(requirement_code=requirement_code, requirement_text=requirement_text)
        if not points:
            return fallback
        unique: list[AuditPoint] = []
        seen_ids: set[str] = set()
        seen_content: set[tuple[str, str]] = set()
        for index, point in enumerate(points, start=1):
            source_excerpt = point.source_excerpt.strip()
            review_point = point.review_point.strip()
            if source_excerpt not in requirement_text:
                continue
            content_key = (source_excerpt.casefold(), " ".join(review_point.casefold().split()))
            if content_key in seen_content:
                continue
            point_id = point.point_id.strip() or f"p{index}"
            if point_id in seen_ids:
                point_id = f"p{index}"
            seen_ids.add(point_id)
            seen_content.add(content_key)
            unique.append(
                point.model_copy(
                    update={
                        "point_id": point_id,
                        "source_excerpt": source_excerpt,
                        "review_point": review_point,
                    }
                )
            )
            if len(unique) == 3:
                break
        return unique or fallback

    @staticmethod
    def _original_audit_point(requirement_text: str) -> list[AuditPoint]:
        return [
            AuditPoint(point_id="p1", source_excerpt=requirement_text, review_point=requirement_text)
        ]

    def _retrieve_evidence(
        self,
        *,
        strategy: str,
        requirement_code: str,
        requirement_text: str,
        audit_points: list[AuditPoint],
        filters: RetrievalFilters,
    ) -> tuple[list[EvidenceChunk], dict[str, Any]]:
        """Execute the selected query policy and retain ranked-query provenance."""
        if strategy == "original":
            queries = [{"audit_point_id": None, "query": requirement_text}]
        elif strategy == "decomposed":
            queries = [
                {
                    "audit_point_id": point.point_id,
                    "query": f"{requirement_code}\n{point.review_point}",
                }
                for point in audit_points
            ]
        else:
            raise ValueError(f"Unsupported analysis strategy: {strategy}")

        evidence_by_id: dict[str, EvidenceChunk] = {}
        query_traces: list[dict[str, Any]] = []
        for query_spec in queries:
            ranked = self.retrieval.retrieve(query_spec["query"], filters, limit=6)
            query_traces.append(
                {
                    **query_spec,
                    "limit": 6,
                    "ranked_chunk_ids": [chunk.chunk_id for chunk in ranked],
                }
            )
            for chunk in ranked:
                evidence_by_id.setdefault(chunk.chunk_id, chunk)
        trace = {
            "query_policy": (
                "original_urs_byte_for_byte" if strategy == "original" else "requirement_code_plus_audit_point"
            ),
            "filters": {
                "document_version_ids": list(filters.document_version_ids),
                "system": filters.system,
                "document_types": list(filters.document_types),
            },
            "queries": query_traces,
            "merged_ranked_chunk_ids": list(evidence_by_id),
        }
        return list(evidence_by_id.values()), trace

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
                suggested_reviewer_action="Review the selected design specifications manually or request a vendor design response.",
                audit_points=[
                    AuditPointJudgment(
                        **point.model_dump(),
                        design_status=CoverageStatus.NOT_EVIDENCED,
                        rationale="No explicit evidence was found in the selected review scope.",
                    )
                    for point in audit_points
                ],
            )
        extras = {"audit_points": audit_points} if _accepts_audit_points(self.judge) else {}
        judgment = self.judge.judge(
            requirement_code=requirement_code,
            requirement_text=requirement_text,
            evidence=evidence,
            **extras,
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
                    "suggested_reviewer_action": "Review the audit points and their cited design-specification passages manually.",
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
        apply_run_status(run, statuses, datetime.now(timezone.utc))

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
