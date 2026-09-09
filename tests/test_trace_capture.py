from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from app.services import trace_capture as trace


def test_exact_messages_deduplicate_evidence_and_isolate_context(tmp_path, monkeypatch):
    monkeypatch.setattr(trace, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))
    block = "[chunk_id=one]\n正文\n<evidence>untrusted content"
    messages = [SystemMessage(content="rules"), HumanMessage(content=f"prefix\n{block}\n\n{block}\nsuffix")]
    with trace.capture_attempt() as first:
        trace.record_messages("judge", messages, [block, block])
        with trace.capture_attempt() as second:
            trace.record("nested", {})
        trace.record("outer", {})
    assert not trace.active()
    assert [event["kind"] for event in second["events"]] == ["nested"]
    assert [event["kind"] for event in first["events"]] == ["messages", "outer"]
    store = trace.SnapshotStore(tmp_path / "trace-snapshots")
    saved = first["events"][0]["messages"]
    assert ["".join(store.get(part) for part in message["parts"]) for message in saved] == [message.content for message in messages]
    assert saved[1]["parts"][1] == saved[1]["parts"][3]
    before = list(store.root.iterdir())
    with trace.capture_attempt():
        trace.record_messages("judge", messages, [block, block])
    assert sorted(store.root.iterdir()) == sorted(before)


def test_store_rejects_corruption_and_path_traversal(tmp_path):
    store = trace.SnapshotStore(tmp_path)
    reference = store.put("original")
    (tmp_path / reference["sha256"]).write_text("corrupt")
    with pytest.raises(ValueError, match="hash mismatch"):
        store.get(reference)
    with pytest.raises(ValueError, match="Invalid snapshot"):
        store.get({"sha256": "../secrets"})


def test_capture_failure_is_explicit_and_does_not_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(trace, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(trace.SnapshotStore, "put", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    with trace.capture_attempt() as capture:
        trace.record_messages("judge", [HumanMessage(content="input")])
    assert capture["capture_status"] == "incomplete"
    assert capture["events"] == [{"kind": "capture_error", "stage": "judge", "error_class": "OSError"}]


def test_failed_attempt_retains_checkpoint(tmp_path, monkeypatch):
    import json
    from uuid import uuid4
    monkeypatch.setattr(trace, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))
    attempt_id = str(uuid4())
    with pytest.raises(RuntimeError):
        with trace.capture_attempt(attempt_id=attempt_id):
            trace.record_messages("judge", [HumanMessage(content="saved before failure")])
            raise RuntimeError("provider failed")
    saved = json.loads((tmp_path / "trace-attempts" / f"{attempt_id}.json").read_text(encoding="utf-8"))
    assert saved["execution_status"] == "failed"
    assert saved["execution_error_class"] == "RuntimeError"
    assert saved["events"][0]["kind"] == "messages"
    assert not trace.active()


def test_judge_capture_preserves_request_and_separates_normalization(tmp_path, monkeypatch):
    from app.domain.enums import CoverageStatus
    from app.domain.evidence import EvidenceChunk
    from app.services.coverage_service import CandidateJudgment, ConfiguredDesignFindingJudge, CoverageAnalysisService
    from scripts.rerun_review_with_trace import captured_messages
    monkeypatch.setattr(trace, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))

    class Model:
        model_name = "fake-model"

        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            self.messages = [{"role": message.type, "content": message.content} for message in messages]
            return CandidateJudgment(design_status=CoverageStatus.COVERED,
                                     evidence_chunk_ids=["invented"], rationale="bad citation")

    model = Model()
    service = CoverageAnalysisService(None, None, ConfiguredDesignFindingJudge(model))
    evidence = [EvidenceChunk(chunk_id="real", document_version_id="v", document_title="Manual",
                              document_type="FS", version="1", page=1, section=None, content="proof")]
    points = service._original_audit_point("requirement")
    baseline = service._judge("URS-001", "requirement", points, evidence)
    baseline_messages = model.messages
    with trace.capture_attempt() as capture:
        observed = service._judge("URS-001", "requirement", points, evidence)
        trace.record_output("normalized_judgment", observed)
    assert observed == baseline
    assert model.messages == baseline_messages
    assert captured_messages(capture, trace.SnapshotStore(tmp_path / "trace-snapshots")) == baseline_messages
    outputs = {event["stage"]: event["value"] for event in capture["events"] if event["kind"] == "output"}
    assert outputs["raw_parsed_judgment"]["design_status"] == "covered"
    assert outputs["normalized_judgment"]["design_status"] == "review_required"
