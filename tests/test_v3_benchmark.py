"""v3 Benchmark — 12 queries: 9 previously tested + 3 v3-new

测试用例说明：
  Q01-Q09  来自 test_query_v2.json（已测试过，用于速度/质量对比）
  Q10-Q12  v3 新增（验证 #hashtag 路由、sheet/bitable 读取、Wiki 去重）

运行：
    PYTHONPATH=. python3 tests/test_v3_benchmark.py
"""

from __future__ import annotations
import asyncio, json, os, re, sys, time
from typing import Any, Dict, List
from dotenv import load_dotenv
load_dotenv()

# ── 12 Test Cases ────────────────────────────────────────────────────────────

QUERIES = [
    # ── 9 previously tested (test_query_v2.json ids 1,2,3,4,6,8,10,12,21) ──
    {
        "id": "Q01", "origin": "v2-id1",
        "query": "nubia 英国店铺3月份的店铺表现以及热卖产品的表现，和去年同期相比的变化趋势",
        "query_type_expected": "data", "project": "nubia",
        "v3_focus": "data→两阶段 (doc+table)",
    },
    {
        "id": "Q02", "origin": "v2-id2",
        "query": "nubia各站点近期的流量表现，近三个月，环比25年和对比前一段时间",
        "query_type_expected": "data", "project": "nubia",
        "v3_focus": "data→table搜索触发",
    },
    {
        "id": "Q03", "origin": "v2-id3",
        "query": "nubia 日本市场的营销计划、博主合作名单或相关的社交媒体推广文档",
        "query_type_expected": "concept", "project": "nubia",
        "v3_focus": "concept→仅文档路径",
    },
    {
        "id": "Q04", "origin": "v2-id5",
        "query": "nubia 产品线在全球市场的定价策略和竞品对比分析",
        "query_type_expected": "concept", "project": "nubia",
        "v3_focus": "concept→Wiki doc检索",
    },
    {
        "id": "Q05", "origin": "v2-id6",
        "query": "nubia Amazon渠道的运营管理策略和12月工作总结",
        "query_type_expected": "mixed", "project": "nubia",
        "v3_focus": "mixed→doc+table双路",
    },
    {
        "id": "Q06", "origin": "v2-id10",
        "query": "请从业务和财务角度介绍hypershell项目的特点，包括运行到目前为止好的点以及存在的痛点",
        "query_type_expected": "concept", "project": "hypershell",
        "v3_focus": "concept→仅文档+Wiki",
    },
    {
        "id": "Q07", "origin": "v2-id12",
        "query": "Hypershell 在消费级外骨骼市场的竞品格局和差异化定位",
        "query_type_expected": "concept", "project": "hypershell",
        "v3_focus": "concept→Wiki doc召回",
    },
    {
        "id": "Q08", "origin": "v2-id21",
        "query": "按照渠道分类对比nubia和hypershell的总体渠道表现数据",
        "query_type_expected": "data", "project": "cross",
        "v3_focus": "多实体+data→多FocusGroup+table",
    },
    {
        "id": "Q09", "origin": "v2-id23",
        "query": "知识库检索Agent的产品架构设计和技术方案",
        "query_type_expected": "concept", "project": "internal",
        "v3_focus": "concept→内部文档",
    },
    # ── 3 v3 new ──
    {
        "id": "Q10", "origin": "v3-new",
        "query": "nubia 各项目营销预算和KOL费用 #feishu",
        "query_type_expected": "data", "project": "nubia",
        "v3_focus": "#feishu hashtag → required_targets=['feishu'] + table搜索",
    },
    {
        "id": "Q11", "origin": "v3-new",
        "query": "数据团队月会记录",
        "query_type_expected": "concept", "project": "internal",
        "v3_focus": "Wiki doc去重验证（MCP∩Wiki 9个重叠，应只出现一次）",
    },
    {
        "id": "Q12", "origin": "v3-new",
        "query": "项目任务进展和状态 #feishu",
        "query_type_expected": "data", "project": "cross",
        "v3_focus": "#feishu + data → bitable reader触发",
    },
]

# ── Runner ────────────────────────────────────────────────────────────────────

async def run_query(q: Dict[str, Any], feishu_uat: str) -> Dict[str, Any]:
    from tools.query_planner import plan_query
    from tools.evaluator import EvaluationLayer, EvalConfig
    from tools.reranker import rerank_and_filter
    from tools.embedding import EmbeddingCache
    from tools.feishu_retrieve import feishu_retrieve
    from tools.bailian_retrieve import bailian_retrieve_unified
    from tools.feishu_doc_pool import FeishuDocPool
    from tools.table_pool import TablePool

    available_sources = ["feishu", "bailian"]
    query = q["query"]
    t0 = time.monotonic()

    # Phase 1: Plan
    t_plan = time.monotonic()
    plan = await asyncio.to_thread(plan_query, query, available_sources)
    plan_ms = int((time.monotonic() - t_plan) * 1000)

    focuses = [f for fg in plan.focus_groups for f in fg.focuses if f]

    # Phase 2: Fan-out
    doc_pool = FeishuDocPool(uat=feishu_uat, query_type=plan.query_type)
    table_pool = TablePool(uat=feishu_uat) if plan.query_type in ("data", "mixed") else None

    task_map = {}
    task_group_map = {}
    for gi, fg in enumerate(plan.focus_groups):
        for sq in fg.sub_queries:
            for target in sq.targets:
                if target == "feishu":
                    kwargs = {
                        "uat": feishu_uat,
                        "query_type": plan.query_type,
                        "doc_pool": doc_pool,
                    }
                    if plan.time_filter:
                        kwargs["time_filter"] = plan.time_filter
                    if table_pool:
                        kwargs["table_pool"] = table_pool
                    coro = feishu_retrieve(sq.query, **kwargs)
                elif target == "bailian":
                    coro = bailian_retrieve_unified(
                        sq.query,
                        tags=sq.tags or [],
                        indexes=sq.indexes or [],
                        query_type=plan.query_type,
                    )
                else:
                    continue
                task = asyncio.create_task(coro)
                task_map[task] = target
                task_group_map[task] = gi

    dashscope_key = os.getenv("DASHSCOPE_API_KEY", "")
    cache = EmbeddingCache(api_key=dashscope_key, enabled=bool(dashscope_key))
    focus_embed_task = asyncio.create_task(cache.precompute_focuses(focuses))

    t_retrieve = time.monotonic()
    ec = EvalConfig()
    evaluator = EvaluationLayer(ec, verbose=False)
    all_chunks, source_stats, focus_hits, _ = await evaluator.collect(
        task_map, focuses=focuses, task_group_map=task_group_map,
        embedding_cache=cache, focus_groups=plan.focus_groups,
        query_type=plan.query_type,
    )
    retrieve_ms = int((time.monotonic() - t_retrieve) * 1000)

    # Phase 3: Rerank
    t_rerank = time.monotonic()
    await focus_embed_task
    if cache.enabled:
        await cache.wait_pending()
    sim_matrix = cache.get_sim_matrix(focuses, all_chunks) if cache.enabled else None
    final_chunks, _ = rerank_and_filter(all_chunks, top_k=ec.top_k_chunks, focuses=focuses or None,
                                     min_per_focus=ec.min_per_focus_select,
                                     precomputed_sim_matrix=sim_matrix,
                                     emb_weight=ec.emb_weight, kw_weight=ec.kw_weight,
                                     query_type=plan.query_type)
    rerank_ms = int((time.monotonic() - t_rerank) * 1000)
    total_ms = int((time.monotonic() - t0) * 1000)

    # Collect v3-specific signals
    table_chunks = [c for c in final_chunks if c.metadata.get("doc_type") in ("SHEET", "BITABLE")]
    wiki_in_final = [c for c in final_chunks if "/wiki/" in (c.source_url or "")]
    required_hit = any(
        bool(fg.required_targets) for fg in plan.focus_groups
    )

    return {
        "id": q["id"],
        "query": query,
        "origin": q["origin"],
        "v3_focus": q["v3_focus"],
        "query_type_expected": q["query_type_expected"],
        "query_type_actual": plan.query_type,
        "type_match": plan.query_type == q["query_type_expected"],
        "required_targets_set": required_hit,
        "required_targets_values": [fg.required_targets for fg in plan.focus_groups if fg.required_targets],
        "num_focus_groups": len(plan.focus_groups),
        "num_sub_queries": sum(len(fg.sub_queries) for fg in plan.focus_groups),
        "sub_queries_detail": [
            {"q": sq.query, "targets": sq.targets, "tags": sq.tags}
            for fg in plan.focus_groups for sq in fg.sub_queries
        ],
        "timing": {"plan_ms": plan_ms, "retrieve_ms": retrieve_ms, "rerank_ms": rerank_ms, "total_ms": total_ms},
        "retrieval": {"raw_chunks": len(all_chunks), "source_stats": dict(source_stats)},
        "rerank": {"final_chunks": len(final_chunks)},
        "v3_signals": {
            "table_chunks_in_final": len(table_chunks),
            "table_chunk_sources": [c.source_name for c in table_chunks],
            "wiki_chunks_in_final": len(wiki_in_final),
        },
        "top3_chunks": [
            {"score": round(c.score, 3), "source": c.source_name[:50],
             "tool": c.source_tool, "heading": (c.heading_path or "")[:80],
             "doc_type": c.metadata.get("doc_type", ""),
             "text_preview": c.text[:120].replace("\n", " ")}
            for c in final_chunks[:3]
        ],
    }


async def main():
    uat = os.getenv("FEISHU_UAT", "")
    if not uat:
        print("ERROR: FEISHU_UAT not set")
        sys.exit(1)

    print(f"{'='*70}")
    print(f"  v3 Benchmark — {len(QUERIES)} queries (9 prev-tested + 3 v3-new)")
    print(f"{'='*70}\n")

    results = []
    for q in QUERIES:
        print(f"  Running {q['id']} ({q['origin']}): {q['query'][:55]}...")
        try:
            r = await run_query(q, uat)
            results.append(r)
            t = r["timing"]
            rs = r["retrieval"]
            rr = r["rerank"]
            type_ok = "✅" if r["type_match"] else f"❌(got {r['query_type_actual']})"
            req = f" required={r['required_targets_values']}" if r["required_targets_set"] else ""
            tbl = f" +{r['v3_signals']['table_chunks_in_final']}tbl" if r['v3_signals']['table_chunks_in_final'] else ""
            print(f"    {type_ok}{req} | plan={t['plan_ms']}ms retrieve={t['retrieve_ms']}ms total={t['total_ms']}ms "
                  f"| raw={rs['raw_chunks']} → final={rr['final_chunks']}{tbl} | src={rs['source_stats']}")
        except Exception as e:
            print(f"    ❌ FAILED: {e}")
            results.append({"id": q["id"], "query": q["query"], "error": str(e)})
        print()

    # ── Summary Report ────────────────────────────────────────────────────
    ok = [r for r in results if "error" not in r]
    print(f"\n{'='*70}")
    print(f"  SUMMARY REPORT")
    print(f"{'='*70}")

    # Timing
    timings = [r["timing"]["total_ms"] for r in ok]
    plan_times = [r["timing"]["plan_ms"] for r in ok]
    retrieve_times = [r["timing"]["retrieve_ms"] for r in ok]
    if timings:
        print(f"\n  ── Timing (ms) ──")
        print(f"  {'ID':5} {'type':8} {'plan':>6} {'retrieve':>9} {'rerank':>7} {'total':>7}  origin")
        print(f"  {'-'*60}")
        for r in ok:
            t = r["timing"]
            print(f"  {r['id']:5} {r['query_type_actual']:8} {t['plan_ms']:>6} {t['retrieve_ms']:>9} "
                  f"{t['rerank_ms']:>7} {t['total_ms']:>7}  {r['origin']}")
        print(f"  {'':5} {'AVG':8} {sum(plan_times)//len(plan_times):>6} "
              f"{sum(retrieve_times)//len(retrieve_times):>9} "
              f"{'':>7} {sum(timings)//len(timings):>7}")
        print(f"  {'':5} {'MAX':8} {max(plan_times):>6} {max(retrieve_times):>9} {'':>7} {max(timings):>7}")
        print(f"  {'':5} {'MIN':8} {min(plan_times):>6} {min(retrieve_times):>9} {'':>7} {min(timings):>7}")

    # Query type accuracy
    type_ok = sum(1 for r in ok if r["type_match"])
    print(f"\n  ── Query Type Accuracy ──")
    print(f"  Correct: {type_ok}/{len(ok)} ({100*type_ok//max(1,len(ok))}%)")
    for r in ok:
        if not r["type_match"]:
            print(f"  ❌ {r['id']}: expected={r['query_type_expected']} got={r['query_type_actual']} — {r['query'][:50]}")

    # Source distribution
    print(f"\n  ── Source Distribution (raw chunks) ──")
    all_sources = {}
    for r in ok:
        for src, cnt in r["retrieval"]["source_stats"].items():
            all_sources[src] = all_sources.get(src, 0) + cnt
    for src, cnt in sorted(all_sources.items()):
        print(f"  {src:12}: {cnt} chunks total across {len(ok)} queries")

    # v3 signals
    print(f"\n  ── v3 Signals ──")
    q_with_table = [r for r in ok if r["v3_signals"]["table_chunks_in_final"] > 0]
    q_with_required = [r for r in ok if r["required_targets_set"]]
    q_with_wiki = [r for r in ok if r["v3_signals"]["wiki_chunks_in_final"] > 0]
    print(f"  Queries with table chunks in final: {len(q_with_table)}/{len(ok)}")
    for r in q_with_table:
        print(f"    {r['id']}: {r['v3_signals']['table_chunk_sources']}")
    print(f"  Queries with required_targets set: {len(q_with_required)}/{len(ok)}")
    for r in q_with_required:
        print(f"    {r['id']}: {r['required_targets_values']}")
    print(f"  Queries with wiki chunks in final: {len(q_with_wiki)}/{len(ok)}")

    # Top-3 chunks per query
    print(f"\n  ── Top Chunks per Query ──")
    for r in ok:
        print(f"\n  {r['id']} [{r['query_type_actual']}] {r['query'][:55]}")
        for i, c in enumerate(r.get("top3_chunks", [])[:3]):
            dt = f"[{c['doc_type']}] " if c.get("doc_type") else ""
            print(f"    #{i+1} score={c['score']} {dt}{c['tool']} | {c['source'][:40]}")
            print(f"        {c['text_preview']}")

    # Save results
    out_path = "tests/benchmark_v3_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n  Full results saved → {out_path}")
    print(f"{'='*70}")


if __name__ == "__main__":
    asyncio.run(main())
