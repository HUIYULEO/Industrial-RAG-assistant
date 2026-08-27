"""Scoped, bilingual technical Q&A over the selected design-review evidence."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.core.glossary import terminology_context
from app.domain.evidence import EvidenceChunk, RetrievalFilters
from app.domain.enums import DESIGN_DOCUMENT_TYPES
from app.services.retrieval_service import RetrievalService


class NormalizedQuery(BaseModel):
    retrieval_query: str = Field(min_length=1, description="Concise English technical retrieval query")


class GroundedAnswer(BaseModel):
    answer: str = Field(min_length=1)
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    limitations: str | None = None


@dataclass(frozen=True)
class PreparedAnswer:
    """Retrieved evidence ready for either a complete or streamed answer."""

    retrieval_query: str
    evidence: list[EvidenceChunk]
    conversation_history: list[tuple[str, str]]
    no_evidence_answer: GroundedAnswer | None = None


def _history_context(history: list[tuple[str, str]]) -> str:
    if not history:
        return "No prior conversation."
    return "\n".join(f"{role.upper()}: {content}" for role, content in history)


class QueryNormalizer(Protocol):
    def normalize(self, question: str, conversation_history: list[tuple[str, str]]) -> NormalizedQuery: ...


class AnswerGenerator(Protocol):
    def generate(
        self, *, question: str, evidence: list[EvidenceChunk], conversation_history: list[tuple[str, str]]
    ) -> GroundedAnswer: ...


class StreamingAnswerGenerator(AnswerGenerator, Protocol):
    """Optional capability: deliver the same answer as incremental text.

    A generator is not required to implement this.  ``DesignReviewChatService``
    keeps :class:`AnswerGenerator` as its constructor contract and reports a
    non-streaming generator at the point of use, so a third-party adapter that
    only supports complete answers stays usable on the regular chat endpoint.
    """

    def stream(
        self, *, question: str, evidence: list[EvidenceChunk], conversation_history: list[tuple[str, str]]
    ) -> Iterator[str]: ...


class ConfiguredQueryNormalizer:
    """Query normalizer backed by the chat provider selected at runtime."""

    def __init__(self, model: Any):
        self._llm = model.with_structured_output(NormalizedQuery)

    def normalize(self, question: str, conversation_history: list[tuple[str, str]]) -> NormalizedQuery:
        glossary_context = terminology_context(question)
        history_context = _history_context(conversation_history)
        return self._llm.invoke(
            "Translate the user's question into a concise English technical retrieval query. "
            "Preserve identifiers, acronyms, vendor terms, and constraints exactly where possible. "
            "Use the terminology guidance only to understand or translate terms; do not add facts not in the question. "
            "Use the recent conversation only to resolve references such as 'it' or 'that requirement'; it is not evidence. "
            f"{glossary_context}\n\n"
            f"Recent conversation:\n{history_context}\n\n"
            f"User question:\n{question}"
        )


class ConfiguredGroundedAnswerGenerator:
    """Grounded answer generator backed by the selected chat provider."""

    def __init__(self, model: Any):
        self._streaming_llm = model
        self._llm = model.with_structured_output(GroundedAnswer)

    @staticmethod
    def _prompt(*, question: str, evidence: list[EvidenceChunk], conversation_history: list[tuple[str, str]]) -> str:
        """Keep complete and streamed answers under the same evidence-only rules."""
        glossary_context = terminology_context(question)
        history_context = _history_context(conversation_history)
        context = "\n\n".join(
            f"[chunk_id={item.chunk_id}]\n"
            f"{item.document_title} v{item.version} | {item.document_type} | "
            f"section={item.section or 'not stated'} | page={item.page or 'not stated'}\n"
            f"{item.content}"
            for item in evidence
        )
        return f"""You are a technical document assistant for warehouse-automation design review.
Answer in English even if the user asked in another language. Use only the supplied evidence.
Never state that a document, design, or supplier is approved, compliant, or verified.
If the evidence is insufficient, explicitly say so and describe the limitation.
Select only chunk IDs shown below that support your answer.
Use terminology guidance only for wording; never treat it as supplier-document evidence.
Use the recent conversation only to understand the question's references; never treat it as evidence.

{glossary_context}

Recent conversation:
{history_context}

Question:
{question}

Evidence:
{context}"""

    def generate(
        self, *, question: str, evidence: list[EvidenceChunk], conversation_history: list[tuple[str, str]]
    ) -> GroundedAnswer:
        return self._llm.invoke(self._prompt(question=question, evidence=evidence, conversation_history=conversation_history))

    def stream(
        self, *, question: str, evidence: list[EvidenceChunk], conversation_history: list[tuple[str, str]]
    ) -> Iterator[str]:
        """Yield provider text chunks while preserving the regular answer prompt."""
        for chunk in self._streaming_llm.stream(
            self._prompt(question=question, evidence=evidence, conversation_history=conversation_history)
        ):
            content = getattr(chunk, "content", "")
            if isinstance(content, str) and content:
                yield content


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
        conversation_history: list[tuple[str, str]] | None = None,
    ) -> tuple[GroundedAnswer, list[EvidenceChunk], str]:
        prepared = self.prepare(
            question=question,
            document_version_ids=document_version_ids,
            system=system,
            conversation_history=conversation_history,
        )
        if prepared.no_evidence_answer:
            return prepared.no_evidence_answer, [], prepared.retrieval_query
        generated = self.generator.generate(
            question=question,
            evidence=prepared.evidence,
            conversation_history=prepared.conversation_history,
        )
        return self._selected_answer(generated, prepared.evidence), self._selected_evidence(generated, prepared.evidence), prepared.retrieval_query

    def prepare(
        self,
        *,
        question: str,
        document_version_ids: list[str],
        system: str,
        conversation_history: list[tuple[str, str]] | None = None,
    ) -> PreparedAnswer:
        """Normalize and retrieve once before either delivery mode begins."""
        recent_history = (conversation_history or [])[-12:]
        normalized = self.normalizer.normalize(question, recent_history)
        evidence = self.retrieval.retrieve(
            normalized.retrieval_query,
            RetrievalFilters(
                document_version_ids=document_version_ids,
                system=system,
                document_types=sorted(DESIGN_DOCUMENT_TYPES),
            ),
            limit=6,
        )
        if not evidence:
            return PreparedAnswer(
                retrieval_query=normalized.retrieval_query,
                evidence=[],
                conversation_history=recent_history,
                no_evidence_answer=GroundedAnswer(
                    answer="No explicit evidence was found in the selected review scope.",
                    limitations="The selected design-specification versions did not return relevant indexed evidence.",
                ),
            )
        return PreparedAnswer(
            retrieval_query=normalized.retrieval_query,
            evidence=evidence,
            conversation_history=recent_history,
        )

    def stream_answer(
        self, *, question: str, prepared: PreparedAnswer
    ) -> Iterator[str]:
        """Stream answer text after retrieval has completed.

        Citations are the retrieved passages supplied to the model. The final
        event exposes their top-ranked subset for the same evidence ledger used
        by the non-streaming response.
        """
        if prepared.no_evidence_answer:
            yield prepared.no_evidence_answer.answer
            return
        # Streaming is optional (see StreamingAnswerGenerator); a generator that
        # does not offer it is a configuration fact, not a request error.
        stream = getattr(self.generator, "stream", None)
        if not callable(stream):
            raise TypeError("The configured answer generator does not support streaming")
        yield from stream(
            question=question,
            evidence=prepared.evidence,
            conversation_history=prepared.conversation_history,
        )

    @staticmethod
    def _selected_evidence(generated: GroundedAnswer, evidence: list[EvidenceChunk]) -> list[EvidenceChunk]:
        evidence_by_id = {item.chunk_id: item for item in evidence}
        valid_ids = [item for item in generated.evidence_chunk_ids if item in evidence_by_id]
        # A response without valid source IDs may still be useful, but its source
        # scope must remain inspectable. Use the top three retrieved chunks.
        return [evidence_by_id[item] for item in valid_ids] or evidence[:3]

    def _selected_answer(self, generated: GroundedAnswer, evidence: list[EvidenceChunk]) -> GroundedAnswer:
        selected = self._selected_evidence(generated, evidence)
        return generated.model_copy(update={"evidence_chunk_ids": [item.chunk_id for item in selected]})
