"""Phase 4b Step C 实验：eval_layer 接入效果评估（E3 ~ E6）。

从 eval/eval_queries.json 随机抽取 8 个 query，对比：

- E3  BASELINE              : eval_layer OFF（merge 后直接输出）
- E4  RULE_ON               : eval_layer=rule, 默认参数
- E5  RULE_WEIGHT_TUNE      : 权重改为 (0.45 tier / 0.40 rel / 0.15 cov)，看 relevance 加重
- E6a NO_BACKFILL           : focus_backfill=False，看 focus 覆盖是否塌方
- E6b MINKEEP_TIGHT         : min_keep_ratio=0.15，max_keep_ratio=0.60，看截断更狠时的影响

每组配置对每个 query 跑 1 次，最后输出对比表 + 存 JSON。

关注指标：
- focus_coverage_rate : 命中 ≥1 chunk 的 focus / 总 focus
- critical_density    : tier=critical 的 chunk 占比
- dup_rate            : 同 id 跨源合并次数（通过 per_source sum - merged 算）
- elapsed_ms
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import statistics
import sys
import time
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from retrieve_agent import build_retrieve_agent
from tools.models import RetrieveConfig, SourceAgentConfig, SourceConfig
from agents.sub_agents.eval_layer import RuleEvalParams

# 劫持 build_eval_layer 以便按实验组注入不同参数
import agents.sub_agents.eval_layer as _eval_layer_mod


logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("exp_phase4b_eval")
logger.setLevel(logging.INFO)


# ── 实验组定义 ──────────────────────────────────────────────────
EXPERIMENTS: List[Dict[str, Any]] = [
    {
        "label": "E3_baseline",
        "eval_enabled": False,
        "params": None,
    },
    {
        "label": "E4_rule_default",
        "eval_enabled": True,
        "params": RuleEvalParams(),  # 默认：0.55/0.30/0.15
    },
    {
        "label": "E5_rule_rel_heavy",
        "eval_enabled": True,
        "params": RuleEvalParams(w_tier=0.45, w_relevance=0.40, w_coverage=0.15),
    },
    {
        "label": "E6a_no_backfill",
        "eval_enabled": True,
        "params": RuleEvalParams(focus_backfill=False),
    },
    {
        "label": "E6b_minkeep_tight",
        "eval_enabled": True,
        "params": RuleEvalParams(min_keep_ratio=0.15, max_keep_ratio=0.60),
    },
]

N_QUERIES = 8
SEED = 42

# S2.4 回归：通过环境变量 EXP_LABELS=E3_baseline,E5_rule_rel_heavy 筛选实验组
_FILTER = os.environ.get("EXP_LABELS", "").strip()
if _FILTER:
    _wanted = {s.strip() for s in _FILTER.split(",") if s.strip()}
    EXPERIMENTS = [e for e in EXPERIMENTS if e["label"] in _wanted]


# ── query 加载 ──────────────────────────────────────────────────
def _load_random_queries(n: int, seed: int) -> List[Dict[str, Any]]:
    p = os.path.join(_ROOT, "eval", "eval_queries.json")
    with open(p, "r", encoding="utf-8") as f:
        raw = json.load(f)
    rng = random.Random(seed)
    pool = raw["queries"]
    picked = rng.sample(pool, k=min(n, len(pool)))
    return picked


# ── 单次 run ────────────────────────────────────────────────────
async def _run_one(
    label: str, query: str, eval_enabled: bool, params: Optional[RuleEvalParams],
) -> Dict[str, Any]:
    # [S1.3] 原先 feishu/bailian 共用 45s；spec §16-P2.4 观察到 Feishu 平均 34-38s、
    # p95 > 45s 导致整源 timed_out，把 feishu 单独拉到 60s，bailian 保持 45s。
    bailian_sa_cfg = SourceAgentConfig(
        source_top_k=8,
        min_loops=1, max_loops=1,
        sub_query_top_n=8,
        source_timeout_s=45.0,
    )
    feishu_sa_cfg = SourceAgentConfig(
        source_top_k=8,
        min_loops=1, max_loops=1,
        sub_query_top_n=8,
        source_timeout_s=60.0,
    )
    cfg = RetrieveConfig(
        enabled_sources=["feishu", "bailian"],
        source_configs={"feishu": feishu_sa_cfg, "bailian": bailian_sa_cfg},
        eval_layer_enabled=eval_enabled,
        eval_layer_mode="rule",
        eval_layer_top_k=12,
    )

    # 劫持 build_eval_layer 以注入实验参数（线程内修改 module 级函数）
    orig_builder = _eval_layer_mod.build_eval_layer

    def _patched(mode: str, p: Optional[RuleEvalParams] = None):
        return _eval_layer_mod.build_rule_eval_layer(params or RuleEvalParams())

    _eval_layer_mod.build_eval_layer = _patched  # type: ignore

    try:
        agent = build_retrieve_agent(
            config=cfg,
            source_config=SourceConfig(load_from_env=True),
            name=f"rexp_{label}",
        )
        app_name = f"exp_{label}_{hash(query) & 0xFFFF:x}"
        runner = InMemoryRunner(agent=agent, app_name=app_name)
        user_id = "u_exp"
        session = await runner.session_service.create_session(
            app_name=app_name, user_id=user_id,
        )
        content = genai_types.Content(
            role="user", parts=[genai_types.Part(text=query)],
        )

        t0 = time.monotonic()
        final_payload: Optional[Dict[str, Any]] = None
        async for event in runner.run_async(
            user_id=user_id, session_id=session.id, new_message=content,
        ):
            if event.actions and event.actions.state_delta:
                raw = event.actions.state_delta.get("retrieval_result")
                if raw:
                    final_payload = json.loads(raw)
        elapsed = time.monotonic() - t0
    finally:
        _eval_layer_mod.build_eval_layer = orig_builder  # type: ignore

    if not final_payload:
        return {"label": label, "query": query, "error": "no payload", "elapsed_s": round(elapsed, 2)}

    chunks = final_payload.get("chunks", [])
    source_stats = final_payload.get("source_stats", {})
    focus_cov = final_payload.get("focus_coverage", {})
    focuses = final_payload.get("focuses", [])

    # 指标计算
    tier_counts: Dict[str, int] = {}
    for c in chunks:
        t = c.get("confidence_tier") or "unknown"
        tier_counts[t] = tier_counts.get(t, 0) + 1
    n = len(chunks) or 1
    critical_density = round(tier_counts.get("critical", 0) / n, 3)
    important_density = round(tier_counts.get("important", 0) / n, 3)
    n_focus = len(focuses) or 1
    hit_focus = sum(1 for v in focus_cov.values() if v > 0)
    focus_coverage_rate = round(hit_focus / n_focus, 3)

    # dup_rate: (per_source sum - merged) / per_source sum
    ps_sum = sum(source_stats.values()) or 1
    dup_rate = round(max(0, ps_sum - n) / ps_sum, 3)

    out: Dict[str, Any] = {
        "label": label,
        "query": query,
        "elapsed_s": round(elapsed, 2),
        "merged_chunks": n if chunks else 0,
        "per_source_sum": ps_sum,
        "source_stats": source_stats,
        "tier_counts": tier_counts,
        "critical_density": critical_density,
        "important_density": important_density,
        "focus_coverage_rate": focus_coverage_rate,
        "n_focus": n_focus,
        "hit_focus": hit_focus,
        "dup_rate": dup_rate,
    }
    return out


# ── 主流程 ──────────────────────────────────────────────────────
async def main():
    queries = _load_random_queries(N_QUERIES, SEED)
    print("=" * 80)
    print(f"Phase 4b Step C - eval_layer experiment (N_QUERIES={len(queries)}, SEED={SEED})")
    for q in queries:
        print(f"  #{q['id']}: {q['query']}")
    print("=" * 80)

    all_results: List[Dict[str, Any]] = []

    for exp in EXPERIMENTS:
        print(f"\n--- {exp['label']} ---")
        per_exp: List[Dict[str, Any]] = []
        for q in queries:
            r = await _run_one(
                label=exp["label"],
                query=q["query"],
                eval_enabled=exp["eval_enabled"],
                params=exp["params"],
            )
            r["query_id"] = q["id"]
            per_exp.append(r)
            all_results.append(r)
            if "error" in r:
                print(f"  q#{q['id']} ERROR: {r['error']}")
            else:
                print(
                    f"  q#{q['id']:<3} chunks={r['merged_chunks']:<3} "
                    f"crit_ρ={r['critical_density']:.2f} "
                    f"cov={r['focus_coverage_rate']:.2f} "
                    f"dup={r['dup_rate']:.2f} "
                    f"t={r['elapsed_s']:.1f}s"
                )

        # 组内均值
        ok = [x for x in per_exp if "error" not in x]
        if ok:
            avg = {
                "merged_chunks": statistics.mean(x["merged_chunks"] for x in ok),
                "critical_density": statistics.mean(x["critical_density"] for x in ok),
                "important_density": statistics.mean(x["important_density"] for x in ok),
                "focus_coverage_rate": statistics.mean(x["focus_coverage_rate"] for x in ok),
                "dup_rate": statistics.mean(x["dup_rate"] for x in ok),
                "elapsed_s": statistics.mean(x["elapsed_s"] for x in ok),
            }
            print(
                f"  [avg] chunks={avg['merged_chunks']:.1f} "
                f"crit_ρ={avg['critical_density']:.2f} "
                f"imp_ρ={avg['important_density']:.2f} "
                f"cov={avg['focus_coverage_rate']:.2f} "
                f"dup={avg['dup_rate']:.2f} "
                f"t={avg['elapsed_s']:.1f}s"
            )

    # 汇总表
    print("\n" + "=" * 90)
    print("SUMMARY (mean over queries)")
    print("=" * 90)
    header = (
        f"{'label':<22}{'chunks':<9}{'crit_ρ':<10}{'imp_ρ':<10}"
        f"{'focus_cov':<12}{'dup':<8}{'t(s)':<7}"
    )
    print(header)
    print("-" * len(header))
    for exp in EXPERIMENTS:
        rs = [r for r in all_results if r["label"] == exp["label"] and "error" not in r]
        if not rs:
            print(f"{exp['label']:<22} (no data)")
            continue
        m = lambda k: statistics.mean(r[k] for r in rs)
        print(
            f"{exp['label']:<22}{m('merged_chunks'):<9.1f}"
            f"{m('critical_density'):<10.3f}{m('important_density'):<10.3f}"
            f"{m('focus_coverage_rate'):<12.3f}{m('dup_rate'):<8.3f}{m('elapsed_s'):<7.1f}"
        )

    out_path = os.path.join(os.path.dirname(__file__), "exp_phase4b_eval_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {"seed": SEED, "queries": queries, "results": all_results},
            f, ensure_ascii=False, indent=2,
        )
    print(f"\nResults saved: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
