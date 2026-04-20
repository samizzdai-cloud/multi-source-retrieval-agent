"""S3.c 基线对比脚本。

把最近一次 tests/exp_phase4b_eval.py 的结果（tests/exp_phase4b_eval_result.json）
与 eval/baselines.json 里冻结的历史基线做差分，输出 delta 表 + 回归判定。

用法
----
# 对最新 result vs 最后一个基线
python eval/compare_baseline.py

# 指定基线
python eval/compare_baseline.py --baseline sprint2_post_fix

# 指定 result 文件
python eval/compare_baseline.py --current tests/other_run.json

# 把当前 result 冻结成新基线追加到 baselines.json
python eval/compare_baseline.py --save-as sprint3_post_s3a --notes "After S3.a unanswerable fix"

退出码
------
0 = 全部 OK
1 = 有 REGRESSION（超阈值）
2 = 使用错误 / 文件缺失
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINES_PATH = os.path.join(_ROOT, "eval", "baselines.json")
DEFAULT_CURRENT = os.path.join(_ROOT, "tests", "exp_phase4b_eval_result.json")

# exp_phase4b_eval.py 的 result 字段 → baselines.json 里的 metric 名
FIELD_MAP = {
    "merged_chunks":       "chunks",
    "critical_density":    "crit_ρ",
    "important_density":   "imp_ρ",
    "focus_coverage_rate": "cov",
    "dup_rate":            "dup",
    "elapsed_s":           "elapsed_s",
}


# ─────────────────────────────────────────────────────────────────────
# 加载 & 聚合
# ─────────────────────────────────────────────────────────────────────
def _load_baselines() -> Dict[str, Any]:
    if not os.path.exists(BASELINES_PATH):
        print(f"ERROR: baselines file not found: {BASELINES_PATH}", file=sys.stderr)
        sys.exit(2)
    with open(BASELINES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _aggregate_current(current_path: str) -> Tuple[Dict[str, Dict[str, float]], int, int]:
    """读 exp_phase4b_eval_result.json，按 label 聚合成 {label: {metric: mean}}。

    Returns: (metrics_by_label, n_queries, seed)
    """
    if not os.path.exists(current_path):
        print(f"ERROR: current result not found: {current_path}", file=sys.stderr)
        sys.exit(2)
    with open(current_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    seed = data.get("seed", -1)
    results = [r for r in data.get("results", []) if "error" not in r]
    # 按 label 分组
    by_label: Dict[str, List[Dict[str, Any]]] = {}
    for r in results:
        by_label.setdefault(r["label"], []).append(r)

    metrics_by_label: Dict[str, Dict[str, float]] = {}
    n_queries = 0
    for label, rows in by_label.items():
        n_queries = max(n_queries, len(rows))
        agg: Dict[str, float] = {}
        for src_field, dst_field in FIELD_MAP.items():
            vals = [x[src_field] for x in rows if src_field in x]
            if not vals:
                continue
            agg[dst_field] = round(statistics.mean(vals), 3)
        metrics_by_label[label] = agg
    return metrics_by_label, n_queries, seed


def _find_baseline(data: Dict[str, Any], bid: Optional[str]) -> Dict[str, Any]:
    baselines = data.get("baselines", [])
    if not baselines:
        print("ERROR: no baselines recorded in eval/baselines.json", file=sys.stderr)
        sys.exit(2)
    if bid is None:
        return baselines[-1]
    for b in baselines:
        if b.get("id") == bid:
            return b
    ids = [b.get("id") for b in baselines]
    print(f"ERROR: baseline id '{bid}' not found. Available: {ids}", file=sys.stderr)
    sys.exit(2)


# ─────────────────────────────────────────────────────────────────────
# 判定 & 渲染
# ─────────────────────────────────────────────────────────────────────
def _judge(
    metric: str, base_v: float, cur_v: float, thresholds: Dict[str, Dict[str, float]],
) -> Tuple[str, str]:
    """返回 (status, threshold_str). status ∈ {OK, REGRESSION, (无阈值)}."""
    cfg = thresholds.get(metric, {}) or {}
    if "max_drop_abs" in cfg:
        limit = cfg["max_drop_abs"]
        if cur_v < base_v - limit:
            return "REGRESSION", f"±{limit}"
        return "OK", f"±{limit}"
    if "max_rise_rel" in cfg:
        limit = cfg["max_rise_rel"]
        if base_v > 0 and cur_v > base_v * (1.0 + limit):
            return "REGRESSION", f"+{int(limit*100)}%"
        return "OK", f"+{int(limit*100)}%"
    if "max_drop_rel" in cfg:
        limit = cfg["max_drop_rel"]
        if base_v > 0 and cur_v < base_v * (1.0 - limit):
            return "REGRESSION", f"-{int(limit*100)}%"
        return "OK", f"-{int(limit*100)}%"
    return "—", "—"


def _fmt_delta(metric: str, base_v: float, cur_v: float) -> str:
    diff = cur_v - base_v
    if metric == "elapsed_s":
        rel = (diff / base_v * 100) if base_v else 0.0
        sign = "+" if diff >= 0 else ""
        return f"{sign}{diff:.1f}s ({sign}{rel:.1f}%)"
    if metric == "chunks":
        sign = "+" if diff >= 0 else ""
        return f"{sign}{diff:.1f}"
    sign = "+" if diff >= 0 else ""
    return f"{sign}{diff:.3f}"


def _compare(
    baseline: Dict[str, Any],
    current: Dict[str, Dict[str, float]],
    thresholds: Dict[str, Dict[str, float]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    base_metrics = baseline.get("metrics_by_label", {})
    # 按基线 label 顺序 + 指标顺序遍历
    metric_order = ["chunks", "crit_ρ", "imp_ρ", "cov", "dup", "elapsed_s"]
    for label, base_vals in base_metrics.items():
        cur_vals = current.get(label, {})
        for metric in metric_order:
            if metric not in base_vals:
                continue
            base_v = base_vals[metric]
            if metric not in cur_vals:
                rows.append({
                    "label": label, "metric": metric, "base": base_v,
                    "cur": None, "delta": "MISSING", "status": "MISSING", "thr": "—",
                })
                continue
            cur_v = cur_vals[metric]
            status, thr = _judge(metric, base_v, cur_v, thresholds)
            rows.append({
                "label": label, "metric": metric, "base": base_v, "cur": cur_v,
                "delta": _fmt_delta(metric, base_v, cur_v),
                "status": status, "thr": thr,
            })
    return rows


def _render(baseline: Dict[str, Any], rows: List[Dict[str, Any]]) -> None:
    print(f"\nBaseline: {baseline['id']}  ({baseline.get('captured_at','?')}, "
          f"N={baseline.get('n_queries','?')}, commit={baseline.get('commit','?')})")
    if baseline.get("notes"):
        print(f"  notes: {baseline['notes']}")
    print()
    print(f"{'label':<22}{'metric':<10}{'base':<10}{'cur':<10}{'delta':<18}{'thr':<10}status")
    print("-" * 90)
    last_label = None
    for r in rows:
        lab = r["label"] if r["label"] != last_label else ""
        last_label = r["label"]
        base = f"{r['base']:.3f}" if isinstance(r["base"], (int, float)) else str(r["base"])
        cur = (f"{r['cur']:.3f}" if isinstance(r["cur"], (int, float))
               else "—" if r["cur"] is None else str(r["cur"]))
        mark = ""
        if r["status"] == "REGRESSION":
            mark = " ⚠"
        print(f"{lab:<22}{r['metric']:<10}{base:<10}{cur:<10}{r['delta']:<18}{r['thr']:<10}{r['status']}{mark}")


# ─────────────────────────────────────────────────────────────────────
# --save-as
# ─────────────────────────────────────────────────────────────────────
def _git_head() -> Tuple[str, bool]:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=_ROOT, text=True
        ).strip()
    except Exception:
        sha = "unknown"
    try:
        dirty_out = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=_ROOT, text=True
        )
        dirty = bool(dirty_out.strip())
    except Exception:
        dirty = False
    return sha, dirty


def _save_new_baseline(
    data: Dict[str, Any], new_id: str, notes: str,
    current_metrics: Dict[str, Dict[str, float]],
    n_queries: int, seed: int, current_path: str,
) -> None:
    existing_ids = {b.get("id") for b in data.get("baselines", [])}
    if new_id in existing_ids:
        print(f"ERROR: baseline id '{new_id}' already exists. Pick another id.", file=sys.stderr)
        sys.exit(2)
    sha, dirty = _git_head()
    entry = {
        "id": new_id,
        "captured_at": datetime.now().strftime("%Y-%m-%dT%H:%M"),
        "commit": sha,
        "commit_dirty": dirty,
        "source_file": os.path.relpath(current_path, _ROOT),
        "n_queries": n_queries,
        "seed": seed,
        "labels_included": sorted(current_metrics.keys()),
        "notes": notes or "",
        "metrics_by_label": {
            lab: {k: round(v, 3) for k, v in m.items()}
            for lab, m in current_metrics.items()
        },
    }
    data.setdefault("baselines", []).append(entry)
    with open(BASELINES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n✓ Saved new baseline '{new_id}' to {os.path.relpath(BASELINES_PATH, _ROOT)}")


# ─────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="Compare current eval result vs frozen baseline.")
    ap.add_argument("--baseline", help="Baseline id (default: last in baselines.json)")
    ap.add_argument("--current", default=DEFAULT_CURRENT, help="Path to exp_phase4b_eval_result.json")
    ap.add_argument("--save-as", help="Freeze current result as new baseline with this id")
    ap.add_argument("--notes", default="", help="Notes for --save-as")
    args = ap.parse_args()

    data = _load_baselines()
    thresholds = data.get("thresholds", {})

    current_metrics, n_queries, seed = _aggregate_current(args.current)

    if args.save_as:
        _save_new_baseline(
            data, args.save_as, args.notes,
            current_metrics, n_queries, seed, args.current,
        )

    baseline = _find_baseline(data, args.baseline)
    rows = _compare(baseline, current_metrics, thresholds)
    _render(baseline, rows)

    n_reg = sum(1 for r in rows if r["status"] == "REGRESSION")
    n_missing = sum(1 for r in rows if r["status"] == "MISSING")
    n_ok = sum(1 for r in rows if r["status"] == "OK")
    print("-" * 90)
    print(f"Summary: {n_reg} regression, {n_missing} missing, {n_ok} ok  "
          f"(current: N={n_queries}, seed={seed}, labels={sorted(current_metrics.keys())})")
    sys.exit(1 if n_reg > 0 else 0)


if __name__ == "__main__":
    main()
