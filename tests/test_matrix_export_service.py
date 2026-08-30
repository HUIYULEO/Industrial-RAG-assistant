from io import BytesIO
from types import SimpleNamespace

from openpyxl import load_workbook

from app.services.matrix_export_service import MatrixExportService


def test_dynamic_matrix_and_metadata_values_are_not_saved_as_formulas():
    review = SimpleNamespace(
        name="=1+1",
        id="review-1",
        system="=2+2",
        requirement_baseline_id="baseline-1",
    )
    run = SimpleNamespace(
        completed_at=None,
        created_at="2026-08-30",
        id="run-1",
        status="completed",
    )
    row = SimpleNamespace(
        requirement_code="=3+3",
        requirement_text="=4+4",
        priority="=5+5",
        rationale_impact="=6+6",
        is_critical=True,
        design_status="covered",
        status_definition="Normal status definition",
        analysis_status="completed",
        rationale="=7+7",
        gap="=8+8",
        suggested_reviewer_action="=9+9",
        technical_error="=10+10",
        evidence=[],
        audit_points=[],
    )
    document = SimpleNamespace(
        document=SimpleNamespace(title="=11+11"),
        version="1.0",
        file_name="=12+12",
        file_hash="=13+13",
    )

    content = MatrixExportService().build(
        review=review,
        run=run,
        rows=[row],
        documents=[document],
    )
    workbook = load_workbook(BytesIO(content), data_only=False)
    matrix = workbook["URS Traceability Matrix"]
    metadata = workbook["Report Metadata"]

    for coordinate in ["A2", "B2", "C2", "D2", "J2", "K2", "L2", "N2"]:
        assert matrix[coordinate].data_type != "f"
        assert matrix[coordinate].value.startswith("'=")
    for coordinate in ["B1", "B3", "B8", "B9", "B10"]:
        assert metadata[coordinate].data_type != "f"
        assert metadata[coordinate].value.startswith("'=")
    assert matrix["F2"].value == "covered"
    assert matrix["F2"].data_type == "s"
