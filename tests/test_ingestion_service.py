"""Unit tests for source-preserving document segmentation."""

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
