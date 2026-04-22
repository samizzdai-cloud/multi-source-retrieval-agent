"""Full e2e baseline test: RetrieveAgent (feishu + bailian) → 3 queries → JSON output

走完整 RetrieveAgent 链路（global plan → source dispatch → merge → output），
输出每个 query 的详细 timing、sub_query 拆解、chunks metadata + content。

用法:
    python3 tests/test_full_e2e_baseline.py
"""

from __future__ import annotations

import asyncio
import json
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
for noisy in ("httpx", "httpcore", "urllib3", "google"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

logger = logging.getLogger("full_e2e")

QUERIES = [
    "Hypershell X Ultra 在各国市场的定价策略和销售渠道分布",
    "REDMAGIC 近三个月的社媒营销效果和KOL合作情况",
    "Nothing Phone 3a 的产品上市时间表和首发市场计划",
    "nubia 日本市场的KOL合作名单和已确认的博主合作进度",
    "Hypershell 归因 UTM 配置现状和改进方案",
]

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "eval" / "results"


async def run_query(query: str, agent, idx: int) -> dict:
    """Run one query through the full RetrieveAgent pipeline."""
    from google.adk.runners import InMemoryRunner
    from google.genai import types as genai_types
    import uuid

    runner = InMemoryRunner(agent=agent, app_name="e2e_baseline")
    user_id = f"u_{uuid.uuid4().hex[:8]}"
    session = await runner.session_service.create_session(
        app_name="e2e_baseline", user_id=user_id,
    )

    content = genai_types.Content(
        role="user", parts=[genai_types.Part(text=query)],
    )

    t0 = time.perf_counter()
    final_text = None
    async for event in runner.run_async(
        user_id=user_id, session_id=session.id, new_message=content,
    ):
        if event.actions and event.actions.state_delta:
            rr = event.actions.state_delta.get("retrieval_result")
            if rr:
                final_text = rr
    total_ms = (time.perf_counter() - t0) * 1000

    if not final_text:
        return {"query_idx": idx, "query": query, "error": "no retrieval_result", "total_ms": total_ms}

    payload = json.loads(final_text)
    chunks = payload.get("chunks", [])
    focuses = payload.get("focuses", [])
    source_stats = payload.get("source_stats", {})

    # Build detailed chunk output
    chunk_details = []
    for i, c in enumerate(chunks, 1):
        meta = c.get("metadata", {})
        chunk_details.append({
            "rank": i,
            "chunk_id": meta.get("chunk_id", ""),
            "tier": c.get("confidence_tier", ""),
            "score": round(c.get("score", 0), 3),
            "source_tool": c.get("source_tool", ""),
            "source_name": c.get("source_name", "")[:60],
            "heading_path": c.get("heading_path", "")[:80],
            "route": meta.get("route", ""),
            "sub_query": meta.get("sub_query", "")[:60],
            "target_focuses": meta.get("target_focuses", []),
            "content": c.get("text", "")[:300],
        })

    return {
        "query_idx": idx,
        "query": query,
        "total_ms": round(total_ms, 1),
        "total_time_ms": payload.get("total_time_ms", 0),
        "source_stats": source_stats,
        "n_focuses": len(focuses),
        "focuses": focuses,
        "n_chunks": len(chunks),
        "tier_dist": {
            "critical": sum(1 for c in chunks if c.get("confidence_tier") == "critical"),
            "important": sum(1 for c in chunks if c.get("confidence_tier") == "important"),
            "noise": sum(1 for c in chunks if c.get("confidence_tier") == "noise"),
        },
        "chunks": chunk_details,
    }


async def main() -> None:
    from tools.models import RetrieveConfig, SourceConfig
    from retrieve_agent import build_retrieve_agent

    print(f"\n{'='*70}")
    print(f"  Full E2E Baseline Test — feishu + bailian")
    print(f"  {len(QUERIES)} queries")
    print(f"{'='*70}\n")

    cfg = RetrieveConfig(
        enabled_sources=["feishu", "bailian"],
        preset="lite",
    )
    sc = SourceConfig(load_from_env=True)
    agent = build_retrieve_agent(config=cfg, source_config=sc, name="e2e_baseline")

    results = []
    for idx, query in enumerate(QUERIES, 1):
        print(f"\n[{idx}/{len(QUERIES)}] {query}")
        print(f"{'─'*60}")
        try:
            result = await run_query(query, agent, idx)
            results.append(result)

            # Print summary
            print(f"  total={result['total_ms']:.0f}ms  chunks={result['n_chunks']}  sources={result['source_stats']}")
            td = result["tier_dist"]
            print(f"  tiers: critical={td['critical']}  important={td['important']}  noise={td['noise']}")
            for f in result.get("focuses", []):
                print(f"  focus: {f.get('focus_id','')} → {f.get('text','')[:60]}")
            print()
            for c in result["chunks"]:
                print(f"  #{c['rank']}  {c['tier']:<10} {c['score']:.3f}  [{c['source_tool']}/{c['route']}]  {c['source_name'][:35]}")
                print(f"       heading: {c['heading_path'][:60]}")
                print(f"       sq: {c['sub_query'][:50]}")
        except Exception as e:
            print(f"  ❌ FAILED: {e}")
            results.append({"query_idx": idx, "query": query, "error": str(e)})

    # Save to JSON
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"full_e2e_baseline_{ts}.json"
    with open(out_path, "w") as f:
        json.dump({"timestamp": ts, "queries": results}, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n✅  Saved to {out_path}")

    # Summary table
    print(f"\n{'='*70}")
    print(f"  SUMMARY")
    print(f"{'='*70}")
    print(f"  {'#':<3} {'ms':>7} {'chunks':>6} {'crit':>5} {'imp':>5} {'noise':>5}  query")
    print(f"  {'─'*65}")
    for r in results:
        if "error" in r:
            print(f"  {r['query_idx']:<3} {'ERR':>7} {'—':>6} {'—':>5} {'—':>5} {'—':>5}  {r['query'][:40]}")
        else:
            td = r["tier_dist"]
            print(f"  {r['query_idx']:<3} {r['total_ms']:>7.0f} {r['n_chunks']:>6} {td['critical']:>5} {td['important']:>5} {td['noise']:>5}  {r['query'][:40]}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
