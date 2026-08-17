"""Closed vocabularies used by the review workflow."""

from enum import Enum


class DocumentType(str, Enum):
    URS = "URS"
    ES = "ES"
    FS = "FS"
    DS = "DS"
    FAT_PROTOCOL = "FAT_PROTOCOL"
    FAT_REPORT = "FAT_REPORT"
    TECHNICAL_MANUAL = "TECHNICAL_MANUAL"
    INTEGRATION_GUIDE = "INTEGRATION_GUIDE"


class DocumentStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class CoverageStatus(str, Enum):
    COVERED = "covered"
    PARTIALLY_COVERED = "partially_covered"
    NOT_EVIDENCED = "not_evidenced"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    NOT_ASSESSABLE = "not_assessable"
    REVIEW_REQUIRED = "review_required"


# These definitions are deliberately review-oriented.  They explain what a
# candidate status means without turning it into an approval or compliance
# decision.
COVERAGE_STATUS_DEFINITIONS: dict[CoverageStatus, str] = {
    CoverageStatus.COVERED: "All key conditions have sufficient, locatable FS/DS evidence. Final confirmation remains with the engineer.",
    CoverageStatus.PARTIALLY_COVERED: "FS/DS describes related capability, but at least one key condition, constraint, or exception is not sufficiently evidenced.",
    CoverageStatus.NOT_EVIDENCED: "No sufficient FS/DS evidence was found; human review is required. This does not mean the supplier has not implemented the capability.",
    CoverageStatus.CONFLICTING_EVIDENCE: "The selected FS/DS evidence is inconsistent and requires engineering judgement.",
    CoverageStatus.NOT_ASSESSABLE: "The URS wording or available evidence is insufficient for a reliable coverage assessment; human clarification is required.",
    CoverageStatus.REVIEW_REQUIRED: "The evidence or model output needs engineering interpretation and cannot support a Covered conclusion.",
}


def coverage_status_definition(value: CoverageStatus | str) -> str:
    """Return the user-facing meaning for a persisted coverage status."""
    try:
        return COVERAGE_STATUS_DEFINITIONS[CoverageStatus(value)]
    except ValueError:
        return "The assessment status is unrecognised; human review is required."
