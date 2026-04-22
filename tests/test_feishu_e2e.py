"""Feishu e2e 端到端测试：接收 query → FeishuSourceAgent 全链路 → 输出 chunks + timing。

用法:
    python3 tests/test_feishu_e2e.py
    python3 tests/test_feishu_e2e.py --query "nubia Affiliate渠道的佣金设置和合作伙伴清单"
    python3 tests/test_feishu_e2e.py --query "Hypershell Ultra款的上线时间表" --top-k 8
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
# 只看关键 TIMING 日志，屏蔽 httpx 噪声
for noisy in ("httpx", "httpcore", "urllib3", "google"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

logger = logging.getLogger("e2e_test")


DEFAULT_QUERY = "nubia Affiliate渠道的佣金设置和合作伙伴清单"


async def run(query: str, top_k: int) -> None:
    uat = os.getenv("FEISHU_UAT", "").strip()
    if not uat:
        print("❌  FEISHU_UAT 未配置，请检查 .env")
        sys.exit(1)

    from tools.models import SourceAgentConfig
    from tools.feishu_doc_pool import FeishuDocPool
    from tools.table_pool import TablePool
    from agents.feishu_source_agent import FeishuSourceAgent
    from agents.source_agent_base import Focus

    cfg = SourceAgentConfig(
        source_top_k=top_k,
        min_loops=1,
        max_loops=1,
        source_timeout_s=60.0,
        sub_query_top_n=8,
    )

    doc_pool  = FeishuDocPool(uat=uat)
    table_pool = TablePool(uat=uat)

    agent = FeishuSourceAgent(
        cfg=cfg,
        uat=uat,
        doc_pool=doc_pool,
        table_pool=table_pool,
    )

    # 用 global planner 拆 focuses（真实链路）
    from tools.query_planner import plan_query
    print(f"\n{'='*70}")
    print(f"Query: {query}")
    print(f"{'='*70}")

    t0 = time.perf_counter()
    plan = await asyncio.to_thread(plan_query, query, ["feishu"])
    plan_ms = (time.perf_counter() - t0) * 1000
    print(f"\n[plan]  {plan_ms:.0f}ms  focuses={len(plan.focuses)}")
    for i, f in enumerate(plan.focuses):
        print(f"  focus_{i}: {f.text}  targets={f.targets}")

    # 构造 Focus 列表（只取 feishu targets）
    focuses = [
        Focus(focus_id=f"focus_{i}", text=f.text, targets=f.targets)
        for i, f in enumerate(plan.focuses)
        if "feishu" in (f.targets or [])
    ]
    if not focuses:
        # fallback: 把所有 focus 都给 feishu
        focuses = [
            Focus(focus_id=f"focus_{i}", text=f.text, targets=["feishu"])
            for i, f in enumerate(plan.focuses)
        ]

    t1 = time.perf_counter()
    result = await agent.run(focuses)
    source_ms = (time.perf_counter() - t1) * 1000
    total_ms  = (time.perf_counter() - t0) * 1000

    chunks = result.top_chunks if hasattr(result, "top_chunks") else []

    print(f"\n[result] total={total_ms:.0f}ms  source={source_ms:.0f}ms  chunks={len(chunks)}")
    print(f"\n{'─'*70}")
    print(f"{'#':<4} {'tier':<12} {'score':<7} {'source':<35} {'heading'}")
    print(f"{'─'*70}")
    for i, c in enumerate(chunks, 1):
        meta = c.metadata or {}
        tier  = c.tier or "—"
        score = f"{c.score:.3f}" if c.score is not None else "—"
        src   = (c.metadata.get("source_name") or "")[:34]
        head  = (meta.get("heading_path") or "")[:50]
        print(f"{i:<4} {tier:<12} {score:<7} {src:<35} {head}")

    print(f"\n{'─'*70}")

    # table ROI summary（从 table_pool 读）
    if hasattr(table_pool, "get_roi_rows"):
        rows = table_pool.get_roi_rows()
        if rows:
            submitted = [r for r in rows if r.get("submit_selected")]
            final_sel = [r for r in rows if r.get("final_selected")]
            wasted    = [r for r in submitted if not r.get("final_selected")]
            total_read_ms = sum(float(r.get("read_ms", 0)) for r in submitted)
            print(f"\n[table ROI]  submitted={len(submitted)}  final={len(final_sel)}  wasted={len(wasted)}  read_ms={total_read_ms:.0f}")
            for r in rows:
                mark = "✓" if r.get("final_selected") else ("✗" if r.get("submit_selected") else "skip")
                print(
                    f"  {mark}  {r.get('doc_type','?'):<8} score={r.get('candidate_rerank_score',0):.3f}"
                    f"  child={r.get('selected_child_count',0)}/{r.get('structure_child_count',0)}"
                    f"  read={r.get('read_ms',0):.0f}ms  {r.get('title','')[:40]}"
                )

    print(f"\n✅  Done  total={total_ms:.0f}ms\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--top-k", type=int, default=6)
    args = parser.parse_args()
    asyncio.run(run(args.query, args.top_k))


if __name__ == "__main__":
    main()
