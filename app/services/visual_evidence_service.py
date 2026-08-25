"""Visual-evidence extraction for flow diagrams and interface drawings in PDFs."""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path
from typing import Any, Protocol

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.models import DocumentChunk, DocumentFigure, DocumentVersion


_VISUAL_TERMS = re.compile(
    r"\b(flow|workflow|process|interface|integration|architecture|sequence|data flow|state machine|"
    r"communication|interaction|diagram|figure)\b",
    re.IGNORECASE,
)


class VisualAnalysis(BaseModel):
    """Unverified description of a supplied figure, never a compliance conclusion."""

    diagram_type: str = Field(description="Short technical type, for example data flow or state diagram")
    visible_labels: list[str] = Field(default_factory=list)
    candidate_description: str = Field(description="Concise factual description of visibly supported behaviour")
    candidate_relationships: list[str] = Field(default_factory=list)
    limitations: str | None = Field(default=None)


class VisualInterpreter(Protocol):
    def analyse(self, *, image_path: Path, page: int, section: str | None) -> VisualAnalysis: ...


class ConfiguredVisualInterpreter:
    """Vision-model adapter kept behind a small protocol for deterministic tests."""

    def __init__(self, model: Any):
        self._llm = model.with_structured_output(VisualAnalysis)

    def analyse(self, *, image_path: Path, page: int, section: str | None) -> VisualAnalysis:
        image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
        message = HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": f"""Inspect this page image from a supplier design document.
Page: {page}; section: {section or 'not stated'}.

Extract only what is visibly supported by the diagram. Identify labels and arrow/relationship direction when clear. Do not infer missing controls, system behaviour, compliance, approval, or verification. Every result is a candidate interpretation for an engineer to review; state a limitation whenever direction, labels, or symbols are ambiguous. Return English only.""",
                },
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_data}", "detail": "high"}},
            ]
        )
        return self._llm.invoke([message])


class VisualEvidenceService:
    """Renders selected PDF pages and promotes confirmed visual analysis to citable chunks."""

    def __init__(self, db: Session, data_dir: Path, *, max_pages: int = 12):
        self.db = db
        self.data_dir = data_dir
        self.max_pages = max_pages

    def extract_pdf_candidates(
        self, document_version_id: str, source_path: Path, *, pdf_password: str | None = None
    ) -> list[DocumentFigure]:
        """Render likely diagram pages without calling an LLM or changing the retrieval index."""
        try:
            import fitz
        except ImportError as exc:  # pragma: no cover - dependency is pinned in production
            raise ValueError("PDF visual extraction requires the PyMuPDF dependency") from exc

        version = self.db.get(DocumentVersion, document_version_id)
        if version is None:
            raise LookupError("Document version not found")
        if source_path.suffix.lower() != ".pdf":
            return []

        self.db.query(DocumentFigure).filter(DocumentFigure.document_version_id == document_version_id).delete()
        page_sections = {
            page: section
            for page, section in self.db.execute(
                select(DocumentChunk.page, DocumentChunk.section)
                .where(DocumentChunk.document_version_id == document_version_id)
                .where(DocumentChunk.section.is_not(None))
            ).all()
        }
        figures: list[DocumentFigure] = []
        figure_dir = self.data_dir / "derived" / "figures" / document_version_id
        figure_dir.mkdir(parents=True, exist_ok=True)

        with fitz.open(source_path) as document:
            if document.needs_pass:
                if not document.authenticate(pdf_password or ""):
                    if pdf_password:
                        raise ValueError("The PDF password is incorrect or cannot render this encrypted PDF")
                    raise ValueError("This PDF is encrypted. Enter its password to render it")
            for page_index, page in enumerate(document, start=1):
                if len(figures) >= self.max_pages:
                    break
                page_text = page.get_text("text") or ""
                page_area = page.rect.width * page.rect.height
                embedded_images = any(
                    sum(rect.width * rect.height for rect in page.get_image_rects(image[0])) >= page_area * 0.08
                    for image in page.get_images(full=True)
                )
                vector_drawings = len(page.get_drawings())
                if not (embedded_images or vector_drawings >= 6 or _VISUAL_TERMS.search(page_text)):
                    continue
                image_path = figure_dir / f"page-{page_index:03d}.png"
                page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(image_path)
                figures.append(
                    DocumentFigure(
                        document_version_id=document_version_id,
                        page=page_index,
                        section=page_sections.get(page_index),
                        image_path=str(image_path),
                        image_hash=hashlib.sha256(image_path.read_bytes()).hexdigest(),
                        analysis_status="extracted",
                    )
                )
        self.db.add_all(figures)
        self.db.commit()
        for figure in figures:
            self.db.refresh(figure)
        return figures

    def list_figures(self, document_version_id: str) -> list[DocumentFigure]:
        if self.db.get(DocumentVersion, document_version_id) is None:
            raise LookupError("Document version not found")
        return list(
            self.db.scalars(
                select(DocumentFigure)
                .where(DocumentFigure.document_version_id == document_version_id)
                .order_by(DocumentFigure.page)
            )
        )

    def get_figure(self, document_version_id: str, figure_id: str) -> DocumentFigure:
        figure = self.db.scalar(
            select(DocumentFigure)
            .where(DocumentFigure.id == figure_id)
            .where(DocumentFigure.document_version_id == document_version_id)
        )
        if figure is None:
            raise LookupError("Visual evidence item not found")
        return figure

    def analyse_figures(self, document_version_id: str, interpreter: VisualInterpreter) -> list[DocumentFigure]:
        figures = self.list_figures(document_version_id)
        if not figures:
            raise ValueError("No candidate visual pages were extracted from this PDF")
        version = self.db.get(DocumentVersion, document_version_id)
        assert version is not None

        for figure in figures:
            try:
                analysis = interpreter.analyse(
                    image_path=Path(figure.image_path), page=figure.page, section=figure.section
                )
                figure.diagram_type = analysis.diagram_type
                figure.visible_labels = analysis.visible_labels
                figure.candidate_description = analysis.candidate_description
                figure.candidate_relationships = analysis.candidate_relationships
                figure.analysis_status = "analysed"
                figure.analysis_error = analysis.limitations
                self._upsert_citation_chunk(version, figure)
            except Exception as exc:  # keep other figures available to the reviewer
                figure.analysis_status = "failed"
                figure.analysis_error = str(exc)

        if any(item.analysis_status == "analysed" for item in figures):
            # Visual chunks are new evidence; the engineer must explicitly re-index.
            version.ingestion_status = "parsed_pending_index"
            version.chunk_count = self.db.scalar(
                select(func.count()).select_from(DocumentChunk).where(DocumentChunk.document_version_id == version.id)
            ) or 0
        self.db.commit()
        for figure in figures:
            self.db.refresh(figure)
        return figures

    def _upsert_citation_chunk(self, version: DocumentVersion, figure: DocumentFigure) -> None:
        labels = "; ".join(figure.visible_labels or []) or "No labels confidently identified"
        relationships = "\n".join(f"- {item}" for item in figure.candidate_relationships or []) or "- No relationship confidently identified"
        content = (
            "Candidate visual interpretation — reviewer confirmation required.\n"
            f"Diagram type: {figure.diagram_type or 'not classified'}\n"
            f"Visible labels: {labels}\n"
            f"Candidate description: {figure.candidate_description or 'not stated'}\n"
            f"Candidate relationships:\n{relationships}\n"
            "Source: rendered PDF page retained in the review workspace."
        )
        chunk = self.db.get(DocumentChunk, figure.citation_chunk_id) if figure.citation_chunk_id else None
        if chunk is None:
            max_index = self.db.scalar(
                select(func.max(DocumentChunk.chunk_index)).where(DocumentChunk.document_version_id == version.id)
            )
            chunk = DocumentChunk(
                document_version_id=version.id,
                chunk_index=(max_index if max_index is not None else -1) + 1,
                page=figure.page,
                section=f"Visual evidence | {figure.section or f'page {figure.page}'}",
                content=content,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            )
            self.db.add(chunk)
            self.db.flush()
            figure.citation_chunk_id = chunk.id
        else:
            chunk.page = figure.page
            chunk.section = f"Visual evidence | {figure.section or f'page {figure.page}'}"
            chunk.content = content
            chunk.content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
