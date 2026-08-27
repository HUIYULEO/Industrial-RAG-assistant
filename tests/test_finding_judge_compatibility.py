"""A third-party judge may use either signature, but its own faults must surface."""

import pytest

from app.domain.enums import CoverageStatus
from app.domain.evidence import EvidenceChunk
from app.services.coverage_service import (
    AuditPoint,
    CandidateJudgment,
    CoverageAnalysisService,
    _accepts_audit_points,
)


EVIDENCE = [
    EvidenceChunk(
        chunk_id="chunk-1",
        document_version_id="version-1",
        document_title="Functional Specification",
        document_type="FS",
        version="1.0",
        page=4,
        section="3.2 Interfaces",
        content="The controller exposes an OPC UA interface.",
    )
]
AUDIT_POINTS = [
    AuditPoint(point_id="p1", source_excerpt="expose an interface", review_point="expose an interface")
]


def _judgment() -> CandidateJudgment:
    return CandidateJudgment(
        design_status=CoverageStatus.COVERED,
        evidence_chunk_ids=["chunk-1"],
        rationale="The interface is described in the cited passage.",
    )


class OriginalSignatureJudge:
    """A third-party adapter written against FindingJudge alone."""

    def __init__(self):
        self.received: dict | None = None

    def judge(self, *, requirement_code, requirement_text, evidence):
        self.received = {"evidence": evidence}
        return _judgment()


class AuditPointJudge:
    """An adapter that opted into the audit-point extension."""

    def __init__(self):
        self.received: dict | None = None

    def judge(self, *, requirement_code, requirement_text, evidence, audit_points):
        self.received = {"audit_points": audit_points}
        return _judgment()


class KeywordCatchAllJudge:
    def __init__(self):
        self.received: dict | None = None

    def judge(self, *, requirement_code, requirement_text, evidence, **kwargs):
        self.received = kwargs
        return _judgment()


class FaultyJudge:
    """An adapter whose own implementation is broken."""

    def judge(self, *, requirement_code, requirement_text, evidence, audit_points):
        raise TypeError("'NoneType' object is not subscriptable")


def _service(judge) -> CoverageAnalysisService:
    return CoverageAnalysisService(None, None, judge)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("judge", "expects_audit_points"),
    [
        (OriginalSignatureJudge(), False),
        (AuditPointJudge(), True),
        (KeywordCatchAllJudge(), True),
    ],
)
def test_the_audit_point_extension_is_detected_from_the_signature(judge, expects_audit_points):
    assert _accepts_audit_points(judge) is expects_audit_points


def test_an_original_signature_adapter_is_called_without_audit_points():
    judge = OriginalSignatureJudge()

    _service(judge)._judge("URS-001", "Expose an interface", AUDIT_POINTS, EVIDENCE)

    assert judge.received == {"evidence": EVIDENCE}


def test_an_extended_adapter_receives_the_audit_points():
    judge = AuditPointJudge()

    _service(judge)._judge("URS-001", "Expose an interface", AUDIT_POINTS, EVIDENCE)

    assert judge.received == {"audit_points": AUDIT_POINTS}


def test_a_fault_inside_the_adapter_is_not_mistaken_for_an_unsupported_keyword():
    """A probe-and-retry compatibility shim would swallow this and call again."""
    with pytest.raises(TypeError, match="not subscriptable"):
        _service(FaultyJudge())._judge("URS-001", "Expose an interface", AUDIT_POINTS, EVIDENCE)
