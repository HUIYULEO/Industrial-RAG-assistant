from app.domain.evidence import EvidenceChunk, RetrievalFilters
from app.services.coverage_service import AuditPoint, CoverageAnalysisService, fuse_ranked_results


def _chunk(chunk_id: str) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        document_version_id="version-1",
        document_title="Test FS",
        document_type="FS",
        version="1",
        page=1,
        section="Test",
        content=chunk_id,
    )


def test_cross_query_fusion_rewards_chunks_retrieved_by_multiple_audit_points() -> None:
    first = [_chunk("a"), _chunk("b"), _chunk("c")]
    second = [_chunk("b"), _chunk("c"), _chunk("d")]

    fused, scores = fuse_ranked_results(
        [("p1", first), ("p2", second)],
        limit=3,
    )

    assert [item.chunk_id for item in fused] == ["b", "c", "a"]
    assert scores["b"] > scores["c"] > scores["a"]


def test_cross_query_fusion_applies_final_evidence_budget() -> None:
    fused, _ = fuse_ranked_results(
        [("p1", [_chunk("a"), _chunk("b")]), ("p2", [_chunk("c"), _chunk("d")])],
        limit=2,
    )

    assert len(fused) == 2
    assert {item.chunk_id for item in fused} == {"a", "c"}


def test_decomposed_retrieval_uses_cross_query_fusion_and_final_top_ten() -> None:
    class FakeRetrieval:
        def retrieve(self, query, filters, limit=8):
            assert limit == 6
            point_id = "p1" if "first" in query else "p2"
            prefix = "a" if point_id == "p1" else "b"
            shared = [_chunk("shared-1"), _chunk("shared-2")]
            unique = [_chunk(f"{prefix}-{index}") for index in range(1, 5)]
            return (shared + unique)[:limit]

    service = CoverageAnalysisService(None, FakeRetrieval(), None)  # type: ignore[arg-type]
    evidence, trace = service._retrieve_evidence(
        strategy="decomposed",
        requirement_code="URS-001",
        requirement_text="The system shall do both things.",
        audit_points=[
            AuditPoint(point_id="p1", source_excerpt="first", review_point="first"),
            AuditPoint(point_id="p2", source_excerpt="second", review_point="second"),
        ],
        filters=RetrievalFilters(document_version_ids=["version-1"]),
    )

    assert len(evidence) == 10
    assert trace["fusion"]["method"] == "cross_query_rrf"
    assert trace["fusion"]["candidate_count_before_final_limit"] == 10
    assert trace["merged_ranked_chunk_ids"][0] == "shared-1"
