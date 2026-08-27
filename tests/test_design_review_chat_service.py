from app.domain.evidence import EvidenceChunk
from app.services.design_review_chat_service import DesignReviewChatService, GroundedAnswer, NormalizedQuery


class FakeNormalizer:
    def __init__(self):
        self.history: list[tuple[str, str]] | None = None

    def normalize(self, question: str, conversation_history: list[tuple[str, str]]) -> NormalizedQuery:
        self.history = conversation_history
        return NormalizedQuery(retrieval_query=question)


class FakeRetrieval:
    def retrieve(self, query, filters, limit):
        return [
            EvidenceChunk(
                chunk_id="chunk-1",
                document_version_id="version-1",
                document_title="Fleet Manager FS",
                document_type="FS",
                version="1.0",
                page=1,
                section="Task dispatch",
                content="The system retains task dispatch records.",
                fused_score=0.8,
            )
        ]


class FakeGenerator:
    def __init__(self):
        self.history: list[tuple[str, str]] | None = None

    def generate(self, *, question, evidence, conversation_history) -> GroundedAnswer:
        self.history = conversation_history
        return GroundedAnswer(answer="The records are retained.", evidence_chunk_ids=["chunk-1"])


class FakeStreamingGenerator(FakeGenerator):
    def stream(self, *, question, evidence, conversation_history):
        self.history = conversation_history
        yield "The records "
        yield "are retained."


def test_chat_uses_a_bounded_history_for_follow_up_questions():
    normalizer = FakeNormalizer()
    generator = FakeGenerator()
    service = DesignReviewChatService(FakeRetrieval(), normalizer, generator)
    history = [("user", f"Question {index}") for index in range(15)]

    answer, evidence, retrieval_query = service.answer(
        question="How long are they retained?",
        document_version_ids=["version-1"],
        system="fleet_manager",
        conversation_history=history,
    )

    assert retrieval_query == "How long are they retained?"
    assert answer.evidence_chunk_ids == ["chunk-1"]
    assert [item.chunk_id for item in evidence] == ["chunk-1"]
    assert normalizer.history == history[-12:]
    assert generator.history == history[-12:]


def test_chat_can_stream_after_retrieval_with_the_same_bounded_history():
    normalizer = FakeNormalizer()
    generator = FakeStreamingGenerator()
    service = DesignReviewChatService(FakeRetrieval(), normalizer, generator)
    history = [("assistant", f"Answer {index}") for index in range(15)]

    prepared = service.prepare(
        question="What is retained?",
        document_version_ids=["version-1"],
        system="fleet_manager",
        conversation_history=history,
    )
    answer = "".join(service.stream_answer(question="What is retained?", prepared=prepared))

    assert answer == "The records are retained."
    assert prepared.retrieval_query == "What is retained?"
    assert [item.chunk_id for item in prepared.evidence] == ["chunk-1"]
    assert normalizer.history == history[-12:]
    assert generator.history == history[-12:]
