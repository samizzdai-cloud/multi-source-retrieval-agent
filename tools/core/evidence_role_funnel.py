"""Benchmark-only Gold-role funnel observation.

The observer lives in a ContextVar so production request contracts stay
unchanged.  It matches anchors against full ``ChunkEntry.content`` in memory,
then exports only role names, stable IDs and content hashes.
"""
from __future__ import annotations

import contextvars
import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from tools.core.models import ChunkEntry


_ACTIVE: contextvars.ContextVar["RoleFunnelObserver | None"] = contextvars.ContextVar(
    "p3_role_funnel_observer", default=None,
)


def normalize_anchor(value: object) -> str:
    return "".join(str(value or "").casefold().split())


@dataclass
class _RoleState:
    role: str
    anchor: str
    document_id: str = ""
    candidate_present: bool = False
    candidate_granularity: str = "not_observed"
    read_present: bool = False
    rerank_retained: bool = False
    partial_tier_retained: bool = False
    plan_eval_judged: bool = False
    plan_eval_tier: str = ""
    source_retained: bool = False
    global_retained: bool = False
    final_present: bool = False
    matches: dict[str, list[dict[str, str]]] = field(default_factory=dict)


class RoleFunnelObserver:
    """In-memory stage observer used only by the P3 runner."""

    def __init__(self, expected_chain: Iterable[Mapping[str, Any]]) -> None:
        self._roles = {
            str(item.get("role") or item.get("anchor") or ""): _RoleState(
                role=str(item.get("role") or item.get("anchor") or ""),
                anchor=normalize_anchor(item.get("anchor")),
                document_id=str(item.get("document_id") or ""),
            )
            for item in expected_chain
            if str(item.get("role") or item.get("anchor") or "") and normalize_anchor(item.get("anchor"))
        }
        self.read_lineage_events: list[dict[str, Any]] = []

    def _matching(self, chunk: ChunkEntry) -> list[_RoleState]:
        text = normalize_anchor(chunk.content)
        return [state for state in self._roles.values() if state.anchor and state.anchor in text]

    @staticmethod
    def _chunk_ref(chunk: ChunkEntry) -> dict[str, str]:
        return {
            "chunk_id": chunk.id,
            "content_sha256": hashlib.sha256(chunk.content.encode("utf-8")).hexdigest(),
            "document_id": str((chunk.metadata or {}).get("document_id") or ""),
        }

    def observe_chunks(self, stage: str, chunks: Iterable[ChunkEntry], *, tier_by_id: Mapping[str, str] | None = None) -> None:
        for chunk in chunks:
            for state in self._matching(chunk):
                state.matches.setdefault(stage, []).append(self._chunk_ref(chunk))
                if stage == "read":
                    state.read_present = True
                    self.read_lineage_events.append({"role": state.role, **self._chunk_ref(chunk)})
                elif stage == "rerank_retained":
                    state.rerank_retained = True
                elif stage == "partial_tier_retained":
                    state.partial_tier_retained = True
                elif stage == "plan_eval":
                    state.plan_eval_judged = True
                    state.plan_eval_tier = str((tier_by_id or {}).get(chunk.id) or state.plan_eval_tier)
                elif stage == "source_retained":
                    state.source_retained = True
                elif stage == "global_retained":
                    state.global_retained = True
                elif stage == "final":
                    state.final_present = True

    def observe_candidates(self, candidates: Iterable[Mapping[str, Any]]) -> None:
        for candidate in candidates:
            candidate_id = str(candidate.get("document_id") or "")
            title = normalize_anchor(candidate.get("document") or candidate.get("title"))
            for state in self._roles.values():
                # Candidate evidence is document-granular: never claim a chunk
                # match before a reader has produced full content.
                if state.document_id and state.document_id == candidate_id:
                    state.candidate_present, state.candidate_granularity = True, "document"
                elif state.document_id and state.document_id in title:
                    state.candidate_present, state.candidate_granularity = True, "document_title"

    def summary(self) -> dict[str, Any]:
        stage_order = (
            ("candidate_present", "candidate"), ("read_present", "read"),
            ("rerank_retained", "rerank"), ("partial_tier_retained", "partial_tier"),
            ("plan_eval_judged", "plan_eval"), ("source_retained", "source_retained"),
            ("global_retained", "global_retained"), ("final_present", "final"),
        )
        roles = []
        for state in self._roles.values():
            first_missing = next((label for attr, label in stage_order if not getattr(state, attr)), "")
            roles.append({
                "role": state.role, "candidate_present": state.candidate_present,
                "candidate_granularity": state.candidate_granularity,
                "read_present": state.read_present, "rerank_retained": state.rerank_retained,
                "partial_tier_retained": state.partial_tier_retained,
                "plan_eval_judged": state.plan_eval_judged, "plan_eval_tier": state.plan_eval_tier,
                "source_retained": state.source_retained, "global_retained": state.global_retained,
                "final_present": state.final_present, "first_missing_stage": first_missing,
                "matches": state.matches,
            })
        return {"roles": roles, "read_lineage_events": self.read_lineage_events}


def activate(observer: RoleFunnelObserver):
    return _ACTIVE.set(observer)


def reset(token: contextvars.Token) -> None:
    _ACTIVE.reset(token)


def current() -> RoleFunnelObserver | None:
    return _ACTIVE.get()
