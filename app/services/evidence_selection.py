"""Pack ranked evidence into bounded source groups without inventing citations.

Table rows retain their original EvidenceChunk.content for source citations.
Only the model-facing copy elides common column labels, with an explicit link
to the first retained row. Group identity is page/bbox/version scoped, never
inferred from text similarity or a heading alone.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import re
from typing import Mapping, Sequence

from app.domain.evidence import EvidenceChunk


def evidence_block(chunk: EvidenceChunk) -> str:
    content = chunk.context_content if chunk.context_content is not None else chunk.content
    return (f"[chunk_id={chunk.chunk_id}]\n"
            f"{chunk.document_title} v{chunk.version} | {chunk.document_type} | "
            f"section={chunk.section or 'not stated'} | page={chunk.page or 'not stated'}\n"
            f"{content}")


def table_group_key(chunk: EvidenceChunk, metadata: Mapping) -> str | None:
    if not metadata.get("headers") or not metadata.get("cells"):
        return None
    if metadata.get("table_index") is None or not metadata.get("bbox"):
        return None
    # Older table_id values can repeat across different pages in one section.
    identity = [chunk.document_version_id, chunk.page, metadata["table_index"],
                metadata["bbox"], metadata["headers"]]
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
    return f"table:{digest}"


def _compact_row(chunk: EvidenceChunk, headers: Sequence[str], anchor_id: str) -> EvidenceChunk:
    content = chunk.content
    # Only replace exact label prefixes. Unrecognised layouts remain verbatim.
    # The anchor's original text supplies the complete shared label meaning.
    for index, header in enumerate(headers, 1):
        if not header:
            continue
        content = "\n".join(
            f"[same table column {index}]: {line[len(header) + 2:]}"
            if line.startswith(f"{header}: ") else line
            for line in content.split("\n")
        )
    if content == chunk.content:
        return chunk
    compact = f"[Table column labels shared with chunk_id={anchor_id}]\n{content}"
    # Short genuine headers do not warrant longer indirection markers.
    return replace(chunk, context_content=compact) if len(compact) < len(chunk.content) else chunk


def select_evidence_groups(
    ranked: Sequence[EvidenceChunk], *, metadata_by_id: Mapping[str, Mapping],
    max_groups: int = 10, max_context_chars: int = 24000,
) -> tuple[list[EvidenceChunk], dict]:
    if max_groups < 1 or max_context_chars < 1:
        raise ValueError("Evidence budgets must be positive")
    # Keep a numbered procedure's next-page continuation when both are already
    # recalled. Scope by version, exact section and adjacent page; never fetch
    # an entire parent section or synthesize a merged citation.
    continuations: dict[str, str] = {}
    for seed in ranked[:max_groups]:
        if not seed.section or seed.page is None or table_group_key(seed, metadata_by_id.get(seed.chunk_id, {})):
            continue
        if len(re.findall(r"(?m)^\s*\d+[.)]\s+", seed.content)) < 2:
            continue
        for candidate in ranked:
            if (candidate.document_version_id == seed.document_version_id
                    and candidate.section == seed.section and candidate.page == seed.page + 1
                    and not table_group_key(candidate, metadata_by_id.get(candidate.chunk_id, {}))
                    and not re.search(r"(?m)^\s*\d+[.)]\s+", candidate.content)):
                continuations.setdefault(seed.chunk_id, f"procedure:{seed.chunk_id}")
                continuations.setdefault(candidate.chunk_id, f"procedure:{seed.chunk_id}")
    groups: dict[str, list[EvidenceChunk]] = {}
    duplicates, seen_ids, seen_content = [], set(), {}
    for chunk in ranked:
        metadata = metadata_by_id.get(chunk.chunk_id, {})
        group = table_group_key(chunk, metadata)
        signature = (chunk.document_version_id, chunk.page, chunk.section, group, chunk.content)
        if chunk.chunk_id in seen_ids or signature in seen_content:
            duplicates.append({"chunk_id": chunk.chunk_id,
                               "retained_chunk_id": seen_content.get(signature, chunk.chunk_id)})
            continue
        seen_ids.add(chunk.chunk_id)
        seen_content[signature] = chunk.chunk_id
        groups.setdefault(group or continuations.get(chunk.chunk_id) or f"chunk:{chunk.chunk_id}", []).append(chunk)

    selected, selected_groups, deferred = [], [], []
    used_chars = 0
    # Extra rows from an early table must not consume space reserved for the
    # original top-K evidence. The original top-K itself may exceed the hard
    # budget; that case stays explicit in deferred instead of truncating text.
    initial_ids = {chunk.chunk_id for chunk in ranked[:max_groups]}
    reserved = {chunk.chunk_id: len(evidence_block(chunk)) + 2
                for members in groups.values() for chunk in members if chunk.chunk_id in initial_ids}
    for group, members in groups.items():
        if len(selected_groups) >= max_groups:
            deferred.extend({"chunk_id": row.chunk_id, "reason": "group_limit"} for row in members)
            continue
        retained, anchor_id = [], None
        for row in members:
            candidate = _compact_row(row, metadata_by_id.get(row.chunk_id, {}).get("headers", []), anchor_id) if anchor_id else row
            cost = len(evidence_block(candidate)) + (2 if selected else 0)
            reserved.pop(row.chunk_id, None)
            if row.chunk_id not in initial_ids and used_chars + cost + sum(reserved.values()) > max_context_chars:
                deferred.append({"chunk_id": row.chunk_id, "reason": "reserved_for_initial_topk"})
                continue
            if used_chars + cost > max_context_chars:
                deferred.append({"chunk_id": row.chunk_id, "reason": "context_budget"})
                continue
            selected.append(candidate)
            retained.append(candidate.chunk_id)
            anchor_id = anchor_id or candidate.chunk_id
            used_chars += cost
        if retained:
            selected_groups.append({"group_id": group, "chunk_ids": retained})
    return selected, {
        "policy": "source_table_groups_v1", "candidate_count": len(ranked),
        "max_groups": max_groups, "max_context_chars": max_context_chars,
        "context_chars": used_chars, "selected_chunk_ids": [row.chunk_id for row in selected],
        "selected_groups": selected_groups, "exact_duplicates": duplicates, "deferred": deferred,
        "compacted_chunk_ids": [row.chunk_id for row in selected if row.context_content is not None],
        "procedure_continuation_chunk_ids": [row.chunk_id for row in selected
                                              if row.chunk_id in continuations
                                              and continuations[row.chunk_id] != f"procedure:{row.chunk_id}"],
        "limit_unit": "source_group; multiple independently citable rows may share one group",
    }
