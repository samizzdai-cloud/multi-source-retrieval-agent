"""CLI entry point — python -m eval.cli <command>

Commands:
    run       Execute experiment
    grade     Grade existing traces with LLM graders
    compare   A/B diff between two experiments
    analyze   Pattern analysis on one experiment
    report    Generate Markdown report
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def _parse_ids(s: str) -> List[int]:
    return [int(x.strip()) for x in s.split(",") if x.strip()]


# ─────────────────────────────────────────────────────────────────────────────
# run
# ─────────────────────────────────────────────────────────────────────────────

def cmd_run(args):
    from eval.config import ExperimentConfig, PRESETS
    from eval.dataset import load_queries
    from eval.runner import run_experiment

    # Load config
    if args.config_file:
        config = ExperimentConfig.load(Path(args.config_file))
    elif args.config in PRESETS:
        config = PRESETS[args.config]
    else:
        print(f"Unknown config preset: {args.config}. Available: {list(PRESETS.keys())}")
        sys.exit(1)

    if args.no_feishu:
        config.available_sources = [s for s in config.available_sources if s != "feishu"]

    if args.repeat:
        config.repeat = args.repeat

    # Load queries
    ids = _parse_ids(args.ids) if args.ids else None
    queries = load_queries(
        path=args.queries,
        ids=ids,
        sample=args.sample,
        difficulty=args.difficulty,
        query_type=args.query_type,
    )

    if not queries:
        print("No queries matched filters.")
        sys.exit(1)

    print(f"Running experiment '{config.name}' on {len(queries)} queries...")
    print(f"  Sources: {config.available_sources}")
    print(f"  Repeat: {config.repeat}")

    output_dir = Path(args.output) if args.output else None
    result = asyncio.run(run_experiment(config, queries, output_dir=output_dir))

    agg = result.get("aggregate", {})
    timing = agg.get("timing_ms", {})
    print(f"\nDone. {agg.get('successful', 0)}/{agg.get('total_queries', 0)} succeeded.")
    print(f"  Mean time: {timing.get('mean', 0)}ms | "
          f"Mean chunks: {agg.get('final_chunks', {}).get('mean', 0)}")
    print(f"  Output: {result.get('output_dir')}")


# ─────────────────────────────────────────────────────────────────────────────
# grade
# ─────────────────────────────────────────────────────────────────────────────

def cmd_grade(args):
    from eval.graders.plan_grader import grade_plan
    from eval.graders.chunk_grader import grade_chunks
    from eval.graders.coverage_grader import grade_coverage
    from eval.graders.sq_efficiency_grader import grade_sq_efficiency, grade_batch as grade_sq_batch

    result_dir = Path(args.result_dir)
    result_file = result_dir / "experiment_result.json"
    if not result_file.exists():
        print(f"experiment_result.json not found in {result_dir}")
        sys.exit(1)

    result = json.loads(result_file.read_text())
    traces = result.get("traces", [])

    if args.ids:
        id_set = set(_parse_ids(args.ids))
        traces = [t for t in traces if t.get("query_id") in id_set]

    if args.sample and args.sample < len(traces):
        import random
        traces = random.sample(traces, args.sample)

    print(f"Grading {len(traces)} traces with {args.graders}...")

    # Load existing grades for merge when re-grading subset
    grades_file = result_dir / "grades.json"
    existing_grades: Dict[str, Any] = {}
    if grades_file.exists() and args.ids:
        existing_grades = json.loads(grades_file.read_text())
        print(f"  (merging into existing grades.json with {len(existing_grades)} entries)")

    grades: Dict[int, Dict[str, Any]] = {}
    grader_list = args.graders.split(",") if args.graders != "all" else ["plan", "chunk", "coverage", "sq"]

    for t in traces:
        qid = t.get("query_id")
        if qid is None:
            continue
        g: Dict[str, Any] = {}

        if "plan" in grader_list:
            print(f"  [plan] q{qid}...", end=" ", flush=True)
            g["plan_grade"] = grade_plan(t, timeout=args.timeout)
            score = g["plan_grade"].get("composite_score", "?")
            print(f"score={score}")

        if "chunk" in grader_list:
            print(f"  [chunk] q{qid}...", end=" ", flush=True)
            g["chunk_grade"] = grade_chunks(t, timeout=args.timeout)
            ndcg = g["chunk_grade"].get("metrics", {}).get("ndcg_at_10", "?")
            print(f"nDCG@10={ndcg}")

        if "coverage" in grader_list:
            print(f"  [coverage] q{qid}...", end=" ", flush=True)
            g["coverage_grade"] = grade_coverage(t, timeout=args.timeout)
            suf = g["coverage_grade"].get("information_sufficiency", "?")
            print(f"sufficiency={suf}/5")

        if "sq" in grader_list:
            # sq grader is non-LLM, fast
            from dataclasses import asdict
            report = grade_sq_efficiency(t)
            g["sq_efficiency"] = asdict(report)
            print(f"  [sq] q{qid}... eff={report.efficiency_rate:.0%} zero={report.zero_rate:.0%}")

        grades[qid] = g

    # If sq grader was used, also generate batch summary
    if "sq" in grader_list:
        batch_report = grade_sq_batch(traces)
        grades["__sq_batch_summary__"] = {
            "total_queries": batch_report["total_queries"],
            "total_sq": batch_report["total_sq"],
            "aggregate": batch_report["aggregate"],
            "per_source": batch_report["per_source"],
        }
        print(f"\n[sq batch] efficiency={batch_report['aggregate']['efficiency_rate']:.0%} "
              f"zero={batch_report['aggregate']['zero_rate']:.0%} "
              f"actionable={batch_report['aggregate']['actionable_zero_rate']:.0%}")

    # Save grades (merge with existing if re-grading subset)
    if existing_grades:
        for qid_key, grade_data in grades.items():
            existing_entry = existing_grades.get(str(qid_key), {})
            existing_entry.update(grade_data)
            existing_grades[str(qid_key)] = existing_entry
        final_grades = existing_grades
    else:
        final_grades = grades
    grades_file.write_text(json.dumps(final_grades, indent=2, ensure_ascii=False, default=str))
    print(f"\nGrades saved to {grades_file}")


# ─────────────────────────────────────────────────────────────────────────────
# compare
# ─────────────────────────────────────────────────────────────────────────────

def cmd_compare(args):
    from eval.comparator import compare_experiments, save_comparison

    result_a = json.loads((Path(args.a) / "experiment_result.json").read_text())
    result_b = json.loads((Path(args.b) / "experiment_result.json").read_text())

    # Load grades if available
    grades_a = _load_grades(Path(args.a))
    grades_b = _load_grades(Path(args.b))

    report = compare_experiments(result_a, result_b, grades_a, grades_b)

    out = Path(args.output) if args.output else Path(args.b) / "comparison.json"
    save_comparison(report, out)

    print(f"Comparison: {report.get('config_a')} vs {report.get('config_b')}")
    print(f"  Improved: {report.get('improved')} | Degraded: {report.get('degraded')} | "
          f"Unchanged: {report.get('unchanged')}")
    print(f"  Timing delta: {report.get('timing_delta_ms')}ms")

    # [P2] Print chunk quality comparison
    q = report.get("quality", {})
    if q:
        print(f"\n  Chunk Quality:")
        print(f"    {'':>16} | {'A':>8} | {'B':>8} | {'Δ':>8}")
        print(f"    {'-'*45}")
        print(f"    {'score_mean':>16} | {q.get('score_mean_a',0):>8.3f} | {q.get('score_mean_b',0):>8.3f} | {q.get('score_mean_delta',0):>+8.4f}")
        print(f"    {'score_p25':>16} | {q.get('score_p25_a',0):>8.3f} | {q.get('score_p25_b',0):>8.3f} | {q.get('score_p25_delta',0):>+8.4f}")
        print(f"    {'score≥0.9 %':>16} | {q.get('score_hi_pct_a',0):>7.1f}% | {q.get('score_hi_pct_b',0):>7.1f}% | {q.get('score_hi_pct_b',0)-q.get('score_hi_pct_a',0):>+7.1f}%")
        print(f"    {'feishu %':>16} | {q.get('feishu_pct_a',0):>7.1f}% | {q.get('feishu_pct_b',0):>7.1f}% | {q.get('feishu_pct_delta',0):>+7.1f}%")

    print(f"\n  Saved to {out}")

    # Also generate markdown
    from eval.report import generate_comparison_report, save_report
    md = generate_comparison_report(report)
    md_path = out.with_suffix(".md")
    save_report(md, md_path)
    print(f"  Report: {md_path}")


# ─────────────────────────────────────────────────────────────────────────────
# analyze
# ─────────────────────────────────────────────────────────────────────────────

def cmd_analyze(args):
    from eval.analyzer import analyze_experiment

    result_dir = Path(args.result_dir)
    result = json.loads((result_dir / "experiment_result.json").read_text())
    traces = result.get("traces", [])
    grades = _load_grades(result_dir)

    analysis = analyze_experiment(traces, grades)

    out = result_dir / "analysis.json"
    out.write_text(json.dumps(analysis, indent=2, ensure_ascii=False, default=str))

    print(f"Analysis for '{result.get('config', {}).get('name', '?')}':")
    for note in analysis.get("notes", []):
        print(f"  • {note}")
    print(f"\nFull analysis: {out}")

    # Also generate report
    from eval.report import generate_experiment_report, save_report
    md = generate_experiment_report(result, grades, analysis)
    md_path = result_dir / "report.md"
    save_report(md, md_path)
    print(f"Report: {md_path}")


# ─────────────────────────────────────────────────────────────────────────────
# report
# ─────────────────────────────────────────────────────────────────────────────

def cmd_report(args):
    from eval.report import generate_experiment_report, save_report

    result_dir = Path(args.result_dir)
    result = json.loads((result_dir / "experiment_result.json").read_text())
    grades = _load_grades(result_dir)
    analysis = None
    analysis_file = result_dir / "analysis.json"
    if analysis_file.exists():
        analysis = json.loads(analysis_file.read_text())

    md = generate_experiment_report(result, grades, analysis)
    out = Path(args.output) if args.output else result_dir / "report.md"
    save_report(md, out)
    print(f"Report written to {out}")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_grades(result_dir: Path) -> Optional[Dict[int, Dict[str, Any]]]:
    grades_file = result_dir / "grades.json"
    if grades_file.exists():
        raw = json.loads(grades_file.read_text())
        # Keys may be strings — convert to int; skip non-query keys like __sq_batch_summary__
        result = {}
        for k, v in raw.items():
            try:
                result[int(k)] = v
            except (ValueError, TypeError):
                pass
        return result
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(prog="eval", description="RAG retrieval eval framework")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = sub.add_parser("run", help="Execute experiment")
    p_run.add_argument("--config", default="baseline", help="Preset name or 'baseline'")
    p_run.add_argument("--config-file", default=None, help="Path to config JSON (overrides --config)")
    p_run.add_argument("--queries", default=None, help="Path to eval queries JSON")
    p_run.add_argument("--ids", default=None, help="Comma-separated query IDs")
    p_run.add_argument("--sample", type=int, default=None, help="Random sample of N queries")
    p_run.add_argument("--difficulty", default=None, help="Filter by difficulty")
    p_run.add_argument("--query-type", default=None, help="Filter by query_type")
    p_run.add_argument("--no-feishu", action="store_true", help="Exclude feishu source")
    p_run.add_argument("--repeat", type=int, default=None, help="Repeat each query N times")
    p_run.add_argument("--output", default=None, help="Output directory")
    p_run.set_defaults(func=cmd_run)

    # grade
    p_grade = sub.add_parser("grade", help="Grade existing traces")
    p_grade.add_argument("result_dir", help="Path to experiment output directory")
    p_grade.add_argument("--ids", default=None, help="Comma-separated query IDs to grade")
    p_grade.add_argument("--sample", type=int, default=None, help="Random sample of N traces")
    p_grade.add_argument("--graders", default="all", help="Comma-separated: plan,chunk,coverage or 'all'")
    p_grade.add_argument("--timeout", type=float, default=90.0, help="Per-grader timeout")
    p_grade.set_defaults(func=cmd_grade)

    # compare
    p_cmp = sub.add_parser("compare", help="A/B diff between two experiments")
    p_cmp.add_argument("--a", required=True, help="Path to baseline experiment dir")
    p_cmp.add_argument("--b", required=True, help="Path to experiment dir")
    p_cmp.add_argument("--output", default=None, help="Output path for comparison JSON")
    p_cmp.set_defaults(func=cmd_compare)

    # analyze
    p_ana = sub.add_parser("analyze", help="Pattern analysis on one experiment")
    p_ana.add_argument("result_dir", help="Path to experiment output directory")
    p_ana.set_defaults(func=cmd_analyze)

    # report
    p_rep = sub.add_parser("report", help="Generate Markdown report")
    p_rep.add_argument("result_dir", help="Path to experiment output directory")
    p_rep.add_argument("--output", default=None, help="Output file path")
    p_rep.set_defaults(func=cmd_report)

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    args.func(args)


if __name__ == "__main__":
    main()
