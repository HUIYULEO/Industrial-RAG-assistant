"""Unit tests for source-preserving document segmentation."""

from pathlib import Path

import fitz

from app.services.ingestion_service import DocumentIngestionService


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
