"""Deterministic merged plan-eval arbitration.

The source plan-eval is the semantic judge for one source.  This module is the
single cross-source completion judge: it consumes *retained, already judged*
evidence plus source verdicts, never raw FocusBoard state or prompt bodies.
It intentionally does not rank final chunks or manufacture another LLM tier.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from tools.core.models import ChunkEntry


_TERMINAL_GAPS = {"upstream_evidence_needed", "evidence_gap"}


@dataclass(frozen=True)
class GlobalPlanEvalResult:
    decision: str  # stop | continue
    focus_decisions: list[dict[str, Any]]
    replan_focus_ids: tuple[str, ...]

    def trace_event(self) -> dict[str, Any]:
        return {
            "event": "global_plan_eval",
            "decision": self.decision,
            "replan_focus_ids": list(self.replan_focus_ids),
            "focus_decisions": self.focus_decisions,
        }


def _source_decisions(
    source_traces: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Return the last source plan-eval decision for every source/focus pair."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for source, events in source_traces.items():
        per_focus: dict[str, dict[str, Any]] = {}
        for event in events:
            if event.get("event") != "plan_eval":
                continue
            for row in event.get("focus_decisions") or []:
                if not isinstance(row, Mapping):
                    continue
                focus_id = str(row.get("focus_id") or "").strip()
                if focus_id:
                    per_focus[focus_id] = dict(row)
        if per_focus:
            out[str(source)] = per_focus
    return out


def _evidence_for_focus(chunks: Iterable[ChunkEntry], focus_id: str) -> list[ChunkEntry]:
    """Only source-evaluated Focus provenance counts as final evidence.

    ``target_focuses`` is a dispatch hint.  Treating it as proof would let a
    broad planner assignment turn an unread/unjudged chunk into coverage.
    """
    return [
        chunk for chunk in chunks
        if chunk.tier in {"critical", "important"}
        and focus_id in set((chunk.metadata or {}).get("answered_focuses") or [])
    ]


def evaluate_merged_plan(
    chunks: Iterable[ChunkEntry],
    focuses: Iterable[object],
    quota: Mapping[str, int],
    source_traces: Mapping[str, Sequence[Mapping[str, Any]]],
) -> GlobalPlanEvalResult:
    """Arbitrate cross-source Focus completion and targeted replan eligibility.

    A source ``covered`` verdict is accepted only when at least one of its
    retained chunks carries evaluated ``answered_focuses`` provenance.  Where
    there is no source verdict (for example a single-loop source), quotas are a
    conservative deterministic fallback.  ``upstream_evidence_needed`` is
    terminal: global retrieval must not repeat the same source search.
    """
    rows = list(chunks)
    source_by_focus = _source_decisions(source_traces)
    critical_min = max(0, int(quota.get("critical_min", 0)))
    important_min = max(0, int(quota.get("important_min", 0)))
    decisions: list[dict[str, Any]] = []
    replan_focus_ids: list[str] = []

    for focus in focuses:
        focus_id = str(getattr(focus, "focus_id", "") or "").strip()
        if not focus_id:
            continue
        evidence = _evidence_for_focus(rows, focus_id)
        critical = sum(chunk.tier == "critical" for chunk in evidence)
        important = sum(chunk.tier == "important" for chunk in evidence)
        source_rows = {
            source: verdicts[focus_id]
            for source, verdicts in source_by_focus.items()
            if focus_id in verdicts
        }
        covered_by_source = [
            source for source, row in source_rows.items()
            if row.get("status") == "covered"
        ]
        quota_covered = critical >= critical_min and important >= important_min
        if evidence and (covered_by_source or (not source_rows and quota_covered)):
            status, gap_type = "covered", "none"
            reason = "merged retained evidence has source-evaluated Focus provenance"
        else:
            gaps = [str(row.get("gap_type") or "") for row in source_rows.values()]
            if "upstream_evidence_needed" in gaps:
                status, gap_type = "gap", "upstream_evidence_needed"
                reason = "source plan-eval requires upstream evidence; do not repeat retrieval"
            elif "query_refinement" in gaps:
                status, gap_type = "gap", "query_refinement"
                reason = "retained evidence does not cover Focus; a new bounded query may be planned"
                replan_focus_ids.append(focus_id)
            elif covered_by_source:
                # A source had accepted the Focus, but merge/dedup/final-cap
                # removed every judged supporting chunk.  Repeating retrieval
                # would duplicate I/O; this is an evidence-retention defect.
                status, gap_type = "gap", "evidence_gap"
                reason = "source-covered evidence was lost before global completion"
            elif evidence:
                status, gap_type = "partially_covered", "evidence_gap"
                reason = "retained evidence is insufficient for a covered decision"
            else:
                status, gap_type = "gap", "query_refinement"
                reason = "no source-evaluated retained evidence for Focus"
                replan_focus_ids.append(focus_id)
        decisions.append({
            "focus_id": focus_id,
            "status": status,
            "gap_type": gap_type,
            "critical_count": critical,
            "important_count": important,
            "evidence_count": len(evidence),
            "contributing_sources": sorted({chunk.source for chunk in evidence}),
            "source_verdict_sources": sorted(source_rows),
            "reason": reason,
        })

    # Replan is an eligibility output.  RetrieveAgent does not execute it until
    # it owns a query-signature/dedup budget; this prevents cross-source global
    # arbitration from repeating already-completed source work.
    return GlobalPlanEvalResult(
        decision="continue" if replan_focus_ids else "stop",
        focus_decisions=decisions,
        replan_focus_ids=tuple(replan_focus_ids),
    )
