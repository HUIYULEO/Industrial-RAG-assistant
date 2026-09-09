from dataclasses import replace

from app.domain.evidence import EvidenceChunk
from app.services.evidence_selection import evidence_block, select_evidence_groups


def row(cid, content, page=1, version="v"):
    return EvidenceChunk(chunk_id=cid, document_version_id=version, document_title="Manual",
                         document_type="FS", version="1", page=page, section="Safety", content=content)


def test_common_long_header_is_stored_once_without_losing_distinct_cells():
    header = "A wrongly extracted paragraph used as a table label. " * 20
    chunks = [row(f"r{i}", f"{header}: unique cell {i}") for i in range(6)]
    chunks += [row("recovery", "The AMRs reconnect and the queue is preserved.", page=38)]
    metadata = {f"r{i}": {"headers": [header], "cells": [f"unique cell {i}"],
                           "table_index": 1, "bbox": [0, 0, 100, 100]} for i in range(6)}
    result, trace = select_evidence_groups(chunks, metadata_by_id=metadata, max_groups=2)
    assert len(result) == 7 and len(trace["selected_groups"]) == 2
    context = "\n\n".join(evidence_block(c) for c in result)
    assert context.count(header) == 1
    for i in range(6):
        assert f"unique cell {i}" in context
        assert result[i].content == chunks[i].content
    assert result[-1].chunk_id == "recovery"
    assert trace["context_chars"] == len(context)


def test_different_pages_versions_and_distinct_short_rows_are_not_collapsed():
    chunks = [row("a", "Status: Running"), row("b", "Status: Failed"),
              row("c", "Status: Running", page=2), row("d", "Status: Running", version="other")]
    metadata = {c.chunk_id: {"headers": ["Status"], "cells": [c.content],
                             "table_index": 1, "bbox": [0, 0, 100, 100]} for c in chunks}
    selected, trace = select_evidence_groups(chunks, metadata_by_id=metadata)
    assert selected == chunks
    assert len(trace["selected_groups"]) == 3
    assert not trace["exact_duplicates"]


def test_budget_is_explicit_and_never_truncates_cell_text():
    chunks = [row("a", "x" * 1000), row("b", "short")]
    selected, trace = select_evidence_groups(chunks, metadata_by_id={}, max_context_chars=200)
    assert selected == [chunks[1]]
    assert trace["deferred"] == [{"chunk_id": "a", "reason": "context_budget"}]
    assert trace["context_chars"] <= 200


def test_exact_duplicate_alias_preserves_reference_to_retained_chunk():
    original = row("a", "same source passage")
    selected, trace = select_evidence_groups([original, replace(original, chunk_id="alias")], metadata_by_id={})
    assert selected == [original]
    assert trace["exact_duplicates"] == [{"chunk_id": "alias", "retained_chunk_id": "a"}]


def test_extra_table_row_cannot_displace_original_topk_within_budget():
    chunks = [row("table", "Status: Running"), row("procedure", "Recovery steps " * 20),
              row("extra", "Status: " + "Additional detail " * 20)]
    metadata = {cid: {"headers": ["Status"], "cells": ["distinct"],
                      "table_index": 1, "bbox": [0, 0, 100, 100]}
                for cid in ["table", "extra"]}
    budget = len("\n\n".join(evidence_block(c) for c in chunks[:2])) + 5
    selected, trace = select_evidence_groups(chunks, metadata_by_id=metadata,
                                              max_groups=2, max_context_chars=budget)
    assert [c.chunk_id for c in selected] == ["table", "procedure"]
    assert trace["deferred"] == [{"chunk_id": "extra", "reason": "reserved_for_initial_topk"}]
    assert trace["context_chars"] <= budget


def test_coverage_uses_candidate_pool_and_preserves_original_citation_text():
    from types import SimpleNamespace
    from app.domain.evidence import RetrievalFilters
    from app.services.coverage_service import CoverageAnalysisService
    header = "Long shared table introduction. " * 30
    chunks = [row(f"r{i}", f"{header}: distinct {i}") for i in range(6)]
    chunks += [row("recovery", "Recovery procedure", page=38)]
    calls = []

    class Retrieval:
        def retrieve_candidates(self, query, filters, *, limit, pool_limit):
            calls.append((query, limit, pool_limit))
            return chunks

    class Database:
        def scalars(self, statement):
            return [SimpleNamespace(id=f"r{i}", source_metadata={"headers": [header],
                     "cells": [f"distinct {i}"], "table_index": 1, "bbox": [0, 0, 10, 10]}) for i in range(6)]

    service = CoverageAnalysisService(Database(), Retrieval(), None,
                                      evidence_selection_policy="source_table_groups_v1")
    selected, trace = service._retrieve_evidence(strategy="original", requirement_code="URS",
        requirement_text="Recover after failure", audit_points=[],
        filters=RetrievalFilters(document_version_ids=["v"]))
    assert calls == [("Recover after failure", 10, 30)]
    assert trace["queries"][0]["limit"] == 30
    assert selected[1].content == chunks[1].content
    assert selected[1].context_content != selected[1].content
    assert trace["selection"]["limit_unit"].startswith("source_group")


def test_numbered_procedure_keeps_only_same_section_adjacent_page_continuation():
    seed = row("steps", "1. Connect to controller.\n2. Apply changes.", page=38)
    continuation = row("result", "Connections resume; queue data is retained.", page=39)
    other_section = replace(continuation, chunk_id="other-section", section="Replacement")
    other_version = replace(continuation, chunk_id="other-version", document_version_id="v2")
    distant = replace(continuation, chunk_id="distant", page=41)
    new_steps = replace(continuation, chunk_id="new-steps", content="1. Start a different procedure.")
    selected, trace = select_evidence_groups(
        [seed, other_section, other_version, distant, new_steps, continuation],
        metadata_by_id={}, max_groups=1)
    assert [c.chunk_id for c in selected] == ["steps", "result"]
    assert selected[1].content == continuation.content
    assert trace["procedure_continuation_chunk_ids"] == ["result"]
