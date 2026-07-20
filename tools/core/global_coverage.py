"""Body-free cross-source evidence coverage snapshots.

This is an observation/contract layer, not a final evidence judge.  It gives
the future merged plan-eval a stable input without letting FocusBoard's
dispatch quota decide final coverage.
"""
from __future__ import annotations

from typing import Any, Iterable

from tools.core.models import ChunkEntry


def snapshot_global_coverage(
    chunks: Iterable[ChunkEntry], focuses: Iterable[object], quota: dict[str, int], *, stage: str,
) -> list[dict[str, Any]]:
    rows = list(chunks)
    critical_min = max(0, int(quota.get("critical_min", 0)))
    important_min = max(0, int(quota.get("important_min", 0)))
    out: list[dict[str, Any]] = []
    for focus in focuses:
        focus_id = str(getattr(focus, "focus_id", "") or "")
        if not focus_id:
            continue
        matched = []
        for chunk in rows:
            if chunk.tier not in {"critical", "important"}:
                continue
            answered = set((chunk.metadata or {}).get("answered_focuses") or [])
            if focus_id in answered or focus_id in (chunk.target_focuses or set()):
                matched.append(chunk)
        critical = [chunk for chunk in matched if chunk.tier == "critical"]
        important = [chunk for chunk in matched if chunk.tier == "important"]
        documents = {
            str((chunk.metadata or {}).get("document_id") or (chunk.metadata or {}).get("doc_id")
                or (chunk.metadata or {}).get("source_name") or "")
            for chunk in matched
        }
        sources = {chunk.source for chunk in matched}
        covered = len(critical) >= critical_min and len(important) >= important_min
        out.append({
            "event": "global_coverage", "stage": stage, "focus_id": focus_id,
            "critical_count": len(critical), "important_count": len(important),
            "evidence_count": len(matched), "unique_document_count": len(documents - {""}),
            "contributing_sources": sorted(sources), "coverage_status": "covered" if covered else "gap",
            "quota": {"critical_min": critical_min, "important_min": important_min},
        })
    return out
