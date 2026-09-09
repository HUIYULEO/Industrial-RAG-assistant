from pathlib import Path

from app.services.ingestion_service import DocumentIngestionService
from app.services.ingestion_service import PdfTextBlock


def test_numbered_procedure_keeps_actions_in_one_section(tmp_path):
    service = DocumentIngestionService(None, tmp_path)
    text = ("3.10 What to do if a Primary Fleet Manager Fails\n"
            "In the event of a failure:\n"
            "1. Connect to SetNetGo on the Secondary Fleet Manager.\n"
            "2. Navigate to the Pairing section of the System area.\n"
            "3. Select the Primary option and click the Apply Button.\n"
            "4. After 30 to 60 seconds, the Current Status should change to Starting.")
    chunks, section = service._chunk_page(text, 38, "Earlier section")
    assert len(chunks) == 1
    assert chunks[0].content == text.split("\n", 1)[1]
    assert section == "3.10 What to do if a Primary Fleet Manager Fails"


def test_supplier_description_is_not_a_repeated_column_header():
    description = "This page allows download of license information files for this device. " * 3
    assert not DocumentIngestionService._plausible_table_header(["", "Download", description])
    assert not DocumentIngestionService._looks_like_table_header(["Description of all risk and safety actions " * 20])
    assert DocumentIngestionService._plausible_table_header(["Callout", "Description"])
    assert DocumentIngestionService._plausible_table_header(["Protocol", "Port(s)", "Initiator to Recipient"])


def test_running_next_section_header_does_not_steal_previous_section_text(tmp_path):
    service = DocumentIngestionService(None, tmp_path)
    next_heading = "3.11 Remove and Replace Appliances"
    blocks = [PdfTextBlock((70, 35, 500, 47), next_heading),
              PdfTextBlock((70, 80, 500, 150), "AMRs automatically reconnect. The queue is preserved."),
              PdfTextBlock((70, 200, 500, 220), next_heading),
              PdfTextBlock((70, 250, 500, 270), "Removal procedure.")]
    repeated = service._repeated_margin_lines([(blocks, [], 842)])
    text = "\n\n".join(service._clean_pdf_block(block, page_height=842, repeated_margin_lines=repeated) for block in blocks)
    chunks, section = service._chunk_page(text, 39, "3.10 Primary Failure Recovery")
    assert chunks[0].section == "3.10 Primary Failure Recovery"
    assert "automatically reconnect" in chunks[0].content
    assert chunks[-1].section == next_heading
    assert section == next_heading
