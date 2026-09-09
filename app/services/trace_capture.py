"""Attempt-local diagnostics and immutable, content-addressed text snapshots.

These are application inputs/parsed outputs, not provider wire-response dumps.
No credentials or arbitrary model configuration are serialized.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import UUID
from datetime import datetime, timezone
from importlib.metadata import version, PackageNotFoundError

from app.core.config import get_settings

_current: ContextVar[dict | None] = ContextVar("review_trace_capture", default=None)


class SnapshotStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def put(self, text: str) -> dict:
        payload = text.encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / digest
        if not path.exists():
            fd, temporary = tempfile.mkstemp(dir=self.root, prefix=".pending-")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        if path.read_bytes() != payload:
            raise ValueError("Snapshot content hash collision or corruption")
        return {"sha256": digest, "bytes": len(payload)}

    def get(self, reference: dict) -> str:
        digest = reference["sha256"]
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid snapshot hash")
        payload = (self.root / digest).read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError("Snapshot hash mismatch")
        return payload.decode("utf-8")


@contextmanager
def capture_attempt(*, branch_diagnostics: bool = False, attempt_id: str | None = None):
    if attempt_id is not None:
        attempt_id = str(UUID(attempt_id))
    state = {"schema_version": 2, "capture_status": "complete",
             "started_at": datetime.now(timezone.utc).isoformat(),
             "attempt_id": attempt_id, "execution_status": "running",
             "branch_diagnostics": branch_diagnostics, "events": []}
    token = _current.set(state)
    try:
        yield state
        state["execution_status"] = "completed"
    except BaseException as exc:
        state["execution_status"] = "failed"
        state["execution_error_class"] = type(exc).__name__
        raise
    finally:
        state["finished_at"] = datetime.now(timezone.utc).isoformat()
        _checkpoint(state)
        _current.reset(token)


def active() -> bool:
    return _current.get() is not None


def branches_enabled() -> bool:
    state = _current.get()
    return bool(state and state["branch_diagnostics"])


def capture_error(stage: str, exc: Exception) -> None:
    state = _current.get()
    if state is not None:
        state["capture_status"] = "incomplete"
        record("capture_error", {"stage": stage, "error_class": type(exc).__name__})


def record(kind: str, value: dict) -> None:
    state = _current.get()
    if state is not None:
        state["events"].append({"kind": kind, **value})
        _checkpoint(state)


def _checkpoint(state: dict) -> None:
    """Per-attempt journal survives retries/failed DB transactions; no public route."""
    if not state.get("attempt_id"):
        return
    temporary = None
    try:
        root = get_settings().data_dir / "trace-attempts"
        root.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=root, prefix=".pending-")
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / f"{state['attempt_id']}.json")
    except Exception as exc:
        state["capture_status"] = "incomplete"
        state["checkpoint_error_class"] = type(exc).__name__
    finally:
        if temporary and os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError as exc:
                state["capture_status"] = "incomplete"
                state["checkpoint_cleanup_error_class"] = type(exc).__name__


def record_messages(stage: str, messages: list, evidence_blocks: list[str] | None = None) -> None:
    """Store exact ordered messages; replace repeated evidence with hash references."""
    state = _current.get()
    if state is None:
        return
    try:
        store = SnapshotStore(get_settings().data_dir / "trace-snapshots")
        saved = []
        for message in messages:
            remaining = message.content
            parts = []
            if message.type == "human":
                for block in evidence_blocks or []:
                    before, separator, after = remaining.partition(block)
                    if not separator:
                        raise ValueError("Evidence block absent from exact message")
                    parts.extend([store.put(before), store.put(block)])
                    remaining = after
            parts.append(store.put(remaining))
            saved.append({"role": message.type, "parts": parts})
        record("messages", {"stage": stage, "messages": saved})
    except Exception as exc:
        # Diagnostics must not turn a successful business request into a retry.
        capture_error(stage, exc)


def record_output(stage: str, value) -> None:
    record("output", {"stage": stage, "value": value.model_dump(mode="json")})


def snapshot_json(value: dict) -> dict:
    return SnapshotStore(get_settings().data_dir / "trace-snapshots").put(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def record_snapshot(stage: str, value: dict) -> None:
    if not active():
        return
    try:
        record("snapshot", {"stage": stage, "reference": snapshot_json(value)})
    except Exception as exc:
        capture_error(stage, exc)


def runtime_identity() -> dict:
    """Whitelist only: never serialize Settings or provider client objects."""
    settings = get_settings()
    names = ("llm_provider", "chat_model", "embedding_provider", "embedding_model",
             "embedding_dimensions", "llm_timeout_seconds", "llm_max_retries",
             "milvus_collection", "worker_build_version", "review_evidence_selection_policy")
    result = {name: getattr(settings, name) for name in names}
    result["packages"] = {}
    for package in ("pymilvus", "langchain-core", "langchain-openai", "pydantic", "PyMuPDF"):
        try:
            result["packages"][package] = version(package)
        except PackageNotFoundError:
            result["packages"][package] = None
    root = Path(__file__).resolve().parents[1]
    result["mounted_source_sha256"] = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ("services/coverage_service.py", "services/ingestion_service.py",
                     "services/trace_capture.py", "services/model_provider.py",
                     "repositories/milvus_repository.py", "services/retrieval_service.py",
                     "services/evidence_selection.py")
    }
    result["parser_version_provenance"] = "mounted source now; not proof of parser used for existing chunks"
    return result
