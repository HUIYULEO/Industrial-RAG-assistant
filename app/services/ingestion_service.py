"""Structured PDF, DOCX, and CSV extraction with citable chunk persistence."""

from __future__ import annotations

import hashlib
import re
import csv
from collections import Counter
from io import BytesIO, StringIO
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import fitz
from docx import Document as WordDocument
from docx.table import Table
from docx.text.paragraph import Paragraph
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.logging_config import get_logger
from app.domain.models import (
    DocumentChunk,
    DocumentFigure,
    DocumentVersion,
    ReviewPackageDocument,
)
from app.services.visual_evidence_service import VisualEvidenceService

logger = get_logger(__name__)

_HEADING_PATTERN = re.compile(
    r"^(?:(?:\d+(?:\.\d+){1,5}\.?|\d+\.)\s+[A-Za-z][^\n]{1,248}|[A-Z][A-Z\s/&-]{5,})$"
)
_STANDALONE_NOTICE_PATTERN = re.compile(r"^(?:WARNING|CAUTION|NOTICE)$", re.IGNORECASE)
_PAGE_NUMBER_PATTERN = re.compile(r"^(?:page\s+)?\d+(?:\s+(?:of|/|\|)\s*\d+)?$", re.IGNORECASE)
_REVISION_PATTERN = re.compile(r"^(?:rev(?:ision)?\.?\s*[:#-]?\s*)[A-Z0-9._-]+$", re.IGNORECASE)

# Change these code-level defaults when the corpus requires a different
# structure-aware segmentation strategy. Existing documents must be reparsed
# and re-indexed for a change to take effect.
DEFAULT_CHUNK_SIZE = 2_200
# Preserve 200 characters of neighbouring context across long chunks to improve
# retrieval recall while keeping each persisted passage independently citable.
DEFAULT_CHUNK_OVERLAP = 200


@dataclass(frozen=True)
class ParsedChunk:
    page: int
    section: str | None
    content: str
    element_type: str = "text"
    source_metadata: dict | None = None


@dataclass(frozen=True)
class PdfTextBlock:
    bbox: tuple[float, float, float, float]
    text: str


@dataclass(frozen=True)
class PdfTable:
    page: int
    table_index: int
    bbox: tuple[float, float, float, float]
    headers: list[str]
    rows: list[list[str]]


class DocumentIngestionService:
    """Preserve document provenance before any vector/LLM processing occurs."""

    def __init__(
        self,
        db: Session,
        data_dir: Path,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ):
        if chunk_size < 1:
            raise ValueError("chunk_size must be at least 1")
        if not 0 <= chunk_overlap < chunk_size:
            raise ValueError("chunk_overlap must be at least 0 and smaller than chunk_size")
        self.db = db
        self.data_dir = data_dir
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    supported_extensions = {".pdf", ".docx", ".csv"}

    def _ensure_version_can_be_modified(self, version: DocumentVersion) -> None:
        if self.db.scalar(
            select(ReviewPackageDocument.id).where(
                ReviewPackageDocument.document_version_id == version.id
            )
        ):
            raise ValueError(
                "A document version in a frozen Review Package cannot be uploaded or reparsed"
            )
        if version.ingestion_status in {"index_queued", "indexing"}:
            raise ValueError(
                "A document version queued for or undergoing indexing cannot be uploaded or reparsed"
            )

    def _raise_ingestion_failure(
        self, document_version_id: str, suffix: str, original_error: Exception
    ) -> NoReturn:
        """Rollback failed writes and retain the original ingestion diagnostic."""
        self.db.rollback()
        try:
            version = self.db.get(DocumentVersion, document_version_id)
            if version is not None:
                version.ingestion_status = "failed"
                version.ingestion_error = str(original_error)
                self.db.commit()
        except Exception:
            # A second persistence failure must not replace the parsing/write
            # exception that explains why ingestion failed.
            self.db.rollback()
        raise ValueError(
            f"{suffix[1:].upper()} parsing failed: {original_error}"
        ) from original_error

    def upload_and_parse(
        self,
        document_version_id: str,
        filename: str,
        content: bytes,
        *,
        pdf_password: str | None = None,
    ) -> DocumentVersion:
        version = self.db.get(DocumentVersion, document_version_id)
        if version is None:
            raise LookupError("Document version not found")
        self._ensure_version_can_be_modified(version)
        suffix = Path(filename).suffix.lower()
        if suffix not in self.supported_extensions:
            raise ValueError("Supported source formats are PDF, DOCX, and CSV")
        if not content:
            raise ValueError("Uploaded file is empty")

        safe_name = Path(filename).name
        target_dir = self.data_dir / "raw" / version.id
        target_path = target_dir / safe_name

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(content)
            version.file_name = safe_name
            version.file_hash = hashlib.sha256(content).hexdigest()
            version.storage_path = str(target_path)
            version.ingestion_status = "parsing"
            version.ingestion_error = None
            self.db.commit()
            parsed_chunks, source_units = self._parse_source(target_path, suffix, pdf_password=pdf_password)
        except Exception as exc:
            self._raise_ingestion_failure(version.id, suffix, exc)

        try:
            self.db.execute(delete(DocumentChunk).where(DocumentChunk.document_version_id == version.id))
            for index, chunk in enumerate(parsed_chunks):
                self.db.add(
                    DocumentChunk(
                        document_version_id=version.id,
                        chunk_index=index,
                        page=chunk.page,
                        section=chunk.section,
                        element_type=chunk.element_type,
                        source_metadata=chunk.source_metadata,
                        content=chunk.content,
                        content_hash=hashlib.sha256(chunk.content.encode("utf-8")).hexdigest(),
                    )
                )
            # For PDF this is pages; for DOCX it is structural blocks and for CSV rows.
            version.page_count = source_units
            version.chunk_count = len(parsed_chunks)
            # The relational record is ready; the next increment adds Milvus vectors.
            version.ingestion_status = "parsed_pending_index"
            self.db.commit()
        except Exception as exc:
            self._raise_ingestion_failure(version.id, suffix, exc)

        if suffix == ".pdf":
            try:
                # Candidate pages are rendered locally but are not sent to an LLM
                # or indexed until an engineer explicitly requests analysis.
                VisualEvidenceService(self.db, self.data_dir).extract_pdf_candidates(
                    version.id, target_path, pdf_password=pdf_password
                )
            except Exception:
                self.db.rollback()
                logger.warning(
                    "Optional PDF visual candidate extraction failed after text ingestion",
                    extra={"document_version_id": version.id},
                    exc_info=True,
                )

        self.db.refresh(version)
        return version

    def reparse_stored_document(
        self, document_version_id: str, *, pdf_password: str | None = None
    ) -> DocumentVersion:
        """Rebuild citable chunks from the original locally stored source file."""
        version = self.db.get(DocumentVersion, document_version_id)
        if version is None:
            raise LookupError("Document version not found")
        self._ensure_version_can_be_modified(version)
        if not version.storage_path:
            raise ValueError("The original uploaded file is unavailable; upload the document again to parse it")

        source_path = Path(version.storage_path)
        if not source_path.is_file():
            raise ValueError("The original uploaded file is unavailable; upload the document again to parse it")
        suffix = source_path.suffix.lower()
        if suffix not in self.supported_extensions:
            raise ValueError("Stored source format is no longer supported")

        try:
            version.ingestion_status = "parsing"
            version.ingestion_error = None
            self.db.commit()
            parsed_chunks, source_units = self._parse_source(source_path, suffix, pdf_password=pdf_password)
        except Exception as exc:
            self._raise_ingestion_failure(version.id, suffix, exc)

        try:
            self.db.execute(delete(DocumentChunk).where(DocumentChunk.document_version_id == version.id))
            for index, chunk in enumerate(parsed_chunks):
                self.db.add(
                    DocumentChunk(
                        document_version_id=version.id,
                        chunk_index=index,
                        page=chunk.page,
                        section=chunk.section,
                        element_type=chunk.element_type,
                        source_metadata=chunk.source_metadata,
                        content=chunk.content,
                        content_hash=hashlib.sha256(chunk.content.encode("utf-8")).hexdigest(),
                    )
                )
            version.page_count = source_units
            version.chunk_count = len(parsed_chunks)
            version.ingestion_status = "parsed_pending_index"
            self.db.commit()
        except Exception as exc:
            self._raise_ingestion_failure(version.id, suffix, exc)

        if suffix == ".pdf":
            try:
                VisualEvidenceService(self.db, self.data_dir).extract_pdf_candidates(
                    version.id, source_path, pdf_password=pdf_password
                )
            except Exception:
                self.db.rollback()
                logger.warning(
                    "Optional PDF visual candidate extraction failed after text reparse",
                    extra={"document_version_id": version.id},
                    exc_info=True,
                )

        self.db.refresh(version)
        return version

    def list_chunks(self, document_version_id: str) -> list[DocumentChunk]:
        if self.db.get(DocumentVersion, document_version_id) is None:
            raise LookupError("Document version not found")
        return list(
            self.db.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.document_version_id == document_version_id)
                .order_by(DocumentChunk.chunk_index)
            )
        )

    def get_chunk_context(
        self, document_version_id: str, chunk_id: str, *, radius: int = 1
    ) -> list[DocumentChunk]:
        """Return one cited passage with its immediate source neighbours."""
        chunk = self.db.scalar(
            select(DocumentChunk).where(
                DocumentChunk.id == chunk_id,
                DocumentChunk.document_version_id == document_version_id,
            )
        )
        if chunk is None:
            raise LookupError("Source passage was not found in this document version")
        return list(
            self.db.scalars(
                select(DocumentChunk)
                .where(
                    DocumentChunk.document_version_id == document_version_id,
                    DocumentChunk.chunk_index >= max(chunk.chunk_index - radius, 0),
                    DocumentChunk.chunk_index <= chunk.chunk_index + radius,
                )
                .order_by(DocumentChunk.chunk_index)
            )
        )

    def get_pdf_source_path(self, document_version_id: str) -> Path:
        """Return a managed original PDF for authenticated in-app reading."""
        version = self.db.get(DocumentVersion, document_version_id)
        if version is None:
            raise LookupError("Document version not found")
        if not version.storage_path:
            raise LookupError("The original source file is unavailable")
        source_path = Path(version.storage_path)
        if source_path.suffix.lower() != ".pdf":
            raise ValueError("The original-page viewer is available only for PDF sources")
        try:
            resolved_path = source_path.resolve(strict=True)
            raw_root = (self.data_dir / "raw").resolve(strict=True)
            resolved_path.relative_to(raw_root)
        except (FileNotFoundError, ValueError) as exc:
            raise LookupError("The original source file is unavailable") from exc
        return resolved_path

    def _parse_source(self, path: Path, suffix: str, *, pdf_password: str | None = None) -> tuple[list[ParsedChunk], int]:
        if suffix == ".pdf":
            return self._parse_pdf(path, pdf_password=pdf_password)
        if suffix == ".docx":
            return self._parse_docx(path)
        return self._parse_csv(path)

    def _parse_pdf(self, path: Path, *, pdf_password: str | None = None) -> tuple[list[ParsedChunk], int]:
        """Extract citable body passages and logical table rows with PyMuPDF."""
        with fitz.open(str(path)) as document:
            if document.needs_pass:
                if not document.authenticate(pdf_password or ""):
                    if pdf_password:
                        raise ValueError("The PDF password is incorrect or cannot open this encrypted PDF")
                    raise ValueError("This PDF is encrypted. Enter its password to parse it; the password is not stored")
            if document.page_count == 0:
                raise ValueError("PDF contains no pages")

            pages: list[tuple[list[PdfTextBlock], list[PdfTable], float]] = []
            for page_number, page in enumerate(document, start=1):
                tables = self._extract_pdf_tables(page, page_number)
                table_bboxes = [table.bbox for table in tables]
                blocks = self._extract_pdf_text_blocks(page, table_bboxes=table_bboxes)
                pages.append((blocks, tables, float(page.rect.height)))

            repeated_margin_lines = self._repeated_margin_lines(pages)
            chunks: list[ParsedChunk] = []
            current_section: str | None = None
            section_by_page: dict[int, str | None] = {}
            for page_number, (blocks, tables, page_height) in enumerate(pages, start=1):
                cleaned_blocks = [
                    self._clean_pdf_block(
                        block,
                        page_height=page_height,
                        repeated_margin_lines=repeated_margin_lines,
                    )
                    for block in blocks
                ]
                page_text = "\n\n".join(text for text in cleaned_blocks if text)
                if page_text:
                    page_chunks, current_section = self._chunk_page(
                        page_text, page_number, current_section
                    )
                    chunks.extend(page_chunks)
                section_by_page[page_number] = current_section
                if not page_text and not tables:
                    chunks.append(
                        ParsedChunk(
                            page=page_number,
                            section=f"Page {page_number} extraction diagnostic",
                            content=f"OCR required: page {page_number} has no reliable embedded text.",
                            element_type="diagnostic",
                            source_metadata={"diagnostic": "ocr_required"},
                        )
                    )

            chunks.extend(self._normalise_pdf_table_rows(pages, section_by_page))
            chunks.sort(
                key=lambda chunk: (
                    chunk.page,
                    1 if chunk.element_type == "table_row" else 0,
                    str((chunk.source_metadata or {}).get("table_index", "")),
                    str((chunk.source_metadata or {}).get("row_index", "")),
                )
            )

            if not any(chunk.element_type != "diagnostic" for chunk in chunks):
                raise ValueError("No extractable text found; OCR is required for this scanned PDF")
            return chunks, document.page_count

    def _extract_pdf_text_blocks(
        self,
        page: fitz.Page,
        *,
        table_bboxes: list[tuple[float, float, float, float]] | None = None,
    ) -> list[PdfTextBlock]:
        """Return non-table text blocks in visual order."""
        text_blocks: list[PdfTextBlock] = []
        table_rects = [fitz.Rect(value) for value in table_bboxes or []]
        for block in page.get_text("blocks", sort=True):
            # ``blocks`` is (x0, y0, x1, y1, text, block_no, block_type).
            # Block type 0 is text; image blocks must not become accidental
            # evidence merely because their metadata is available.
            if block[6] != 0:
                continue
            bbox = fitz.Rect(block[:4])
            if any(bbox.intersects(table_bbox) for table_bbox in table_rects):
                continue
            text = self._clean_pdf_line_breaks(self._preserve_pdf_layout(block[4]))
            if text:
                text_blocks.append(PdfTextBlock(tuple(bbox), text))
        return text_blocks

    def _extract_pdf_tables(self, page: fitz.Page, page_number: int) -> list[PdfTable]:
        """Extract raw table grids while retaining page-local bounding boxes."""
        result: list[PdfTable] = []
        finder = page.find_tables()
        for table_index, table in enumerate(finder.tables, start=1):
            raw_rows = table.extract() or []
            rows = [
                [self._clean_pdf_cell(cell or "") for cell in row]
                for row in raw_rows
            ]
            rows = [row for row in rows if any(row) and not self._is_continued_marker(row)]
            if not rows:
                continue
            table_header = getattr(table, "header", None)
            header_names = [
                self._clean_pdf_cell(value or "")
                for value in (getattr(table_header, "names", None) or [])
            ]
            first_row = rows[0]
            if self._looks_like_table_header(first_row):
                headers = first_row
                rows = rows[1:]
            elif any(header_names):
                headers = header_names
            else:
                headers = [f"Column {index}" for index in range(1, len(first_row) + 1)]
            width = max(len(headers), *(len(row) for row in rows))
            headers = (headers + [f"Column {index}" for index in range(len(headers) + 1, width + 1)])[:width]
            rows = [(row + [""] * width)[:width] for row in rows]
            result.append(
                PdfTable(
                    page=page_number,
                    table_index=table_index,
                    bbox=tuple(float(value) for value in table.bbox),
                    headers=headers,
                    rows=rows,
                )
            )
        return result

    def _repeated_margin_lines(
        self, pages: list[tuple[list[PdfTextBlock], list[PdfTable], float]]
    ) -> set[str]:
        """Identify running headers/footers repeated on multiple PDF pages."""
        counts: Counter[str] = Counter()
        for blocks, _, page_height in pages:
            seen_on_page: set[str] = set()
            for block in blocks:
                if block.bbox[3] > page_height * 0.16 and block.bbox[1] < page_height * 0.84:
                    continue
                seen_on_page.update(
                    self._artifact_key(line)
                    for line in block.text.splitlines()
                    if self._artifact_key(line)
                )
            counts.update(seen_on_page)
        threshold = max(2, int(len(pages) * 0.3 + 0.999))
        return {line for line, count in counts.items() if count >= threshold}

    def _clean_pdf_block(
        self,
        block: PdfTextBlock,
        *,
        page_height: float,
        repeated_margin_lines: set[str],
    ) -> str:
        is_margin = block.bbox[3] <= page_height * 0.16 or block.bbox[1] >= page_height * 0.84
        lines: list[str] = []
        for line in block.text.splitlines():
            stripped = line.strip()
            key = self._artifact_key(stripped)
            if _STANDALONE_NOTICE_PATTERN.fullmatch(stripped):
                continue
            if is_margin and (
                key in repeated_margin_lines
                or _PAGE_NUMBER_PATTERN.fullmatch(stripped)
                or _REVISION_PATTERN.fullmatch(stripped)
                or "copyright" in stripped.casefold()
            ):
                continue
            lines.append(line)
        return self._clean_pdf_line_breaks("\n".join(lines)).strip()

    def _normalise_pdf_table_rows(
        self,
        pages: list[tuple[list[PdfTextBlock], list[PdfTable], float]],
        section_by_page: dict[int, str | None],
    ) -> list[ParsedChunk]:
        """Convert table grids to whole logical-row chunks without character splitting."""
        logical_rows: list[dict] = []
        previous_headers: list[str] = []
        for _, tables, _ in pages:
            for table in tables:
                headers = table.headers if any(table.headers) else previous_headers
                if headers:
                    previous_headers = headers
                for row_index, cells in enumerate(table.rows, start=1):
                    if self._same_table_row(cells, headers) or not any(cells):
                        continue
                    if not cells[0] and logical_rows and logical_rows[-1]["headers"] == headers:
                        previous = logical_rows[-1]
                        previous["cells"] = [
                            self._join_cell_text(old, new)
                            for old, new in zip(previous["cells"], cells)
                        ]
                        previous["page_end"] = table.page
                        continue
                    logical_rows.append(
                        {
                            "page": table.page,
                            "page_end": table.page,
                            "table_index": table.table_index,
                            "row_index": row_index,
                            "bbox": list(table.bbox),
                            "headers": list(headers),
                            "cells": list(cells),
                        }
                    )

        chunks: list[ParsedChunk] = []
        for row in logical_rows:
            section = section_by_page.get(row["page"])
            labels = [self._canonical_table_header(value, index) for index, value in enumerate(row["headers"])]
            lines = [f"Section: {section}" if section else "Section: Document table"]
            lines.extend(
                f"{label}: {value}"
                for label, value in zip(labels, row["cells"])
                if value
            )
            page_reference = (
                str(row["page"])
                if row["page"] == row["page_end"]
                else f"{row['page']}-{row['page_end']}"
            )
            lines.extend([f"Page: {page_reference}", f"Table row: {row['row_index']}"])
            chunks.append(
                ParsedChunk(
                    page=row["page"],
                    section=section,
                    content="\n".join(lines),
                    element_type="table_row",
                    source_metadata={
                        "table_index": row["table_index"],
                        "row_index": row["row_index"],
                        "bbox": row["bbox"],
                        "headers": row["headers"],
                        "cells": row["cells"],
                        "page_end": row["page_end"],
                    },
                )
            )
        return chunks

    def _parse_docx(self, path: Path) -> tuple[list[ParsedChunk], int]:
        document = WordDocument(str(path))
        chunks: list[ParsedChunk] = []
        current_heading: str | None = None
        paragraph_buffer: list[str] = []
        buffer_start = 0
        structural_index = 0

        def flush_paragraphs(last_index: int) -> None:
            nonlocal paragraph_buffer, buffer_start
            if not paragraph_buffer:
                return
            text = self._normalise_text("\n".join(paragraph_buffer))
            if text:
                reference = f"{current_heading or 'Document body'} | paragraphs {buffer_start}-{last_index}"
                chunks.extend(self._split_buffer(text, buffer_start, reference))
            paragraph_buffer = []

        for child in document.element.body.iterchildren():
            structural_index += 1
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                paragraph = Paragraph(child, document)
                text = self._normalise_text(paragraph.text)
                if not text:
                    continue
                style_name = (paragraph.style.name or "").lower()
                if style_name.startswith("heading"):
                    flush_paragraphs(structural_index - 1)
                    current_heading = text
                    continue
                if not paragraph_buffer:
                    buffer_start = structural_index
                paragraph_buffer.append(text)
                if len("\n".join(paragraph_buffer)) >= self.chunk_size:
                    flush_paragraphs(structural_index)
            elif tag == "tbl":
                flush_paragraphs(structural_index - 1)
                table = Table(child, document)
                rows = [[self._normalise_text(cell.text) for cell in row.cells] for row in table.rows]
                if not rows:
                    continue
                headers = rows[0]
                for row_number, row in enumerate(rows[1:] or rows, start=1):
                    values = [f"{headers[i] or f'Column {i + 1}'}: {value}" for i, value in enumerate(row) if value]
                    if values:
                        reference = f"{current_heading or 'Document body'} | table {structural_index}, row {row_number}"
                        chunks.extend(self._split_buffer(" | ".join(values), structural_index, reference))
        flush_paragraphs(structural_index)
        if not chunks:
            raise ValueError("No readable paragraphs or table rows found in DOCX")
        return chunks, structural_index

    def _parse_csv(self, path: Path) -> tuple[list[ParsedChunk], int]:
        raw = path.read_bytes()
        text = self._decode_csv(raw)
        reader = csv.DictReader(StringIO(text))
        if not reader.fieldnames:
            raise ValueError("CSV must contain a header row")
        chunks: list[ParsedChunk] = []
        row_count = 0
        for row_count, row in enumerate(reader, start=1):
            values = [f"{header}: {self._normalise_text(value or '')}" for header, value in row.items() if self._normalise_text(value or "")]
            if values:
                chunks.extend(self._split_buffer(" | ".join(values), row_count, f"CSV row {row_count}"))
        if not chunks:
            raise ValueError("CSV contains no readable data rows")
        return chunks, row_count

    @staticmethod
    def _decode_csv(raw: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ValueError("CSV encoding is not supported")

    def _chunk_page(
        self, page_text: str, page: int, current_section: str | None
    ) -> tuple[list[ParsedChunk], str | None]:
        result: list[ParsedChunk] = []
        buffer: list[str] = []

        def flush() -> None:
            if buffer:
                text = self._preserve_pdf_layout("\n".join(buffer), trim_outer=False)
                if text:
                    result.extend(self._split_buffer(text, page, current_section))
                buffer.clear()

        # Pdf text extractors commonly return a line per visual row rather than
        # paragraphs. Detect headings before joining content, otherwise a heading
        # can be buried inside a page-sized text block and lose its provenance.
        for raw_line in page_text.splitlines():
            # Keep the source line unchanged in the persisted passage. The
            # stripped value is used only to recognise headings.
            line = raw_line.rstrip()
            display_line = line.strip()
            if not display_line:
                if buffer and buffer[-1] != "":
                    buffer.append("")
                continue
            if _HEADING_PATTERN.match(display_line) and len(display_line) <= 250:
                flush()
                current_section = display_line
                continue
            buffer.append(line)
        flush()
        return result, current_section

    def _split_buffer(self, text: str, page: int, section: str | None) -> list[ParsedChunk]:
        if len(text) <= self.chunk_size:
            return [ParsedChunk(page=page, section=section, content=text)]
        chunks: list[ParsedChunk] = []
        start = 0
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            if end < len(text):
                end = self._semantic_break(text, start, end)
            # Do not left-strip: indentation is meaningful in bullet lists and
            # tables, and the same content is shown in the citation reader.
            chunks.append(ParsedChunk(page=page, section=section, content=text[start:end].rstrip()))
            if end >= len(text):
                break
            # Retain neighbouring context for retrieval while forcing at least
            # one-character progress even with a very large configured overlap.
            start = max(end - self.chunk_overlap, start + 1)
        return chunks

    def _semantic_break(self, text: str, start: int, proposed_end: int) -> int:
        """Choose a human-readable break instead of cutting a sentence mid-way."""
        minimum = start + self.chunk_size // 3
        candidates: list[int] = []
        for separator in ("\n\n", "\n"):
            index = text.rfind(separator, start, proposed_end)
            if index >= minimum:
                candidates.append(index + len(separator))
        sentence_ends = [match.end() for match in re.finditer(r"[.!?。！？；;](?:\s|$)", text[start:proposed_end])]
        if sentence_ends:
            candidates.append(start + sentence_ends[-1])
        return max(candidates, default=proposed_end)

    @staticmethod
    def _artifact_key(text: str) -> str:
        return " ".join(text.casefold().split())

    @staticmethod
    def _clean_pdf_line_breaks(text: str) -> str:
        """Repair extraction-only English hyphenation while retaining layout."""
        preserved_compounds = {
            "design-specification",
            "end-user",
            "load-presence",
            "material-movement",
            "product-quality",
            "risk-reduction",
            "safety-related",
            "site-approved",
            "source-traceable",
            "time-stamped",
        }

        def repair(match: re.Match[str]) -> str:
            left, right = match.groups()
            separator = "-" if f"{left.casefold()}-{right.casefold()}" in preserved_compounds else ""
            return f"{left}{separator}{right}"

        return re.sub(r"([A-Za-z]+)-[ \t]*\n[ \t]*([a-z][A-Za-z]*)", repair, text)

    @classmethod
    def _clean_pdf_cell(cls, text: str) -> str:
        repaired = cls._clean_pdf_line_breaks(cls._preserve_pdf_layout(text))
        return re.sub(r"\s*\n\s*", " ", repaired).strip()

    @staticmethod
    def _looks_like_table_header(row: list[str]) -> bool:
        values = [value.casefold() for value in row if value]
        if not values:
            return False
        header_terms = (
            "danger",
            "risk",
            "action",
            "measure",
            "end-user",
            "integrator",
            "description",
            "requirement",
        )
        return sum(any(term in value for term in header_terms) for value in values) >= min(2, len(values))

    @classmethod
    def _is_continued_marker(cls, row: list[str]) -> bool:
        populated = [cls._artifact_key(value) for value in row if value.strip()]
        return len(populated) == 1 and (
            populated[0] == "continued"
            or populated[0].endswith(" continued")
            or populated[0].endswith(" (continued)")
        )

    @classmethod
    def _same_table_row(cls, left: list[str], right: list[str]) -> bool:
        if not right:
            return False
        width = max(len(left), len(right))
        left_keys = [cls._artifact_key(value) for value in left + [""] * (width - len(left))]
        right_keys = [cls._artifact_key(value) for value in right + [""] * (width - len(right))]
        return left_keys == right_keys

    @staticmethod
    def _join_cell_text(first: str, second: str) -> str:
        if not first:
            return second
        if not second:
            return first
        return f"{first} {second}".strip()

    @staticmethod
    def _canonical_table_header(header: str, index: int) -> str:
        value = " ".join(header.split())
        folded = value.casefold()
        if "danger" in folded:
            return "Danger"
        if "abb" in folded and "action" in folded:
            return "ABB action"
        if "end-user" in folded or "integrator" in folded:
            return "End-user/integrator measure"
        return value or f"Column {index + 1}"

    @staticmethod
    def _preserve_pdf_layout(text: str, *, trim_outer: bool = True) -> str:
        """Remove extraction artefacts without flattening the original layout."""
        lines = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").split("\n")
        result = "\n".join(line.replace("\u00a0", " ").rstrip() for line in lines)
        return result.strip() if trim_outer else result.rstrip()

    @staticmethod
    def _normalise_text(text: str) -> str:
        return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()
