"""Report generator — Markdown reports for experiments and comparisons."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def generate_experiment_report(
    result: Dict[str, Any],
    grades: Optional[Dict[int, Dict[str, Any]]] = None,
    analysis: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate a Markdown report for a single experiment."""
    config = result.get("config", {})
    agg = result.get("aggregate", {})
    traces = result.get("traces", [])
    grades = grades or {}

    lines = [
        f"# Experiment Report: {config.get('name', 'unknown')}",
        f"",
        f"**Timestamp**: {result.get('timestamp', 'N/A')}",
        f"**Description**: {config.get('description', 'N/A')}",
        f"**Queries**: {agg.get('total_queries', 0)} ({agg.get('successful', 0)} ok, {agg.get('failed', 0)} failed)",
        f"**Sources**: {', '.join(config.get('available_sources', []))}",
        f"",
        f"## Aggregate Metrics",
        f"",
        f"| Metric | Mean | Min | Max | StdDev |",
        f"|--------|------|-----|-----|--------|",
    ]

    for key in ("timing_ms", "final_chunks", "raw_chunks"):
        s = agg.get(key, {})
        lines.append(
            f"| {key} | {s.get('mean', 0)} | {s.get('min', 0)} | "
            f"{s.get('max', 0)} | {s.get('stddev', 0)} |"
        )

    # ── Per-query table ──────────────────────────────────────────────────
    lines.extend([
        f"",
        f"## Per-Query Summary",
        f"",
        f"| ID | Query | Total ms | Plan ms | Final | Raw | Type |",
        f"|----|-------|----------|---------|-------|-----|------|",
    ])

    for t in sorted(traces, key=lambda x: x.get("query_id", 0)):
        if "timing" not in t:
            continue
        tm = t["timing"]
        rk = t.get("rerank", {})
        rv = t.get("retrieval", {})
        qt = t.get("plan", {}).get("query_type", "?")
        lines.append(
            f"| {t['query_id']} | {t.get('query', '')[:35]} | "
            f"{tm['total_ms']} | {tm['plan_ms']} | "
            f"{rk.get('final_chunk_count', 0)} | {rv.get('raw_chunk_count', 0)} | {qt} |"
        )

    # ── Grade summary (if available) ─────────────────────────────────────
    if grades:
        lines.extend([
            f"",
            f"## Grade Summary",
            f"",
            f"| ID | Plan Score | Chunk nDCG@10 | Coverage | Feasibility |",
            f"|----|------------|---------------|----------|-------------|",
        ])
        for qid in sorted(grades.keys()):
            g = grades[qid]
            pg = g.get("plan_grade", {})
            cg = g.get("chunk_grade", {}).get("metrics", {})
            covg = g.get("coverage_grade", {})
            lines.append(
                f"| {qid} | {pg.get('composite_score', 'N/A')} | "
                f"{cg.get('ndcg_at_10', 'N/A')} | "
                f"{covg.get('information_sufficiency', 'N/A')}/5 | "
                f"{covg.get('answer_feasibility', 'N/A')} |"
            )

    # ── Analysis notes ───────────────────────────────────────────────────
    if analysis:
        notes = analysis.get("notes", [])
        if notes:
            lines.extend([f"", f"## Analysis Notes", f""])
            for note in notes:
                lines.append(f"- {note}")

    return "\n".join(lines)


def generate_comparison_report(comparison: Dict[str, Any]) -> str:
    """Generate a Markdown report for an A/B comparison."""
    lines = [
        f"# A/B Comparison: {comparison.get('config_a')} vs {comparison.get('config_b')}",
        f"",
        f"**Total queries**: {comparison.get('total_queries', 0)}",
        f"**Improved**: {comparison.get('improved', 0)} | "
        f"**Degraded**: {comparison.get('degraded', 0)} | "
        f"**Unchanged**: {comparison.get('unchanged', 0)}",
        f"**Timing delta (mean)**: {comparison.get('timing_delta_ms', 0)}ms",
        f"",
        f"## Per-Query Diff",
        f"",
        f"| ID | Query | Verdict | Time Δ | Chunks Δ | Grade Δ |",
        f"|----|-------|---------|--------|----------|---------|",
    ]

    for pq in comparison.get("per_query", []):
        td = pq.get("timing_delta", {}).get("total_ms", 0)
        vd = pq.get("volume_diff", {}).get("final_chunks_delta", 0)
        gd = pq.get("grade_delta", "N/A")
        lines.append(
            f"| {pq['query_id']} | {pq.get('query', '')[:30]} | "
            f"{pq.get('verdict', '?')} | {td:+d}ms | {vd:+d} | {gd} |"
        )

    return "\n".join(lines)


def save_report(content: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
