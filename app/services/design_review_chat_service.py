"""Scoped, bilingual technical Q&A over the selected design-review evidence."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.core.glossary import terminology_context
from app.domain.evidence import EvidenceChunk, RetrievalFilters
from app.services.retrieval_service import RetrievalService


class NormalizedQuery(BaseModel):
    retrieval_query: str = Field(min_length=1, description="Concise English technical retrieval query")


class GroundedAnswer(BaseModel):
    answer: str = Field(min_length=1)
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    limitations: str | None = None


class QueryNormalizer(Protocol):
    def normalize(self, question: str) -> NormalizedQuery: ...


class AnswerGenerator(Protocol):
    def generate(self, *, question: str, evidence: list[EvidenceChunk]) -> GroundedAnswer: ...


class ConfiguredQueryNormalizer:
    """Query normalizer backed by the chat provider selected at runtime."""

    def __init__(self, model: Any):
        self._llm = model.with_structured_output(NormalizedQuery)

    def normalize(self, question: str) -> NormalizedQuery:
        glossary_context = terminology_context(question)
        return self._llm.invoke(
            "Translate the user's question into a concise English technical retrieval query. "
            "Preserve identifiers, acronyms, vendor terms, and constraints exactly where possible. "
            "Use the terminology guidance only to understand or translate terms; do not add facts not in the question. "
            f"{glossary_context}\n\n"
            f"User question:\n{question}"
        )


class ConfiguredGroundedAnswerGenerator:
    """Grounded answer generator backed by the selected chat provider."""

    def __init__(self, model: Any):
        self._llm = model.with_structured_output(GroundedAnswer)

    def generate(self, *, question: str, evidence: list[EvidenceChunk]) -> GroundedAnswer:
        glossary_context = terminology_context(question)
        context = "\n\n".join(
            f"[chunk_id={item.chunk_id}]\n"
            f"{item.document_title} v{item.version} | {item.document_type} | "
            f"section={item.section or 'not stated'} | page={item.page or 'not stated'}\n"
            f"{item.content}"
            for item in evidence
        )
        return self._llm.invoke(
            f"""You are a technical document assistant for warehouse-automation design review.
Answer in English even if the user asked in another language. Use only the supplied evidence.
Never state that a document, design, or supplier is approved, compliant, or verified.
If the evidence is insufficient, explicitly say so and describe the limitation.
Select only chunk IDs shown below that support your answer.
Use terminology guidance only for wording; never treat it as supplier-document evidence.

{glossary_context}

Question:
{question}

Evidence:
{context}"""
        )


class DesignReviewChatService:
    """A normal RAG chat workflow, with no open-ended agent planning."""

    def __init__(
        self,
        retrieval: RetrievalService,
        normalizer: QueryNormalizer,
        generator: AnswerGenerator,
    ):
        self.retrieval = retrieval
        self.normalizer = normalizer
        self.generator = generator

    def answer(
        self,
        *,
        question: str,
        document_version_ids: list[str],
        system: str,
    ) -> tuple[GroundedAnswer, list[EvidenceChunk], str]:
        normalized = self.normalizer.normalize(question)
        evidence = self.retrieval.retrieve(
            normalized.retrieval_query,
            RetrievalFilters(document_version_ids=document_version_ids, system=system, document_types=["FS", "DS"]),
            limit=6,
        )
        if not evidence:
            return (
                GroundedAnswer(
                    answer="No explicit evidence was found in the selected review scope.",
                    limitations="The selected FS/DS document versions did not return relevant indexed evidence.",
                ),
                [],
                normalized.retrieval_query,
            )
        generated = self.generator.generate(question=question, evidence=evidence)
        evidence_by_id = {item.chunk_id: item for item in evidence}
        valid_ids = [item for item in generated.evidence_chunk_ids if item in evidence_by_id]
        # A response without valid source IDs may still be useful, but its source
        # scope must remain inspectable. Use the top three retrieved chunks.
        selected = [evidence_by_id[item] for item in valid_ids] or evidence[:3]
        return generated.model_copy(update={"evidence_chunk_ids": [item.chunk_id for item in selected]}), selected, normalized.retrieval_query
