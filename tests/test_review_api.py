"""Tests for the local, version-aware Design Review API foundation."""

from pathlib import Path
from io import BytesIO

import pytest
from docx import Document as WordDocument
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.domain.evidence import EvidenceChunk
from app.domain.enums import CoverageStatus
from app.api.auth import require_authenticated_user
from app.repositories import database
from app.services.auth_service import AuthenticatedUser
from app.services.coverage_service import (
    AuditPoint,
    AuditPointJudgment,
    CandidateJudgment,
    CoverageAnalysisService,
)
from app.services.visual_evidence_service import VisualAnalysis, VisualEvidenceService


@pytest.fixture
def review_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database.get_engine.cache_clear()
    database.get_session_factory.cache_clear()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'review.db'}")
    monkeypatch.setenv("ANALYSIS_QUEUE_BACKEND", "memory")
    database.initialise_database()
    from app.main import app
    app.dependency_overrides[require_authenticated_user] = lambda: AuthenticatedUser(
        id="engineer-1",
        organization_id="organization-1",
        email="test.engineer@example.com",
        display_name="Test Engineer",
        role="engineer",
    )
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(require_authenticated_user, None)
    database.get_engine.cache_clear()
    database.get_session_factory.cache_clear()


def create_design_document(client: TestClient, *, document_type: str = "FS", version: str = "1.0") -> str:
    response = client.post(
        "/documents",
        json={
            "title": "Fleet Manager Functional Specification",
            "document_type": document_type,
            "system": "fleet_manager_wcs",
            "vendor": "Demo Vendor",
            "version": version,
            "status": "draft",
            "file_name": "fleet_manager_fs_v1.0.pdf",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_import_requirements_and_create_review_package(review_client: TestClient):
    baseline = review_client.post(
        "/requirement-baselines",
        json={"name": "Pseudo URS v1.0", "system": "fleet_manager_wcs"},
    )
    assert baseline.status_code == 201
    baseline_id = baseline.json()["id"]

    requirements = review_client.post(
        f"/requirement-baselines/{baseline_id}/requirements/import",
        files={
            "file": (
                "urs.csv",
                b"requirement_code,requirement_text,priority,category\n"
                b"AGV-URS-001,The Fleet Manager shall reserve exclusive zones.,High,Traffic management\n"
                b"AGV-URS-002,The system shall provide an audit history.,High,Auditability\n",
                "text/csv",
            )
        },
    )
    assert requirements.status_code == 200
    assert requirements.json()["imported_count"] == 2

    version_id = create_design_document(review_client)
    review = review_client.post(
        "/review-packages",
        json={
            "name": "DR-001 Fleet Manager Review",
            "system": "fleet_manager_wcs",
            "requirement_baseline_id": baseline_id,
            "design_document_version_ids": [version_id],
        },
    )
    assert review.status_code == 201
    assert review.json()["design_document_version_ids"] == [version_id]
    assert review.json()["requirement_count"] == 2

    analysis = review_client.post(f"/review-packages/{review.json()['id']}/analyses")
    assert analysis.status_code == 202
    assert analysis.json()["status"] == "queued"

    resumed_runs = review_client.get(f"/review-packages/{review.json()['id']}/analyses")
    assert resumed_runs.status_code == 200
    assert [run["id"] for run in resumed_runs.json()] == [analysis.json()["id"]]


def test_review_packages_are_private_to_the_owner_within_an_organization(review_client: TestClient):
    baseline = review_client.post(
        "/requirement-baselines", json={"name": "Private URS", "system": "fleet_manager_wcs"}
    ).json()
    review_client.post(
        f"/requirement-baselines/{baseline['id']}/requirements/import",
        files={"file": ("urs.csv", b"requirement_code,requirement_text\nURS-001,Private requirement\n", "text/csv")},
    )
    package = review_client.post(
        "/review-packages",
        json={
            "name": "Private review",
            "system": "fleet_manager_wcs",
            "requirement_baseline_id": baseline["id"],
            "design_document_version_ids": [create_design_document(review_client)],
        },
    ).json()

    from app.main import app

    app.dependency_overrides[require_authenticated_user] = lambda: AuthenticatedUser(
        id="engineer-2",
        organization_id="organization-1",
        email="other.engineer@example.com",
        display_name="Other Engineer",
        role="engineer",
    )
    assert review_client.get("/review-packages").json() == []
    assert review_client.get(f"/review-packages/{package['id']}").status_code == 404


def test_import_urs_table_creates_traceable_baseline_without_manual_setup(review_client: TestClient):
    response = review_client.post(
        "/requirement-baselines/import",
        data={"name": "AGV Fleet Manager URS v1.0"},
        files={
            "file": (
                "agv_fleet_manager_urs_v1.0.csv",
                "序号,系统,requirement,reasonal/impact,是否critical\n"
                "1,Fleet Manager,The system shall prevent dispatch to unavailable AGVs.,Patient safety and operational continuity,是\n"
                "2,Fleet Manager,The system shall retain task status history.,Deviation investigation support,否\n".encode("utf-8"),
                "text/csv",
            )
        },
    )

    assert response.status_code == 201
    imported = response.json()
    assert imported["baseline"]["name"] == "AGV Fleet Manager URS v1.0"
    assert imported["baseline"]["system"] == "Fleet Manager"
    assert imported["imported_count"] == 2
    assert imported["requirements"][0] == {
        "id": imported["requirements"][0]["id"],
        "requirement_code": "URS-001",
        "requirement_text": "The system shall prevent dispatch to unavailable AGVs.",
        "source_row": "1",
        "requirement_system": "Fleet Manager",
        "rationale_impact": "Patient safety and operational continuity",
        "is_critical": True,
        "priority": None,
        "category": None,
        "source_section": None,
    }


def test_review_package_rejects_non_design_document(review_client: TestClient):
    baseline = review_client.post(
        "/requirement-baselines",
        json={"name": "Pseudo URS v1.1", "system": "fleet_manager_wcs"},
    ).json()
    review_client.post(
        f"/requirement-baselines/{baseline['id']}/requirements/import",
        files={
            "file": (
                "urs.csv",
                b"requirement_code,requirement_text\n"
                b"AGV-URS-001,The Fleet Manager shall reserve exclusive zones.\n",
                "text/csv",
            )
        },
    )
    version_id = create_design_document(review_client, document_type="TECHNICAL_MANUAL")

    response = review_client.post(
        "/review-packages",
        json={
            "name": "DR-invalid",
            "system": "fleet_manager_wcs",
            "requirement_baseline_id": baseline["id"],
            "design_document_version_ids": [version_id],
        },
    )
    assert response.status_code == 400
    assert "FS and DS" in response.json()["detail"]


def test_document_replacement_preserves_logical_document(review_client: TestClient):
    v1_id = create_design_document(review_client)
    response = review_client.post(
        "/documents",
        json={
            "title": "Fleet Manager Functional Specification",
            "document_type": "FS",
            "system": "fleet_manager_wcs",
            "vendor": "Demo Vendor",
            "version": "1.1",
            "status": "draft",
            "file_name": "fleet_manager_fs_v1.1.pdf",
            "supersedes_version_id": v1_id,
        },
    )
    assert response.status_code == 201
    v2 = response.json()
    original = review_client.get("/documents").json()
    v1 = next(item for item in original if item["id"] == v1_id)
    assert v2["document_id"] == v1["document_id"]
    assert v1["status"] == "superseded"


def test_pdf_upload_rejects_non_extractable_document(review_client: TestClient, tmp_path: Path):
    version_id = create_design_document(review_client)
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    pdf_path = tmp_path / "empty.pdf"
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    response = review_client.post(
        f"/documents/{version_id}/upload",
        files={"file": ("empty.pdf", pdf_path.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 400
    assert "No extractable text" in response.json()["detail"]


def test_pdf_visual_evidence_is_rendered_before_explicit_analysis(
    review_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("ENABLE_VISUAL_ANALYSIS", "false")
    import fitz

    version_id = create_design_document(review_client)
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "5.1 Interface Diagram\nWCS -> Fleet Manager -> AGV")
    page.draw_rect(fitz.Rect(70, 100, 300, 170))
    source_pdf = document.tobytes()
    document.close()

    upload = review_client.post(
        f"/documents/{version_id}/upload",
        files={"file": ("fleet_manager_interface.pdf", source_pdf, "application/pdf")},
    )
    assert upload.status_code == 200

    figures = review_client.get(f"/documents/{version_id}/figures")
    assert figures.status_code == 200
    assert len(figures.json()) == 1
    figure = figures.json()[0]
    assert figure["page"] == 1
    assert figure["analysis_status"] == "extracted"
    assert figure["candidate_description"] is None

    asset = review_client.get(f"/documents/{version_id}/figures/{figure['id']}/asset")
    assert asset.status_code == 200
    assert asset.headers["content-type"] == "image/png"

    disabled = review_client.post(f"/documents/{version_id}/figures/analyse")
    assert disabled.status_code == 403
    assert "disabled" in disabled.json()["detail"]

    class FakeVisualInterpreter:
        def analyse(self, *, image_path: Path, page: int, section: str | None) -> VisualAnalysis:
            assert image_path.is_file()
            assert page == 1
            return VisualAnalysis(
                diagram_type="interface data flow",
                visible_labels=["WCS", "Fleet Manager", "AGV"],
                candidate_description="The diagram visibly shows an interface sequence between WCS, Fleet Manager, and AGV.",
                candidate_relationships=["WCS → Fleet Manager", "Fleet Manager → AGV"],
                limitations="Arrow semantics require engineering review.",
            )

    db = database.get_session_factory()()
    try:
        analysed = VisualEvidenceService(db, Path("data")).analyse_figures(version_id, FakeVisualInterpreter())
        assert analysed[0].analysis_status == "analysed"
        assert analysed[0].citation_chunk_id is not None
    finally:
        db.close()

    chunks = review_client.get(f"/documents/{version_id}/chunks").json()
    visual_chunk = next(item for item in chunks if item["id"] == analysed[0].citation_chunk_id)
    assert visual_chunk["page"] == 1
    assert "Candidate visual interpretation" in visual_chunk["content"]


def test_docx_upload_preserves_heading_and_table_row_sources(review_client: TestClient):
    version_id = create_design_document(review_client)
    document = WordDocument()
    document.add_heading("4.2 Zone reservation", level=1)
    document.add_paragraph("The Fleet Manager shall reserve exclusive zones before vehicle dispatch.")
    table = document.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Event"
    table.rows[0].cells[1].text = "Retention"
    table.rows[1].cells[0].text = "Task status change"
    table.rows[1].cells[1].text = "90 days"
    stream = BytesIO()
    document.save(stream)

    response = review_client.post(
        f"/documents/{version_id}/upload",
        files={
            "file": (
                "fleet_manager_fs.docx",
                stream.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert response.status_code == 200
    chunks = review_client.get(f"/documents/{version_id}/chunks").json()
    assert any("4.2 Zone reservation" in (chunk["section"] or "") for chunk in chunks)
    assert any("Retention: 90 days" in chunk["content"] for chunk in chunks)


def test_csv_upload_preserves_rows_as_citable_chunks(review_client: TestClient):
    version_id = create_design_document(review_client, document_type="TECHNICAL_MANUAL")
    response = review_client.post(
        f"/documents/{version_id}/upload",
        files={
            "file": (
                "interface_register.csv",
                b"interface,owner,description\nWCS API,Automation,Dispatches tasks to fleet manager\nMES API,IT,Receives execution status\n",
                "text/csv",
            )
        },
    )
    assert response.status_code == 200
    chunks = review_client.get(f"/documents/{version_id}/chunks").json()
    assert len(chunks) == 2
    assert chunks[0]["section"] == "CSV row 1"
    assert "owner: Automation" in chunks[0]["content"]


def test_document_upload_rejects_unsupported_source(review_client: TestClient):
    version_id = create_design_document(review_client)
    response = review_client.post(
        f"/documents/{version_id}/upload",
        files={"file": ("supplier_note.txt", b"not a supported file", "text/plain")},
    )
    assert response.status_code == 400
    assert "PDF, DOCX, and CSV" in response.json()["detail"]


def test_coverage_analysis_persists_only_retrieved_evidence(review_client: TestClient):
    class FakeRetrieval:
        def retrieve(self, query, filters, limit=8):
            return [
                EvidenceChunk(
                    chunk_id="chunk-001",
                    document_version_id=filters.document_version_ids[0],
                    document_title="Fleet Manager Functional Specification",
                    document_type="FS",
                    version="1.0",
                    page=7,
                    section="4.1 Zone reservation",
                    content="The Fleet Manager reserves exclusive zones before dispatching a vehicle.",
                )
            ]

    class FakeJudge:
        def judge(self, *, requirement_code, requirement_text, evidence):
            return CandidateJudgment(
                design_status=CoverageStatus.COVERED,
                evidence_chunk_ids=["chunk-001", "fabricated-id"],
                rationale="The selected evidence explicitly describes exclusive-zone reservation.",
            )

    baseline = review_client.post(
        "/requirement-baselines",
        json={"name": "Coverage URS v1.0", "system": "fleet_manager_wcs"},
    ).json()
    review_client.post(
        f"/requirement-baselines/{baseline['id']}/requirements/import",
        files={
            "file": (
                "urs.csv",
                b"requirement_code,requirement_text\nAGV-URS-001,The Fleet Manager shall reserve exclusive zones.\n",
                "text/csv",
            )
        },
    )
    version_id = create_design_document(review_client)
    review = review_client.post(
        "/review-packages",
        json={
            "name": "DR-coverage",
            "system": "fleet_manager_wcs",
            "requirement_baseline_id": baseline["id"],
            "design_document_version_ids": [version_id],
        },
    ).json()
    run = review_client.post(f"/review-packages/{review['id']}/analyses").json()

    db = database.get_session_factory()()
    try:
        CoverageAnalysisService(db, FakeRetrieval(), FakeJudge()).execute(run["id"])
    finally:
        db.close()

    findings = review_client.get(f"/analysis-runs/{run['id']}/findings")
    assert findings.status_code == 200
    result = findings.json()[0]
    assert result["design_status"] == "covered"
    assert [evidence["chunk_id"] for evidence in result["evidence"]] == ["chunk-001"]
    progress = review_client.get(f"/analysis-runs/{run['id']}/progress")
    assert progress.status_code == 200
    assert progress.json()["completed_items"] == 1
    assert progress.json()["items"][0]["status"] == "completed"


def test_matrix_keeps_one_row_for_every_requirement_including_unfinished_items(review_client: TestClient):
    baseline = review_client.post(
        "/requirement-baselines",
        json={"name": "Matrix completeness URS", "system": "fleet_manager_wcs"},
    ).json()
    review_client.post(
        f"/requirement-baselines/{baseline['id']}/requirements/import",
        files={
            "file": (
                "urs.csv",
                b"requirement_code,requirement_text,priority\n"
                b"AGV-URS-001,The Fleet Manager shall reserve exclusive zones.,High\n"
                b"AGV-URS-002,The Fleet Manager shall retain audit history.,Medium\n",
                "text/csv",
            )
        },
    )
    version_id = create_design_document(review_client)
    review = review_client.post(
        "/review-packages",
        json={
            "name": "DR-matrix-completeness",
            "system": "fleet_manager_wcs",
            "requirement_baseline_id": baseline["id"],
            "design_document_version_ids": [version_id],
        },
    ).json()
    run = review_client.post(f"/review-packages/{review['id']}/analyses").json()

    matrix = review_client.get(f"/analysis-runs/{run['id']}/matrix")
    assert matrix.status_code == 200
    rows = matrix.json()
    assert [row["requirement_code"] for row in rows] == ["AGV-URS-001", "AGV-URS-002"]
    assert all(row["analysis_status"] == "queued" for row in rows)
    assert all(row["design_status"] is None for row in rows)


def test_composite_requirement_cannot_be_marked_covered_when_one_audit_point_is_not_covered(
    review_client: TestClient,
):
    class FakeRetrieval:
        def retrieve(self, query, filters, limit=8):
            chunk_id = "chunk-interface" if "interface" in query.lower() else "chunk-audit"
            return [
                EvidenceChunk(
                    chunk_id=chunk_id,
                    document_version_id=filters.document_version_ids[0],
                    document_title="Fleet Manager Functional Specification",
                    document_type="FS",
                    version="1.0",
                    page=7,
                    section="4.1 Integration",
                    content="The FS describes the requested capability.",
                )
            ]

    class FakeJudge:
        def decompose(self, *, requirement_code, requirement_text):
            return [
                AuditPoint(point_id="p1", source_excerpt="provide an interface", review_point="provide an interface"),
                AuditPoint(point_id="p2", source_excerpt="retain audit history", review_point="retain audit history"),
            ]

        def judge(self, *, requirement_code, requirement_text, evidence, audit_points):
            return CandidateJudgment(
                design_status=CoverageStatus.COVERED,
                evidence_chunk_ids=["chunk-interface", "chunk-audit"],
                rationale="The requirement appears covered.",
                audit_points=[
                    AuditPointJudgment(
                        **audit_points[0].model_dump(),
                        design_status=CoverageStatus.COVERED,
                        evidence_chunk_ids=["chunk-interface"],
                        rationale="Interface evidence is explicit.",
                    ),
                    AuditPointJudgment(
                        **audit_points[1].model_dump(),
                        design_status=CoverageStatus.PARTIALLY_COVERED,
                        evidence_chunk_ids=["chunk-audit"],
                        rationale="Retention duration is not stated.",
                    ),
                ],
            )

    baseline = review_client.post(
        "/requirement-baselines",
        json={"name": "Composite URS", "system": "fleet_manager_wcs"},
    ).json()
    review_client.post(
        f"/requirement-baselines/{baseline['id']}/requirements/import",
        files={
            "file": (
                "urs.csv",
                b"requirement_code,requirement_text\n"
                b"AGV-URS-003,The system shall provide an interface and retain audit history.\n",
                "text/csv",
            )
        },
    )
    version_id = create_design_document(review_client)
    review = review_client.post(
        "/review-packages",
        json={
            "name": "DR-composite",
            "system": "fleet_manager_wcs",
            "requirement_baseline_id": baseline["id"],
            "design_document_version_ids": [version_id],
        },
    ).json()
    run = review_client.post(f"/review-packages/{review['id']}/analyses").json()
    db = database.get_session_factory()()
    try:
        CoverageAnalysisService(db, FakeRetrieval(), FakeJudge()).execute(run["id"])
    finally:
        db.close()

    matrix = review_client.get(f"/analysis-runs/{run['id']}/matrix").json()
    assert matrix[0]["design_status"] == "review_required"
    assert len(matrix[0]["audit_points"]) == 2
    assert matrix[0]["audit_points"][1]["design_status"] == "partially_covered"


def test_matrix_excel_export_contains_all_rows_and_report_metadata(review_client: TestClient):
    from openpyxl import load_workbook

    baseline = review_client.post(
        "/requirement-baselines",
        json={"name": "Export URS", "system": "fleet_manager_wcs"},
    ).json()
    review_client.post(
        f"/requirement-baselines/{baseline['id']}/requirements/import",
        files={
            "file": (
                "urs.csv",
                b"requirement_code,requirement_text\nAGV-URS-010,The system shall retain audit history.\n",
                "text/csv",
            )
        },
    )
    version_id = create_design_document(review_client)
    review = review_client.post(
        "/review-packages",
        json={
            "name": "DR-export",
            "system": "fleet_manager_wcs",
            "requirement_baseline_id": baseline["id"],
            "design_document_version_ids": [version_id],
        },
    ).json()
    run = review_client.post(f"/review-packages/{review['id']}/analyses").json()

    response = review_client.get(f"/analysis-runs/{run['id']}/export.xlsx")
    assert response.status_code == 200
    workbook = load_workbook(BytesIO(response.content))
    assert workbook.sheetnames == ["URS Traceability Matrix", "Report Metadata"]
    matrix = workbook["URS Traceability Matrix"]
    assert matrix["A2"].value == "AGV-URS-010"
    assert matrix["F2"].value == "Assessment incomplete / technical exception"
