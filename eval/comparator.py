"""Comparator — A/B diff between two experiment results.

For each query, compares plan quality, chunk quality, coverage, and timing
between config A (baseline) and config B (experiment).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def compare_experiments(
    result_a: Dict[str, Any],
    result_b: Dict[str, Any],
    grades_a: Optional[Dict[str, Any]] = None,
    grades_b: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compare two experiment results and their grades.

    Args:
        result_a: experiment_result from runner (baseline)
        result_b: experiment_result from runner (experiment)
        grades_a: {query_id: {plan_grade, chunk_grade, coverage_grade}} for A
        grades_b: same for B

    Returns:
        ComparisonReport dict
    """
    config_a = result_a.get("config", {}).get("name", "A")
    config_b = result_b.get("config", {}).get("name", "B")

    traces_a = {t["query_id"]: t for t in result_a.get("traces", []) if "query_id" in t}
    traces_b = {t["query_id"]: t for t in result_b.get("traces", []) if "query_id" in t}

    grades_a = grades_a or {}
    grades_b = grades_b or {}

    common_ids = sorted(set(traces_a.keys()) & set(traces_b.keys()))

    per_query: List[Dict[str, Any]] = []
    improved = degraded = unchanged = 0

    for qid in common_ids:
        ta = traces_a[qid]
        tb = traces_b[qid]
        ga = grades_a.get(qid, {})
        gb = grades_b.get(qid, {})

        diff = _diff_query(ta, tb, ga, gb)
        diff["query_id"] = qid
        diff["query"] = ta.get("query", "")[:60]

        verdict = diff.get("verdict", "unchanged")
        if verdict == "improved":
            improved += 1
        elif verdict == "degraded":
            degraded += 1
        else:
            unchanged += 1

        per_query.append(diff)

    # Aggregate timing diff
    timing_a = [t["timing"]["total_ms"] for t in traces_a.values() if "timing" in t]
    timing_b = [t["timing"]["total_ms"] for t in traces_b.values() if "timing" in t]
    mean_a = sum(timing_a) / max(1, len(timing_a))
    mean_b = sum(timing_b) / max(1, len(timing_b))

    # [P2] Aggregate chunk quality metrics
    all_scores_a, all_scores_b = [], []
    fe_total_a, fe_total_b, chunks_a, chunks_b = 0, 0, 0, 0
    for qid in common_ids:
        for c in traces_a[qid].get("final_chunks", []):
            s = c.get("score")
            if isinstance(s, (int, float)):
                all_scores_a.append(s)
            if c.get("source_tool") == "feishu":
                fe_total_a += 1
            chunks_a += 1
        for c in traces_b[qid].get("final_chunks", []):
            s = c.get("score")
            if isinstance(s, (int, float)):
                all_scores_b.append(s)
            if c.get("source_tool") == "feishu":
                fe_total_b += 1
            chunks_b += 1
    all_scores_a.sort()
    all_scores_b.sort()
    na, nb = len(all_scores_a), len(all_scores_b)

    quality_aggregate = {
        "score_mean_a": round(sum(all_scores_a) / max(1, na), 4),
        "score_mean_b": round(sum(all_scores_b) / max(1, nb), 4),
        "score_p25_a": round(all_scores_a[na // 4], 4) if na >= 4 else 0,
        "score_p25_b": round(all_scores_b[nb // 4], 4) if nb >= 4 else 0,
        "score_hi_pct_a": round(sum(1 for s in all_scores_a if s >= 0.9) * 100 / max(1, na), 1),
        "score_hi_pct_b": round(sum(1 for s in all_scores_b if s >= 0.9) * 100 / max(1, nb), 1),
        "feishu_pct_a": round(fe_total_a * 100 / max(1, chunks_a), 1),
        "feishu_pct_b": round(fe_total_b * 100 / max(1, chunks_b), 1),
    }
    quality_aggregate["score_mean_delta"] = round(
        quality_aggregate["score_mean_b"] - quality_aggregate["score_mean_a"], 4
    )
    quality_aggregate["score_p25_delta"] = round(
        quality_aggregate["score_p25_b"] - quality_aggregate["score_p25_a"], 4
    )
    quality_aggregate["feishu_pct_delta"] = round(
        quality_aggregate["feishu_pct_b"] - quality_aggregate["feishu_pct_a"], 1
    )

    return {
        "config_a": config_a,
        "config_b": config_b,
        "total_queries": len(common_ids),
        "improved": improved,
        "degraded": degraded,
        "unchanged": unchanged,
        "timing_delta_ms": round(mean_b - mean_a, 1),
        "quality": quality_aggregate,
        "per_query": per_query,
    }


def _diff_query(
    ta: Dict, tb: Dict,
    ga: Dict, gb: Dict,
) -> Dict[str, Any]:
    """Per-query diff between A and B."""
    diff: Dict[str, Any] = {}

    # ── Timing diff ──────────────────────────────────────────────────────
    t_a = ta.get("timing", {})
    t_b = tb.get("timing", {})
    diff["timing_delta"] = {
        k: t_b.get(k, 0) - t_a.get(k, 0)
        for k in ("plan_ms", "retrieve_ms", "rerank_ms", "total_ms")
    }

    # ── Plan diff ────────────────────────────────────────────────────────
    plan_a = ta.get("plan", {})
    plan_b = tb.get("plan", {})
    fgs_a = plan_a.get("focus_groups", [])
    fgs_b = plan_b.get("focus_groups", [])
    focuses_a = [f for fg in fgs_a for f in fg.get("focuses", [])]
    focuses_b = [f for fg in fgs_b for f in fg.get("focuses", [])]
    diff["plan_diff"] = {
        "focus_count_delta": len(focuses_b) - len(focuses_a),
        "query_type_a": plan_a.get("query_type"),
        "query_type_b": plan_b.get("query_type"),
        "focus_group_count_delta": len(fgs_b) - len(fgs_a),
    }

    # ── Chunk volume diff ────────────────────────────────────────────────
    r_a = ta.get("rerank", {})
    r_b = tb.get("rerank", {})
    diff["volume_diff"] = {
        "final_chunks_delta": r_b.get("final_chunk_count", 0) - r_a.get("final_chunk_count", 0),
        "raw_chunks_a": ta.get("retrieval", {}).get("raw_chunk_count", 0),
        "raw_chunks_b": tb.get("retrieval", {}).get("raw_chunk_count", 0),
    }

    # ── Chunk quality diff [P2] ──────────────────────────────────────────
    diff["quality_diff"] = {
        **_chunk_quality_metrics(ta, "a"),
        **_chunk_quality_metrics(tb, "b"),
    }
    qa = diff["quality_diff"]
    qa["score_mean_delta"] = round(qa.get("score_mean_b", 0) - qa.get("score_mean_a", 0), 4)
    qa["score_p25_delta"] = round(qa.get("score_p25_b", 0) - qa.get("score_p25_a", 0), 4)
    qa["feishu_pct_delta"] = round(qa.get("feishu_pct_b", 0) - qa.get("feishu_pct_a", 0), 1)

    # ── Grade diff (if available) ────────────────────────────────────────
    score_a = _composite_grade_score(ga)
    score_b = _composite_grade_score(gb)
    diff["grade_delta"] = round(score_b - score_a, 2) if score_a is not None and score_b is not None else None

    # ── Verdict ──────────────────────────────────────────────────────────
    if diff["grade_delta"] is not None:
        if diff["grade_delta"] > 0.3:
            diff["verdict"] = "improved"
        elif diff["grade_delta"] < -0.3:
            diff["verdict"] = "degraded"
        else:
            diff["verdict"] = "unchanged"
    else:
        # Fallback: use timing + volume heuristic
        t_delta = diff["timing_delta"].get("total_ms", 0)
        v_delta = diff["volume_diff"].get("final_chunks_delta", 0)
        if t_delta < -500 and v_delta >= 0:
            diff["verdict"] = "improved"
        elif t_delta > 1000 and v_delta <= 0:
            diff["verdict"] = "degraded"
        else:
            diff["verdict"] = "unchanged"

    return diff


def _composite_grade_score(grades: Dict) -> Optional[float]:
    """Extract a single composite score from per-query grades."""
    scores = []
    # Plan grade composite
    pg = grades.get("plan_grade", {})
    if "composite_score" in pg:
        scores.append(pg["composite_score"])
    # Chunk grade mean_max_score
    cg = grades.get("chunk_grade", {})
    metrics = cg.get("metrics", {})
    if "mean_max_score" in metrics:
        scores.append(metrics["mean_max_score"])
    # Coverage sufficiency
    covg = grades.get("coverage_grade", {})
    if "information_sufficiency" in covg:
        scores.append(covg["information_sufficiency"])

    return sum(scores) / len(scores) if scores else None


def _chunk_quality_metrics(trace: Dict, suffix: str) -> Dict[str, Any]:
    """Extract final chunk quality metrics from a trace."""
    chunks = trace.get("final_chunks", [])
    scores = sorted(
        [c["score"] for c in chunks if isinstance(c.get("score"), (int, float))]
    )
    n = len(scores)
    fe = sum(1 for c in chunks if c.get("source_tool") == "feishu")
    total = max(1, len(chunks))
    return {
        f"score_mean_{suffix}": round(sum(scores) / max(1, n), 4) if scores else 0,
        f"score_p25_{suffix}": round(scores[n // 4], 4) if n >= 4 else (round(scores[0], 4) if scores else 0),
        f"score_hi_pct_{suffix}": round(sum(1 for s in scores if s >= 0.9) * 100 / max(1, n), 1),
        f"feishu_pct_{suffix}": round(fe * 100 / total, 1),
        f"feishu_count_{suffix}": fe,
        f"final_count_{suffix}": total,
    }


def save_comparison(report: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
