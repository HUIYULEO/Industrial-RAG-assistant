"""Structured PDF, DOCX, and CSV extraction with citable chunk persistence."""

from __future__ import annotations

import hashlib
import re
import csv
from io import BytesIO, StringIO
from dataclasses import dataclass
from pathlib import Path

import fitz
from docx import Document as WordDocument
from docx.table import Table
from docx.text.paragraph import Paragraph
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.models import DocumentChunk, DocumentFigure, DocumentVersion
from app.services.visual_evidence_service import VisualEvidenceService

_HEADING_PATTERN = re.compile(r"^(?:\d+(?:\.\d+){0,5}\.?\s+.+|[A-Z][A-Z\s/&-]{5,})$")

# Change these code-level defaults when the corpus requires a different
# structure-aware segmentation strategy. Existing documents must be reparsed
# and re-indexed for a change to take effect.
DEFAULT_CHUNK_SIZE = 2_200
# Citable passages are also read by people. Duplicating the end of one passage
# at the start of the next made the source reader look like a damaged copy of
# the original document, so chunks deliberately do not overlap.
DEFAULT_CHUNK_OVERLAP = 200


@dataclass(frozen=True)
class ParsedChunk:
    page: int
    section: str | None
    content: str


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
        suffix = Path(filename).suffix.lower()
        if suffix not in self.supported_extensions:
            raise ValueError("Supported source formats are PDF, DOCX, and CSV")
        if not content:
            raise ValueError("Uploaded file is empty")

        safe_name = Path(filename).name
        target_dir = self.data_dir / "raw" / version.id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / safe_name
        target_path.write_bytes(content)

        version.file_name = safe_name
        version.file_hash = hashlib.sha256(content).hexdigest()
        version.storage_path = str(target_path)
        version.ingestion_status = "parsing"
        version.ingestion_error = None
        self.db.commit()

        try:
            parsed_chunks, source_units = self._parse_source(target_path, suffix, pdf_password=pdf_password)
            self.db.execute(delete(DocumentChunk).where(DocumentChunk.document_version_id == version.id))
            for index, chunk in enumerate(parsed_chunks):
                self.db.add(
                    DocumentChunk(
                        document_version_id=version.id,
                        chunk_index=index,
                        page=chunk.page,
                        section=chunk.section,
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
            if suffix == ".pdf":
                # Candidate pages are rendered locally but are not sent to an LLM
                # or indexed until an engineer explicitly requests analysis.
                VisualEvidenceService(self.db, self.data_dir).extract_pdf_candidates(
                    version.id, target_path, pdf_password=pdf_password
                )
        except Exception as exc:
            version.ingestion_status = "failed"
            version.ingestion_error = str(exc)
            self.db.commit()
            raise ValueError(f"{suffix[1:].upper()} parsing failed: {exc}") from exc

        self.db.refresh(version)
        return version

    def reparse_stored_document(
        self, document_version_id: str, *, pdf_password: str | None = None
    ) -> DocumentVersion:
        """Rebuild citable chunks from the original locally stored source file."""
        version = self.db.get(DocumentVersion, document_version_id)
        if version is None:
            raise LookupError("Document version not found")
        if not version.storage_path:
            raise ValueError("The original uploaded file is unavailable; upload the document again to parse it")

        source_path = Path(version.storage_path)
        if not source_path.is_file():
            raise ValueError("The original uploaded file is unavailable; upload the document again to parse it")
        suffix = source_path.suffix.lower()
        if suffix not in self.supported_extensions:
            raise ValueError("Stored source format is no longer supported")

        version.ingestion_status = "parsing"
        version.ingestion_error = None
        self.db.commit()
        try:
            parsed_chunks, source_units = self._parse_source(source_path, suffix, pdf_password=pdf_password)
            self.db.execute(delete(DocumentChunk).where(DocumentChunk.document_version_id == version.id))
            for index, chunk in enumerate(parsed_chunks):
                self.db.add(
                    DocumentChunk(
                        document_version_id=version.id,
                        chunk_index=index,
                        page=chunk.page,
                        section=chunk.section,
                        content=chunk.content,
                        content_hash=hashlib.sha256(chunk.content.encode("utf-8")).hexdigest(),
                    )
                )
            version.page_count = source_units
            version.chunk_count = len(parsed_chunks)
            version.ingestion_status = "parsed_pending_index"
            self.db.commit()
            if suffix == ".pdf":
                VisualEvidenceService(self.db, self.data_dir).extract_pdf_candidates(
                    version.id, source_path, pdf_password=pdf_password
                )
        except Exception as exc:
            version.ingestion_status = "failed"
            version.ingestion_error = str(exc)
            self.db.commit()
            raise ValueError(f"{suffix[1:].upper()} parsing failed: {exc}") from exc

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
        """Extract text blocks with PyMuPDF while retaining their page order.

        PyMuPDF gives us page-local text blocks and coordinates instead of a
        single producer-dependent text stream.  We currently persist the
        page/section anchor; keeping block boundaries here also prevents text
        from unrelated visual regions being concatenated into one paragraph.
        """
        with fitz.open(str(path)) as document:
            if document.needs_pass:
                if not document.authenticate(pdf_password or ""):
                    if pdf_password:
                        raise ValueError("The PDF password is incorrect or cannot open this encrypted PDF")
                    raise ValueError("This PDF is encrypted. Enter its password to parse it; the password is not stored")
            if document.page_count == 0:
                raise ValueError("PDF contains no pages")

            chunks: list[ParsedChunk] = []
            current_section: str | None = None
            for page_number, page in enumerate(document, start=1):
                page_text = self._extract_pdf_text_blocks(page)
                if not page_text:
                    continue
                page_chunks, current_section = self._chunk_page(page_text, page_number, current_section)
                chunks.extend(page_chunks)

            if not chunks:
                raise ValueError("No extractable text found; OCR is required for this scanned PDF")
            return chunks, document.page_count

    def _extract_pdf_text_blocks(self, page: fitz.Page) -> str:
        """Return text blocks in visual order, excluding non-text image blocks."""
        text_blocks: list[str] = []
        for block in page.get_text("blocks", sort=True):
            # ``blocks`` is (x0, y0, x1, y1, text, block_no, block_type).
            # Block type 0 is text; image blocks must not become accidental
            # evidence merely because their metadata is available.
            if block[6] != 0:
                continue
            text = self._preserve_pdf_layout(block[4])
            if text:
                text_blocks.append(text)
        return "\n\n".join(text_blocks)

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
            # An overlap makes a source citation repeat content when read.
            # Retain it only for callers that explicitly opt into it.
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
    def _preserve_pdf_layout(text: str, *, trim_outer: bool = True) -> str:
        """Remove extraction artefacts without flattening the original layout."""
        lines = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").split("\n")
        result = "\n".join(line.replace("\u00a0", " ").rstrip() for line in lines)
        return result.strip() if trim_outer else result.rstrip()

    @staticmethod
    def _normalise_text(text: str) -> str:
        return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()
