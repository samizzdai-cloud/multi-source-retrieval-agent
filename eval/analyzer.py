"""Analyzer — pattern discovery across experiment results and grades.

Surfaces insights that aggregate metrics hide:
  - query_type breakdown
  - difficulty breakdown
  - focus theme patterns
  - source contribution analysis
  - early-stop effectiveness
  - reranker funnel loss
  - timing outliers
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional


def analyze_experiment(
    traces: List[Dict[str, Any]],
    grades: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Analyze patterns in a single experiment's results.

    Args:
        traces: list of per-query traces from runner
        grades: {query_id: {plan_grade, chunk_grade, coverage_grade}}

    Returns:
        {
            "notes": [...],             # freeform observations
            "by_query_type": {...},     # grouped analysis
            "by_difficulty": {...},
            "by_project": {...},
            "source_analysis": {...},
            "timing_analysis": {...},
            "focus_analysis": {...},
        }
    """
    grades = grades or {}
    notes: List[str] = []

    valid_traces = [t for t in traces if "timing" in t]
    if not valid_traces:
        return {"notes": ["No valid traces to analyze"], "error": "empty"}

    # ── Group by dimensions ──────────────────────────────────────────────
    by_qt = _group_traces(valid_traces, lambda t: t.get("plan", {}).get("query_type", "unknown"))
    by_diff = _group_traces(valid_traces, lambda t: t.get("annotations", {}).get("difficulty", "unknown"))
    by_proj = _group_traces(valid_traces, lambda t: t.get("annotations", {}).get("project", "unknown"))

    # ── Query type breakdown ─────────────────────────────────────────────
    qt_analysis = {}
    for qt, ts in by_qt.items():
        stats = _timing_stats(ts)
        chunk_stats = _chunk_stats(ts)
        qt_analysis[qt] = {**stats, **chunk_stats, "count": len(ts)}

    # ── Difficulty breakdown ─────────────────────────────────────────────
    diff_analysis = {}
    for d, ts in by_diff.items():
        stats = _timing_stats(ts)
        chunk_stats = _chunk_stats(ts)
        diff_analysis[d] = {**stats, **chunk_stats, "count": len(ts)}

        # Pattern detection
        if stats.get("mean_total_ms", 0) > 15000 and d != "hard":
            notes.append(f"'{d}' difficulty queries avg {stats['mean_total_ms']}ms — unexpectedly slow")

    # ── Project breakdown ────────────────────────────────────────────────
    proj_analysis = {}
    for p, ts in by_proj.items():
        proj_analysis[p] = {**_timing_stats(ts), **_chunk_stats(ts), "count": len(ts)}

    # ── Source contribution ──────────────────────────────────────────────
    source_chunks: Dict[str, List[int]] = {}
    for t in valid_traces:
        for c in t.get("final_chunks", []):
            src = c.get("source_tool", "unknown")
            source_chunks.setdefault(src, []).append(1)
    source_analysis = {src: len(counts) for src, counts in source_chunks.items()}
    total_final = sum(source_analysis.values())
    source_pct = {src: round(n / max(1, total_final) * 100, 1) for src, n in source_analysis.items()}

    if source_pct:
        dominant = max(source_pct, key=source_pct.get)
        if source_pct[dominant] > 80:
            notes.append(f"Source '{dominant}' dominates at {source_pct[dominant]}% of final chunks — check if other sources are contributing")

    # ── Timing outliers ──────────────────────────────────────────────────
    all_times = [(t.get("query_id"), t["timing"]["total_ms"]) for t in valid_traces]
    if len(all_times) > 3:
        times_only = [x[1] for x in all_times]
        mean_t = statistics.mean(times_only)
        std_t = statistics.stdev(times_only) if len(times_only) > 1 else 0
        outliers = [(qid, ms) for qid, ms in all_times if ms > mean_t + 2 * std_t]
        if outliers:
            for qid, ms in outliers:
                notes.append(f"Query id={qid} is a timing outlier at {ms}ms (mean={mean_t:.0f}ms, 2σ={mean_t + 2*std_t:.0f}ms)")

    # ── Focus analysis ───────────────────────────────────────────────────
    focus_hit_rates: Dict[str, List[float]] = {}
    for t in valid_traces:
        hits = t.get("retrieval", {}).get("focus_hits", {})
        for f, cnt in hits.items():
            focus_hit_rates.setdefault(f, []).append(1.0 if cnt > 0 else 0.0)

    never_hit = [f for f, rates in focus_hit_rates.items() if sum(rates) == 0]
    if never_hit:
        notes.append(f"{len(never_hit)} focuses NEVER hit across all queries — may indicate plan or source gaps")

    # ── Grade-based analysis (if grades available) ───────────────────────
    if grades:
        plan_scores = []
        chunk_mean_scores = []
        coverage_scores = []
        for qid, g in grades.items():
            pg = g.get("plan_grade", {})
            if "composite_score" in pg:
                plan_scores.append(pg["composite_score"])
            cg = g.get("chunk_grade", {}).get("metrics", {})
            if "mean_max_score" in cg:
                chunk_mean_scores.append(cg["mean_max_score"])
            covg = g.get("coverage_grade", {})
            if "information_sufficiency" in covg:
                coverage_scores.append(covg["information_sufficiency"])

        if plan_scores:
            mean_plan = statistics.mean(plan_scores)
            notes.append(f"Plan quality: mean={mean_plan:.2f}/5.0 (n={len(plan_scores)})")
        if chunk_mean_scores:
            mean_chunk = statistics.mean(chunk_mean_scores)
            notes.append(f"Chunk relevance: mean_max_score={mean_chunk:.2f}/3.0 (n={len(chunk_mean_scores)})")
        if coverage_scores:
            mean_cov = statistics.mean(coverage_scores)
            notes.append(f"Coverage sufficiency: mean={mean_cov:.2f}/5.0 (n={len(coverage_scores)})")

        # Find worst queries
        worst = [(qid, g) for qid, g in grades.items()
                 if g.get("coverage_grade", {}).get("information_sufficiency", 5) <= 2]
        if worst:
            ids = [str(qid) for qid, _ in worst[:5]]
            notes.append(f"Lowest coverage queries (≤2/5): ids={','.join(ids)} — prioritize investigation")

    # ── Funnel analysis ──────────────────────────────────────────────────
    funnel_rates = [t.get("rerank", {}).get("funnel_rate", 1.0) for t in valid_traces]
    if funnel_rates:
        mean_funnel = statistics.mean(funnel_rates)
        notes.append(f"Reranker funnel: mean={mean_funnel:.1%} of raw chunks survive to final output")

    return {
        "notes": notes,
        "by_query_type": qt_analysis,
        "by_difficulty": diff_analysis,
        "by_project": proj_analysis,
        "source_analysis": {"counts": source_analysis, "percentages": source_pct},
        "timing_analysis": {
            "all": _timing_stats(valid_traces),
            "outlier_count": len(outliers) if 'outliers' in dir() else 0,
        },
        "focus_analysis": {
            "total_unique_focuses": len(focus_hit_rates),
            "never_hit_count": len(never_hit),
        },
    }


# ── Helpers ──────────────────────────────────────────────────────────────

def _group_traces(traces, key_fn):
    groups = {}
    for t in traces:
        k = key_fn(t)
        groups.setdefault(k, []).append(t)
    return groups


def _timing_stats(traces):
    times = [t["timing"]["total_ms"] for t in traces if "timing" in t]
    if not times:
        return {}
    return {
        "mean_total_ms": round(statistics.mean(times)),
        "min_ms": min(times),
        "max_ms": max(times),
        "stddev_ms": round(statistics.stdev(times)) if len(times) > 1 else 0,
    }


def _chunk_stats(traces):
    finals = [t.get("rerank", {}).get("final_chunk_count", 0) for t in traces]
    raws = [t.get("retrieval", {}).get("raw_chunk_count", 0) for t in traces]
    if not finals:
        return {}
    return {
        "mean_final_chunks": round(statistics.mean(finals), 1),
        "mean_raw_chunks": round(statistics.mean(raws), 1),
    }
