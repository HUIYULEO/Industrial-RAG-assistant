"""Predeployment invariants; SQLite tests do not prove PostgreSQL lock behaviour."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.domain.models import Base, Document, DocumentVersion, DocumentChunk, RequirementBaseline, Requirement
from app.services.ingestion_service import DocumentIngestionService
from app.services.review_service import ReviewService
from app.services.visual_evidence_service import VisualEvidenceService


@pytest.fixture
def context(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'review.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        version = DocumentVersion(document=Document(title="FS", document_type="FS", system="wcs"), version="1", status="draft")
        baseline = RequirementBaseline(name="URS", system="wcs")
        db.add_all([version, baseline])
        db.flush()
        db.add(Requirement(baseline_id=baseline.id, requirement_code="URS-1", requirement_text="Retain records"))
        db.commit()
        yield db, version, baseline, DocumentIngestionService(db, tmp_path)
    engine.dispose()


def freeze(db, version, baseline, system="wcs"):
    return ReviewService(db, owner_user_id="engineer", organization_id="org").create_review_package(
        name="Review", system=system, requirement_baseline_id=baseline.id,
        design_document_version_ids=[version.id],
    )


@pytest.mark.parametrize("status", ["registered", "parsing", "parsed_pending_index", "index_queued", "indexing", "index_failed"])
def test_only_indexed_sources_can_be_frozen(context, status):
    db, version, baseline, _ = context
    version.ingestion_status = status
    db.commit()
    with pytest.raises(ValueError, match="must be indexed"):
        freeze(db, version, baseline)


def test_system_boundaries_are_checked(context):
    db, version, baseline, _ = context
    version.ingestion_status = "indexed"
    db.commit()
    with pytest.raises(ValueError, match="baseline system"):
        freeze(db, version, baseline, system="other")
    version.document.system = "other"
    db.commit()
    with pytest.raises(ValueError, match="match the review system"):
        freeze(db, version, baseline)


def test_duplicate_parse_and_visual_write_cannot_change_frozen_evidence(context, tmp_path):
    db, version, baseline, ingestion = context
    ingestion.upload_and_parse(version.id, "fs.csv", b"name,value\nretention,90 days\n")
    chunk_ids = list(db.scalars(select(DocumentChunk.id)))
    version.ingestion_status = "indexed"
    db.commit()
    freeze(db, version, baseline)
    ingestion.parse_staged_document(version.id, version.parse_dispatch_version)
    assert list(db.scalars(select(DocumentChunk.id))) == chunk_ids
    with pytest.raises(ValueError, match="frozen"):
        VisualEvidenceService(db, tmp_path).extract_pdf_candidates(version.id, tmp_path / "missing.pdf")


@pytest.mark.parametrize("running", [False, True])
def test_lost_parse_becomes_retryable_and_old_delivery_is_fenced(context, running):
    db, version, _, ingestion = context
    ingestion.stage_upload(version.id, "fs.csv", b"name,value\nretention,90 days\n")
    old_generation = version.parse_dispatch_version
    version.parse_started_at = datetime.now(timezone.utc) - timedelta(hours=2)
    version.parse_owner = "dead-worker" if running else None
    db.commit()
    assert ingestion.recover_expired_parses(3600) == 1
    assert version.ingestion_status == "failed"
    ingestion.stage_reparse(version.id)
    generation = version.parse_dispatch_version
    ingestion.parse_staged_document(version.id, old_generation)
    ingestion.mark_staged_parse_failed(version.id, "late enqueue error", old_generation)
    assert version.ingestion_status == "parsing"
    assert version.parse_dispatch_version == generation
    ingestion.parse_staged_document(version.id, generation)
    assert version.ingestion_status == "parsed_pending_index"
    assert version.chunk_count == 1


def test_duplicate_delivery_during_parse_does_not_execute_parser_twice(context, monkeypatch):
    db, version, _, ingestion = context
    ingestion.stage_upload(version.id, "fs.csv", b"name,value\nretention,90 days\n")
    parser = ingestion._parse_source
    calls = []

    def parse(path, suffix):
        calls.append(path)
        ingestion.parse_staged_document(version.id, version.parse_dispatch_version)
        return parser(path, suffix)

    monkeypatch.setattr(ingestion, "_parse_source", parse)
    ingestion.parse_staged_document(version.id, version.parse_dispatch_version)
    assert len(calls) == 1
    assert version.ingestion_status == "parsed_pending_index"


def test_missing_source_records_failure(context):
    db, version, _, ingestion = context
    version.ingestion_status = "parsing"
    db.commit()
    with pytest.raises(ValueError):
        ingestion.parse_staged_document(version.id, version.parse_dispatch_version)
    assert version.ingestion_status == "failed"
    assert version.parse_owner is None


def test_worker_finishing_after_timeout_cannot_publish_chunks(context, monkeypatch):
    db, version, _, ingestion = context
    ingestion.stage_upload(version.id, "fs.csv", b"name,value\nretention,90 days\n")
    parser = ingestion._parse_source

    def parse(path, suffix):
        result = parser(path, suffix)
        version.parse_started_at = datetime.now(timezone.utc) - timedelta(hours=2)
        db.commit()
        ingestion.recover_expired_parses(3600)
        ingestion.stage_reparse(version.id)
        return result

    monkeypatch.setattr(ingestion, "_parse_source", parse)
    ingestion.parse_staged_document(version.id, version.parse_dispatch_version)
    assert version.ingestion_status == "parsing"
    assert not list(db.scalars(select(DocumentChunk.id)))


def test_existing_review_with_unready_source_cannot_start_analysis(context):
    db, version, baseline, _ = context
    version.ingestion_status = "indexed"
    db.commit()
    review = freeze(db, version, baseline)
    version.ingestion_status = "index_failed"
    db.commit()
    service = ReviewService(db, owner_user_id="engineer", organization_id="org")
    with pytest.raises(ValueError, match="indexed"):
        service.create_analysis_run(review.id)
