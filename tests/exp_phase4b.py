"""Phase 4b 实验：源预设调参（E1 + E2）。

E1 - source_top_k 扫描：5 / 8 / 12 / 15（feishu + bailian 双源 single loop）
    观察：merged_chunks 数量、tier 分布、E2E 耗时、focus 覆盖
E2 - STRICT 预设稳定性：source_top_k=5 跑 3 次，看耗时与 chunks 是否稳定

不改代码 / 不接 eval_layer，仅基础 retrieve 流程。运行完打印汇总表。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List

# 让 tests/ 下脚本能 import 项目根模块
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from retrieve_agent import build_retrieve_agent
from tools.models import RetrieveConfig, SourceAgentConfig, SourceConfig

logging.basicConfig(
    level=logging.WARNING,  # 实验静音，只看汇总
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

QUERY = "nubia 英国站3月份DTC渠道的GMV和订单量"


async def _run_once(label: str, top_k: int) -> Dict[str, Any]:
    """用指定 source_top_k 跑一次 retrieve_agent（feishu+bailian）。"""
    sa_cfg = SourceAgentConfig(
        source_top_k=top_k,
        min_loops=1,
        max_loops=1,
        source_timeout_s=45.0,
        sub_query_top_n=8,
    )
    cfg = RetrieveConfig(
        enabled_sources=["feishu", "bailian"],
        source_configs={"feishu": sa_cfg, "bailian": sa_cfg},
        eval_layer_enabled=False,
    )
    agent = build_retrieve_agent(
        config=cfg,
        source_config=SourceConfig(load_from_env=True),
        name=f"retrieve_exp_{label}",
    )

    runner = InMemoryRunner(agent=agent, app_name=f"exp_{label}")
    user_id = "u_exp"
    session = await runner.session_service.create_session(
        app_name=f"exp_{label}", user_id=user_id,
    )
    content = genai_types.Content(
        role="user", parts=[genai_types.Part(text=QUERY)],
    )

    t0 = time.monotonic()
    final_payload: Dict[str, Any] | None = None
    async for event in runner.run_async(
        user_id=user_id, session_id=session.id, new_message=content,
    ):
        if event.actions and event.actions.state_delta:
            raw = event.actions.state_delta.get("retrieval_result")
            if raw:
                final_payload = json.loads(raw)
    elapsed = time.monotonic() - t0

    if not final_payload:
        return {"label": label, "top_k": top_k, "elapsed_s": elapsed, "error": "no payload"}

    chunks = final_payload.get("chunks", [])
    tier_count: Dict[str, int] = {}
    for c in chunks:
        # 注意：retrieve_agent 为兼容 AnswerAgent 旧 schema，字段名用 "confidence_tier"
        t = c.get("confidence_tier") or "unknown"
        tier_count[t] = tier_count.get(t, 0) + 1

    return {
        "label": label,
        "top_k": top_k,
        "elapsed_s": round(elapsed, 2),
        "merged_chunks": len(chunks),
        "source_stats": final_payload.get("source_stats", {}),
        "focus_coverage": final_payload.get("focus_coverage", {}),
        "tier_dist": tier_count,
    }


async def main():
    print("=" * 80)
    print("Phase 4b Experiment - E1 (top_k scan) + E2 (strict stability)")
    print(f"Query: {QUERY}")
    print("=" * 80)

    results: List[Dict[str, Any]] = []

    # E1: top_k 扫描 5 / 8 / 12 / 15
    print("\n--- E1: source_top_k scan ---")
    for k in (5, 8, 12, 15):
        r = await _run_once(f"E1_k{k}", k)
        results.append(r)
        print(f"[E1 k={k}] {r}")

    # E2: STRICT (k=5) 跑 3 次稳定性
    print("\n--- E2: STRICT stability (k=5 x 3) ---")
    e2_runs = []
    for i in range(3):
        r = await _run_once(f"E2_run{i+1}", 5)
        e2_runs.append(r)
        results.append(r)
        print(f"[E2 run{i+1}] {r}")

    # 汇总
    print("\n" + "=" * 80)
    print("SUMMARY TABLE")
    print("=" * 80)
    header = f"{'label':<14}{'top_k':<7}{'elapsed':<10}{'merged':<8}{'feishu':<8}{'bailian':<9}{'tier_dist':<30}"
    print(header)
    print("-" * len(header))
    for r in results:
        if "error" in r:
            print(f"{r['label']:<14} ERROR: {r['error']}")
            continue
        fs = r["source_stats"].get("feishu", 0)
        bl = r["source_stats"].get("bailian", 0)
        print(f"{r['label']:<14}{r['top_k']:<7}{r['elapsed_s']:<10}{r['merged_chunks']:<8}{fs:<8}{bl:<9}{r['tier_dist']}")

    # E2 方差
    e2_ok = [r for r in e2_runs if "error" not in r]
    if len(e2_ok) >= 2:
        times = [r["elapsed_s"] for r in e2_ok]
        chunks = [r["merged_chunks"] for r in e2_ok]
        print(f"\nE2 stability: elapsed min={min(times):.2f} max={max(times):.2f} Δ={max(times)-min(times):.2f}s")
        print(f"              chunks  min={min(chunks)}  max={max(chunks)}  Δ={max(chunks)-min(chunks)}")

    # 写结果文件
    out_path = os.path.join(os.path.dirname(__file__), "exp_phase4b_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
