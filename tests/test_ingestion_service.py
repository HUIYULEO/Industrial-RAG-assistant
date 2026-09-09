"""Unit tests for source-preserving document segmentation."""

import json
from pathlib import Path

import fitz
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.domain.models import Base, Document, DocumentVersion, DocumentChunk
from app.services.ingestion_service import (
    DEFAULT_CHUNK_OVERLAP,
    DocumentIngestionService,
    PdfTable,
    PdfTextBlock,
    build_embedding_text,
    parent_section_id_for,
)


def test_default_long_text_chunks_retain_200_characters_of_overlap(tmp_path: Path):
    service = DocumentIngestionService(db=None, data_dir=tmp_path)  # type: ignore[arg-type]
    text = "A" * 2_500

    chunks = service._split_buffer(text, page=1, section="Test")

    assert DEFAULT_CHUNK_OVERLAP == 200
    assert service.chunk_overlap == 200
    assert len(chunks) == 2
    assert chunks[0].content[-200:] == chunks[1].content[:200]
    assert "".join(chunk.content for chunk in chunks)[-500:] == text[-500:]


def test_chunk_persistence_failure_rolls_back_and_preserves_original_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    version = DocumentVersion(
        document=Document(title="Fleet Manager FS", document_type="FS", system="fleet_manager"),
        version="1.0",
        status="draft",
    )
    db.add(version)
    db.commit()
    original_commit = db.commit
    original_rollback = db.rollback
    calls = {"commits": 0, "rollbacks": 0}

    def flaky_commit():
        calls["commits"] += 1
        if any(isinstance(item, DocumentChunk) for item in db.new):
            raise RuntimeError("chunk persistence failed")
        return original_commit()

    def tracked_rollback():
        calls["rollbacks"] += 1
        return original_rollback()

    monkeypatch.setattr(db, "commit", flaky_commit)
    monkeypatch.setattr(db, "rollback", tracked_rollback)

    with pytest.raises(ValueError, match="CSV parsing failed: chunk persistence failed"):
        DocumentIngestionService(db, tmp_path).upload_and_parse(
            version.id,
            "interfaces.csv",
            b"interface,owner\nWCS API,Automation\n",
        )

    db.expire_all()
    persisted = db.get(DocumentVersion, version.id)
    assert calls["rollbacks"] >= 1
    assert persisted.ingestion_status == "failed"
    assert persisted.ingestion_error == "chunk persistence failed"
    db.close()


def test_persisted_chunks_have_stable_minimal_hierarchy_metadata(tmp_path: Path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        version = DocumentVersion(
            document=Document(title="Fleet Manager FS", document_type="FS", system="fleet_manager"),
            version="1.0",
            status="draft",
        )
        db.add(version)
        db.commit()

        persisted = DocumentIngestionService(db, tmp_path).upload_and_parse(
            version.id,
            "interfaces.csv",
            b"interface,owner\nWCS API,Automation\nMES API,IT\n",
        )

        chunks = sorted(persisted.chunks, key=lambda chunk: chunk.chunk_index)
        assert len(chunks) == 2
        assert [chunk.source_metadata["chunk_sequence"] for chunk in chunks] == [0, 1]
        assert all(
            chunk.source_metadata["document_version_id"] == version.id
            and chunk.source_metadata["page"] == chunk.page
            and chunk.source_metadata["section_path"] == "CSV"
            and chunk.source_metadata["parent_section_id"] == parent_section_id_for("CSV")
            and chunk.source_metadata["element_type"] == "table_row"
            and chunk.source_metadata["row_index"] == chunk.page
            for chunk in chunks
        )
        assert len({chunk.source_metadata["table_id"] for chunk in chunks}) == 1


def test_embedding_context_prefix_does_not_change_citable_content():
    content = "Danger: stored energy | Isolate energy before maintenance."
    metadata = {"headers": ["Danger", "Measure"]}

    embedded = build_embedding_text(
        content,
        section_path="Residual risks",
        element_type="table_row",
        source_metadata=metadata,
    )

    assert embedded.startswith("[Section: Residual risks]\n[Table header: Danger | Measure]")
    assert embedded.endswith(f"[Evidence: {content}]")
    assert content == "Danger: stored energy | Isolate energy before maintenance."


@pytest.mark.parametrize("page,section,left,cells", [
    (3, "Safety", 40, ["", "unrelated"]),
    (1, "Safety", 40, ["", "unrelated"]),
    (2, "Other section", 40, ["", "unrelated"]),
    (2, "Safety", 100, ["", "unrelated"]),
    (2, "Safety", 40, ["", "unrelated", "extra column"]),
])
def test_blank_first_cell_does_not_join_unrelated_table_rows(tmp_path, page, section, left, cells):
    service = DocumentIngestionService(db=None, data_dir=tmp_path)
    first = PdfTable(page=1, table_index=1, bbox=(40, 120, 560, 760),
                     headers=["Risk", "Measure"], rows=[["stored energy", "isolate"]])
    second = PdfTable(page=page, table_index=1, bbox=(left, 120, 560, 760),
                      headers=["Risk", "Measure"], rows=[cells])
    sections = {1: "Safety", page: section}
    chunks = service._normalise_pdf_table_rows([([], [first], 842), ([], [second], 842)], sections)
    assert len(chunks) == 2
    assert "unrelated" not in chunks[0].content


def test_pdf_chunking_keeps_visual_line_indentation_and_semantic_boundaries():
    service = object.__new__(DocumentIngestionService)
    service.chunk_size = 54
    service.chunk_overlap = 0

    chunks, _ = service._chunk_page(
        "1. Dispatch rules\n  • Reserve the zone before dispatch.\n\nSecond paragraph remains intact.",
        page=1,
        current_section=None,
    )

    assert chunks[0].section == "1. Dispatch rules"
    assert chunks[0].content.startswith("  • Reserve the zone before dispatch.")
    assert chunks[0].content.endswith("dispatch.")
    assert chunks[1].content.strip() == "Second paragraph remains intact."


def test_pdf_parser_uses_pymupdf_blocks_and_retains_page_section_anchors(tmp_path: Path):
    source_path = tmp_path / "design.pdf"
    source = fitz.open()
    first_page = source.new_page()
    first_page.insert_textbox(
        fitz.Rect(72, 72, 500, 220),
        "1. Dispatch rules\nReserve the zone before dispatch.",
        fontsize=11,
    )
    second_page = source.new_page()
    second_page.insert_textbox(
        fitz.Rect(72, 72, 500, 220),
        "The controller retains task dispatch records for audit.",
        fontsize=11,
    )
    source.save(source_path)
    source.close()

    service = object.__new__(DocumentIngestionService)
    service.chunk_size = 2_200
    service.chunk_overlap = 0

    chunks, page_count = service._parse_pdf(source_path)

    assert page_count == 2
    assert [(chunk.page, chunk.section) for chunk in chunks] == [
        (1, "1. Dispatch rules"),
        (2, "1. Dispatch rules"),
    ]
    assert "Reserve the zone before dispatch." in chunks[0].content
    assert "retains task dispatch records" in chunks[1].content


def test_residual_risks_fixture_recovers_all_tables_and_two_independent_page_68_rows():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "residual_risks_tables.json").read_text(encoding="utf-8")
    )
    headers = fixture["headers"]
    pages = []
    for item in fixture["pages"]:
        table = PdfTable(
            page=item["page"],
            table_index=1,
            bbox=(40.0, 120.0, 560.0, 760.0),
            headers=headers,
            rows=item["rows"],
        )
        pages.append(([], [table], 842.0))

    service = object.__new__(DocumentIngestionService)
    chunks = service._normalise_pdf_table_rows(
        pages,
        {item["page"]: "1.8.8 Residual risks during online charging" for item in fixture["pages"]},
    )

    assert {table.page for _, tables, _ in pages for table in tables} == set(range(61, 72))
    assert all(table.headers == headers for _, tables, _ in pages for table in tables)
    page_68 = [chunk for chunk in chunks if chunk.page == 68]
    assert len(page_68) == 2
    assert all(chunk.element_type == "table_row" for chunk in page_68)
    assert "Danger: Risk of active battery contacts" in page_68[0].content
    assert "ABB action: The charging contacts are disconnected" in page_68[0].content
    assert "End-user/integrator measure: It is forbidden to touch" in page_68[0].content
    assert "Table row: 2" in page_68[1].content
    assert page_68[0].source_metadata == {
        "table_index": 1,
        "row_index": 1,
        "bbox": [40.0, 120.0, 560.0, 760.0],
        "headers": headers,
        "cells": fixture["pages"][7]["rows"][0],
        "page_end": 68,
    }


def test_pdf_cleaning_removes_repeated_margins_repairs_hyphenation_and_rejects_false_heading():
    service = object.__new__(DocumentIngestionService)
    service.chunk_size = 2_200
    service.chunk_overlap = 0
    pages = [
        (
            [
                PdfTextBlock((40, 20, 500, 45), "ABB AMR Functional Specification"),
                PdfTextBlock((40, 100, 500, 300), "Follow the ISO 3691-4 proced-\nure for the end-\nuser.\n29 kg)."),
                PdfTextBlock((40, 800, 500, 830), f"Copyright ABB Robotics\nPage {page}"),
            ],
            [],
            842.0,
        )
        for page in range(1, 4)
    ]
    repeated = service._repeated_margin_lines(pages)
    cleaned = service._clean_pdf_block(
        pages[0][0][1], page_height=842.0, repeated_margin_lines=repeated
    )
    footer = service._clean_pdf_block(
        pages[0][0][2], page_height=842.0, repeated_margin_lines=repeated
    )
    chunks, section = service._chunk_page(cleaned, 1, None)

    assert "ISO 3691-4 procedure" in chunks[0].content
    assert "end-user" in chunks[0].content
    assert "29 kg)." in chunks[0].content
    assert section is None
    assert footer == ""


def test_text_blocks_inside_a_table_bbox_are_not_extracted_twice():
    class FakePage:
        def get_text(self, kind, sort=True):
            assert kind == "blocks"
            return [
                (40, 50, 500, 90, "Body paragraph", 0, 0),
                (40, 120, 500, 400, "Danger\nABB action\nMeasure", 1, 0),
            ]

    service = object.__new__(DocumentIngestionService)
    blocks = service._extract_pdf_text_blocks(
        FakePage(), table_bboxes=[(35, 110, 510, 410)]  # type: ignore[arg-type]
    )

    assert [block.text for block in blocks] == ["Body paragraph"]


def test_continued_table_cells_are_merged_into_the_previous_logical_row():
    headers = [
        "Danger",
        "Actions taken by ABB Robotics",
        "Risk reduction measures for the end-user/integrator",
    ]
    pages = [
        (
            [],
            [PdfTable(67, 1, (40, 100, 560, 800), headers, [["Charging risk", "Contacts are", "Do not touch"]])],
            842.0,
        ),
        (
            [],
            [PdfTable(68, 1, (40, 100, 560, 300), headers, [["", "disconnected from the battery.", "the contacts."]])],
            842.0,
        ),
    ]
    service = object.__new__(DocumentIngestionService)

    chunks = service._normalise_pdf_table_rows(pages, {67: "Residual risks", 68: "Residual risks"})

    assert len(chunks) == 1
    assert "ABB action: Contacts are disconnected from the battery." in chunks[0].content
    assert "End-user/integrator measure: Do not touch the contacts." in chunks[0].content
    assert "Page: 67-68" in chunks[0].content
    assert chunks[0].source_metadata["page_end"] == 68
