"""
Step A5-A7: Analyze graded dataset → derive optimal thresholds.

Outputs:
  1. L2 score vs LLM grade distribution per query_type
  2. Optimal noise_threshold per query_type
  3. Focus satisfaction analysis
  4. Early-stop simulation
  5. Summary with threshold recommendations

Usage:
    python -m eval.threshold_calibration.analyze_threshold

Input:  .eval_output/threshold_calibration/graded_dataset.json
        .eval_output/threshold_calibration/traces/  (for early-stop simulation)
Output: .eval_output/threshold_calibration/analysis_report.json
        .eval_output/threshold_calibration/analysis_summary.txt
"""

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

OUTPUT_DIR = ROOT / ".eval_output" / "threshold_calibration"
GRADED_PATH = OUTPUT_DIR / "graded_dataset.json"
TRACES_DIR = OUTPUT_DIR / "traces"
REPORT_PATH = OUTPUT_DIR / "analysis_report.json"
SUMMARY_PATH = OUTPUT_DIR / "analysis_summary.txt"


def load_graded():
    if not GRADED_PATH.exists():
        sys.exit(f"ERROR: {GRADED_PATH} not found. Run threshold_grader.py first.")
    data = json.loads(GRADED_PATH.read_text())
    valid = [d for d in data if d.get("overall_grade", 0) > 0]
    print(f"Loaded {len(valid)} valid graded entries (out of {len(data)})")
    return valid


def analyze_score_vs_grade(graded: list) -> dict:
    """For each query_type: compute score buckets vs LLM grade distribution."""
    buckets = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    results = {}

    for qt in ["data", "concept", "mixed", "all"]:
        entries = graded if qt == "all" else [g for g in graded if g["query_type"] == qt]
        if not entries:
            continue

        bucket_data = []
        for i in range(len(buckets) - 1):
            lo, hi = buckets[i], buckets[i + 1]
            in_bucket = [g for g in entries if lo <= g["chunk_score"] < hi]
            if not in_bucket:
                bucket_data.append({
                    "range": f"[{lo:.1f}, {hi:.1f})",
                    "count": 0,
                    "avg_overall": 0,
                    "avg_semantic": 0,
                    "avg_keyword": 0,
                    "pct_grade_ge4": 0,
                    "pct_grade_ge3": 0,
                })
                continue

            grades = [g["overall_grade"] for g in in_bucket]
            sem_grades = [g["semantic_relevance"] for g in in_bucket]
            kw_grades = [g["keyword_relevance"] for g in in_bucket]
            bucket_data.append({
                "range": f"[{lo:.1f}, {hi:.1f})",
                "count": len(in_bucket),
                "avg_overall": round(sum(grades) / len(grades), 2),
                "avg_semantic": round(sum(sem_grades) / len(sem_grades), 2),
                "avg_keyword": round(sum(kw_grades) / len(kw_grades), 2),
                "pct_grade_ge4": round(sum(1 for g in grades if g >= 4) / len(grades) * 100, 1),
                "pct_grade_ge3": round(sum(1 for g in grades if g >= 3) / len(grades) * 100, 1),
            })

        results[qt] = bucket_data

    return results


def find_optimal_threshold(graded: list) -> dict:
    """Find L2 score threshold where grade>=4 proportion exceeds 80% per query_type."""
    thresholds = {}
    candidates = [round(x * 0.05, 2) for x in range(1, 20)]  # 0.05 to 0.95

    for qt in ["data", "concept", "mixed", "all"]:
        entries = graded if qt == "all" else [g for g in graded if g["query_type"] == qt]
        if not entries:
            continue

        best = {"threshold": 0.33, "pct_ge4": 0, "pct_ge3": 0, "n_above": 0, "n_total": len(entries)}

        for t in candidates:
            above = [g for g in entries if g["chunk_score"] >= t]
            if not above:
                continue
            pct4 = sum(1 for g in above if g["overall_grade"] >= 4) / len(above)
            pct3 = sum(1 for g in above if g["overall_grade"] >= 3) / len(above)

            # Target: >=80% of chunks above threshold should have grade>=4
            if pct4 >= 0.80 and len(above) >= 3:
                if t > best["threshold"] or (t == best["threshold"] and pct4 > best["pct_ge4"]):
                    best = {
                        "threshold": t,
                        "pct_ge4": round(pct4 * 100, 1),
                        "pct_ge3": round(pct3 * 100, 1),
                        "n_above": len(above),
                        "n_total": len(entries),
                    }
            # If no threshold reaches 80%, find the one that maximizes pct_ge4 × n_above
            elif not best["pct_ge4"]:
                score = pct4 * len(above)
                best_score = best["pct_ge4"] / 100 * best["n_above"] if best["pct_ge4"] else 0
                if score > best_score:
                    best = {
                        "threshold": t,
                        "pct_ge4": round(pct4 * 100, 1),
                        "pct_ge3": round(pct3 * 100, 1),
                        "n_above": len(above),
                        "n_total": len(entries),
                    }

        thresholds[qt] = best

    return thresholds


def analyze_focus_satisfaction(graded: list) -> dict:
    """How many grade>=4 chunks per focus does each query have?"""
    # Group by (query_id, focus)
    groups = defaultdict(list)
    for g in graded:
        groups[(g["query_id"], g["focus"])].append(g)

    stats = []
    for (qid, focus), entries in sorted(groups.items()):
        grades = [g["overall_grade"] for g in entries]
        scores = [g["chunk_score"] for g in entries]
        n_ge4 = sum(1 for g in grades if g >= 4)
        n_ge3 = sum(1 for g in grades if g >= 3)
        qt = entries[0]["query_type"]
        stats.append({
            "query_id": qid,
            "query_type": qt,
            "focus": focus,
            "total_chunks": len(entries),
            "n_grade_ge4": n_ge4,
            "n_grade_ge3": n_ge3,
            "avg_grade": round(sum(grades) / len(grades), 2),
            "avg_score": round(sum(scores) / len(scores), 3),
            "max_score": round(max(scores), 3),
        })

    # Recommendation: how many grade>=4 chunks per focus to require for "satisfied"
    satisfied_analysis = {}
    for qt in ["data", "concept", "mixed"]:
        qt_stats = [s for s in stats if s["query_type"] == qt]
        if not qt_stats:
            continue
        ge4_counts = [s["n_grade_ge4"] for s in qt_stats]
        ge3_counts = [s["n_grade_ge3"] for s in qt_stats]
        satisfied_analysis[qt] = {
            "focuses_evaluated": len(qt_stats),
            "median_ge4_per_focus": sorted(ge4_counts)[len(ge4_counts) // 2],
            "median_ge3_per_focus": sorted(ge3_counts)[len(ge3_counts) // 2],
            "min_ge4": min(ge4_counts),
            "max_ge4": max(ge4_counts),
            "pct_with_zero_ge4": round(sum(1 for c in ge4_counts if c == 0) / len(ge4_counts) * 100, 1),
        }

    return {"per_focus_stats": stats, "satisfaction_analysis": satisfied_analysis}


def compute_global_averages(graded: list) -> dict:
    """Compute overall averages by query_type."""
    results = {}
    for qt in ["data", "concept", "mixed", "all"]:
        entries = graded if qt == "all" else [g for g in graded if g["query_type"] == qt]
        if not entries:
            continue

        results[qt] = {
            "n_entries": len(entries),
            "avg_overall_grade": round(sum(g["overall_grade"] for g in entries) / len(entries), 3),
            "avg_semantic_relevance": round(sum(g["semantic_relevance"] for g in entries) / len(entries), 3),
            "avg_keyword_relevance": round(sum(g["keyword_relevance"] for g in entries) / len(entries), 3),
            "avg_chunk_score_L2": round(sum(g["chunk_score"] for g in entries) / len(entries), 3),
            "pct_grade_ge4": round(sum(1 for g in entries if g["overall_grade"] >= 4) / len(entries) * 100, 1),
            "pct_grade_ge3": round(sum(1 for g in entries if g["overall_grade"] >= 3) / len(entries) * 100, 1),
        }

    return results


def generate_summary(report: dict) -> str:
    """Generate human-readable summary with threshold recommendations."""
    lines = []
    lines.append("=" * 70)
    lines.append("  THRESHOLD CALIBRATION ANALYSIS REPORT")
    lines.append("=" * 70)
    lines.append("")

    # 1. Global averages
    lines.append("1. GLOBAL AVERAGES BY QUERY TYPE")
    lines.append("-" * 50)
    avgs = report.get("global_averages", {})
    for qt in ["data", "concept", "mixed", "all"]:
        a = avgs.get(qt)
        if not a:
            continue
        lines.append(f"  [{qt.upper()}] (n={a['n_entries']})")
        lines.append(f"    Avg semantic relevance : {a['avg_semantic_relevance']:.3f}")
        lines.append(f"    Avg keyword relevance  : {a['avg_keyword_relevance']:.3f}")
        lines.append(f"    Avg overall grade      : {a['avg_overall_grade']:.3f}")
        lines.append(f"    Avg L2 chunk score     : {a['avg_chunk_score_L2']:.3f}")
        lines.append(f"    % grade >= 4           : {a['pct_grade_ge4']}%")
        lines.append(f"    % grade >= 3           : {a['pct_grade_ge3']}%")
        lines.append("")

    # 2. Score vs Grade distribution
    lines.append("2. L2 SCORE vs LLM GRADE DISTRIBUTION")
    lines.append("-" * 50)
    dist = report.get("score_vs_grade", {})
    for qt in ["data", "concept", "mixed"]:
        buckets = dist.get(qt, [])
        if not buckets:
            continue
        lines.append(f"  [{qt.upper()}]")
        lines.append(f"  {'Score Range':<15} {'Count':>6} {'AvgGrade':>9} {'AvgSem':>7} {'AvgKW':>6} {'%≥4':>5} {'%≥3':>5}")
        for b in buckets:
            if b["count"] == 0:
                continue
            lines.append(
                f"  {b['range']:<15} {b['count']:>6} {b['avg_overall']:>9.2f} "
                f"{b['avg_semantic']:>7.2f} {b['avg_keyword']:>6.2f} "
                f"{b['pct_grade_ge4']:>5.1f} {b['pct_grade_ge3']:>5.1f}"
            )
        lines.append("")

    # 3. Optimal thresholds
    lines.append("3. RECOMMENDED THRESHOLDS")
    lines.append("-" * 50)
    thresholds = report.get("optimal_thresholds", {})
    for qt in ["data", "concept", "mixed", "all"]:
        t = thresholds.get(qt)
        if not t:
            continue
        lines.append(f"  [{qt.upper()}]")
        lines.append(f"    noise_threshold = {t['threshold']:.2f}")
        lines.append(f"    → {t['n_above']}/{t['n_total']} chunks pass ({t['pct_ge4']}% grade≥4, {t['pct_ge3']}% grade≥3)")
        lines.append("")

    # 4. Focus satisfaction
    lines.append("4. FOCUS SATISFACTION ANALYSIS")
    lines.append("-" * 50)
    sat = report.get("focus_satisfaction", {}).get("satisfaction_analysis", {})
    for qt in ["data", "concept", "mixed"]:
        s = sat.get(qt)
        if not s:
            continue
        lines.append(f"  [{qt.upper()}] ({s['focuses_evaluated']} focuses evaluated)")
        lines.append(f"    Median grade≥4 chunks per focus: {s['median_ge4_per_focus']}")
        lines.append(f"    Median grade≥3 chunks per focus: {s['median_ge3_per_focus']}")
        lines.append(f"    Min/Max grade≥4 per focus: {s['min_ge4']} / {s['max_ge4']}")
        lines.append(f"    % focuses with ZERO grade≥4: {s['pct_with_zero_ge4']}%")
        lines.append("")

    # 5. Threshold recommendations summary
    lines.append("5. THRESHOLD RECOMMENDATIONS SUMMARY")
    lines.append("=" * 50)

    all_t = thresholds.get("all", {})
    data_t = thresholds.get("data", {})
    concept_t = thresholds.get("concept", {})
    mixed_t = thresholds.get("mixed", {})

    lines.append(f"  GLOBAL noise_threshold    : {all_t.get('threshold', '?')}")
    lines.append(f"  DATA noise_threshold      : {data_t.get('threshold', '?')}")
    lines.append(f"  CONCEPT noise_threshold   : {concept_t.get('threshold', '?')}")
    lines.append(f"  MIXED noise_threshold     : {mixed_t.get('threshold', '?')}")
    lines.append("")

    sat_data = sat.get("data", {})
    sat_concept = sat.get("concept", {})
    lines.append(f"  Suggested min_per_focus (data)   : {max(1, sat_data.get('median_ge4_per_focus', 2))}")
    lines.append(f"  Suggested min_per_focus (concept): {max(1, sat_concept.get('median_ge4_per_focus', 2))}")
    lines.append(f"  Suggested min_relevance_score    : {all_t.get('threshold', 0.45)}")
    lines.append("")
    lines.append("  NOTE: These are L2 (DocPool hybrid) thresholds.")
    lines.append("  The global reranker (L4) has its own scoring.")
    lines.append("  Use these for DocPool noise_threshold + EvaluationLayer early-stop.")

    return "\n".join(lines)


def main():
    graded = load_graded()

    print("\nAnalyzing score vs grade distribution...")
    score_vs_grade = analyze_score_vs_grade(graded)

    print("Finding optimal thresholds...")
    thresholds = find_optimal_threshold(graded)

    print("Analyzing focus satisfaction...")
    focus_sat = analyze_focus_satisfaction(graded)

    print("Computing global averages...")
    global_avgs = compute_global_averages(graded)

    report = {
        "global_averages": global_avgs,
        "score_vs_grade": score_vs_grade,
        "optimal_thresholds": thresholds,
        "focus_satisfaction": focus_sat,
    }

    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Report saved → {REPORT_PATH}")

    summary = generate_summary(report)
    SUMMARY_PATH.write_text(summary)
    print(f"Summary saved → {SUMMARY_PATH}")
    print()
    print(summary)


if __name__ == "__main__":
    main()
