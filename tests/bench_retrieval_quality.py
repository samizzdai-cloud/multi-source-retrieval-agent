"""检索质量与延迟评测脚本

指标：
  - 延迟（wall time ms）
  - MRR@K（Mean Reciprocal Rank）：需要 expected_top_keywords 判断相关性
  - 召回覆盖度（focus_hits 满足率）
  - 各 source 贡献比例
  - Reranker 漏斗收缩率

运行方式：
    # 仅 bailian（不启用 feishu）
    python tests/bench_retrieval_quality.py --no-feishu

    # 全源（feishu + bailian）
    python tests/bench_retrieval_quality.py

    # 仅测试指定 query id
    python tests/bench_retrieval_quality.py --ids 9,10

输出：
    .test_output/bench_YYYYMMDD_HHMMSS.json  完整结果
    终端：摘要表格
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=logging.WARNING,  # 评测时静默，只看摘要
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bench_retrieval")

from tools.query_planner import plan_query
from tools.evaluator import EvaluationLayer, EvalConfig
from tools.reranker import rerank_and_filter
from tools.models import UnifiedChunk
from tools.feishu_retrieve import feishu_retrieve
from tools.bailian_retrieve import bailian_retrieve_unified, get_configured_indexes
from tools.google_retrieve import google_retrieve
from tools.notion_retrieve import notion_retrieve
from tools.embedding import EmbeddingCache

SOURCE_REGISTRY = {
    "feishu": feishu_retrieve,
    "bailian": bailian_retrieve_unified,
    "google": google_retrieve,
    "notion": notion_retrieve,
}


# ─────────────────────────────────────────────────────────────────────────────
# MRR 计算（无标注集，用关键词启发式代替）
# ─────────────────────────────────────────────────────────────────────────────

def _is_relevant(chunk: UnifiedChunk, query: str, query_obj: dict) -> bool:
    """启发式相关性判断（无 ground truth 标注时的代理指标）。

    判断条件（任一满足）：
    1. chunk 的 confidence_tier 为 "critical"（RRF 高分）
    2. focus_overlap >= 3（与 focus 意图高度匹配）
    3. chunk 文本包含 query 中的核心实体词
    """
    if chunk.confidence_tier == "critical":
        return True
    if chunk.metadata.get("focus_overlap", 0) >= 3:
        return True
    # 从 query 提取实体词（2字以上的中文词或英文词）
    import re
    query_words = set(re.findall(r'[\w\u4e00-\u9fff]{2,}', query.lower()))
    chunk_words = set(re.findall(r'[\w\u4e00-\u9fff]{2,}', chunk.text[:300].lower()))
    overlap = len(query_words & chunk_words)
    return overlap >= 3


def compute_mrr(chunks: List[UnifiedChunk], query: str, query_obj: dict, k: int = 10) -> float:
    """计算 MRR@K。返回 0.0 表示 top-K 内无相关结果。"""
    for rank, chunk in enumerate(chunks[:k], start=1):
        if _is_relevant(chunk, query, query_obj):
            return 1.0 / rank
    return 0.0


def compute_recall_at_k(chunks: List[UnifiedChunk], query: str, query_obj: dict, k: int = 5) -> float:
    """计算 Recall@K（相关 chunk 数 / min(K, total)，无真实集时用启发式）。"""
    relevant = sum(1 for c in chunks[:k] if _is_relevant(c, query, query_obj))
    return relevant / max(1, min(k, len(chunks)))


# ─────────────────────────────────────────────────────────────────────────────
# 单 query 执行
# ─────────────────────────────────────────────────────────────────────────────

async def _run_one(
    query_obj: dict,
    available_sources: List[str],
    eval_cfg: EvalConfig,
) -> Dict[str, Any]:
    query = query_obj["query"]
    query_id = query_obj["id"]
    t0 = time.monotonic()

    # ── 1. Query Planning ────────────────────────────────────────────────────
    t_plan_start = time.monotonic()
    plan = await asyncio.to_thread(plan_query, query, available_sources)
    plan_ms = int((time.monotonic() - t_plan_start) * 1000)

    focuses = [f for fg in plan.focus_groups for f in fg.focuses if f]

    # ── 2. Fan-out + focus embedding 并行 ────────────────────────────────────
    dashscope_key = os.getenv("DASHSCOPE_API_KEY", "")
    cache = EmbeddingCache(api_key=dashscope_key, enabled=bool(dashscope_key))
    focus_embed_task = asyncio.create_task(cache.precompute_focuses(focuses))

    task_map: Dict[asyncio.Task, str] = {}
    task_group_map: Dict[asyncio.Task, int] = {}

    # 创建 FeishuDocPool（跨 sub_query 共享）
    feishu_uat = os.getenv("FEISHU_UAT", "")
    feishu_doc_pool = None
    if feishu_uat and "feishu" in available_sources:
        try:
            from tools.feishu_doc_pool import FeishuDocPool
            feishu_doc_pool = FeishuDocPool(uat=feishu_uat, query_type=plan.query_type)
        except Exception:
            pass

    for gi, fg in enumerate(plan.focus_groups):
        for sq in fg.sub_queries:
            for target in sq.targets:
                retriever = SOURCE_REGISTRY.get(target)
                if not retriever:
                    continue
                kwargs: Dict[str, Any] = {}
                if target == "feishu":
                    kwargs["uat"] = feishu_uat
                    if plan.time_filter:
                        kwargs["time_filter"] = plan.time_filter
                    kwargs["query_type"] = plan.query_type
                    if feishu_doc_pool:
                        kwargs["doc_pool"] = feishu_doc_pool
                elif target == "bailian":
                    if sq.tags:
                        kwargs["tags"] = sq.tags
                    if sq.indexes:
                        kwargs["indexes"] = sq.indexes
                    kwargs["query_type"] = plan.query_type
                task = asyncio.create_task(retriever(sq.query, **kwargs))
                task_map[task] = target
                task_group_map[task] = gi

    # ── 3. EvaluationLayer ───────────────────────────────────────────────────
    t_retrieve_start = time.monotonic()
    evaluator = EvaluationLayer(eval_cfg, verbose=False)
    all_chunks, source_stats, focus_hits, _ = await evaluator.collect(
        task_map,
        focuses=focuses,
        task_group_map=task_group_map,
        embedding_cache=cache,
        focus_groups=plan.focus_groups,
        query_type=plan.query_type,
    )
    retrieve_ms = int((time.monotonic() - t_retrieve_start) * 1000)

    # ── 4. Embedding + Rerank ─────────────────────────────────────────────────
    t_rerank_start = time.monotonic()
    await focus_embed_task
    if cache.enabled:
        await cache.wait_pending()
    sim_matrix = cache.get_sim_matrix(focuses, all_chunks) if cache.enabled else None

    final_chunks, _ = rerank_and_filter(
        all_chunks,
        top_k=eval_cfg.top_k_chunks,
        focuses=focuses or None,
        min_per_focus=eval_cfg.min_per_focus_select,
        precomputed_sim_matrix=sim_matrix,
        emb_weight=eval_cfg.emb_weight,
        kw_weight=eval_cfg.kw_weight,
        source_weight=eval_cfg.source_weight,
    )
    rerank_ms = int((time.monotonic() - t_rerank_start) * 1000)

    total_ms = int((time.monotonic() - t0) * 1000)

    # ── 5. 质量指标 ──────────────────────────────────────────────────────────
    mrr_10 = compute_mrr(final_chunks, query, query_obj, k=10)
    recall_5 = compute_recall_at_k(final_chunks, query, query_obj, k=5)

    # focus 覆盖满足率
    focus_satisfied = sum(1 for f, cnt in focus_hits.items() if cnt >= 1)
    focus_total = len(focus_hits)
    focus_coverage_rate = focus_satisfied / max(1, focus_total)

    # source 贡献分布
    src_dist: Dict[str, int] = {}
    for c in final_chunks:
        src_dist[c.source_tool] = src_dist.get(c.source_tool, 0) + 1

    # reranker 漏斗收缩率
    funnel_rate = len(final_chunks) / max(1, len(all_chunks))

    return {
        "query_id": query_id,
        "query": query[:80],
        "available_sources": available_sources,
        "timing": {
            "plan_ms": plan_ms,
            "retrieve_ms": retrieve_ms,
            "rerank_ms": rerank_ms,
            "total_ms": total_ms,
        },
        "quality": {
            "mrr_at_10": round(mrr_10, 4),
            "recall_at_5": round(recall_5, 4),
            "focus_coverage_rate": round(focus_coverage_rate, 3),
            "focus_satisfied": f"{focus_satisfied}/{focus_total}",
        },
        "volume": {
            "raw_chunks": len(all_chunks),
            "final_chunks": len(final_chunks),
            "funnel_rate": round(funnel_rate, 3),
        },
        "source_distribution": src_dist,
        "source_stats": {k.split(":")[0] if ":" in k else k: v for k, v in source_stats.items()},
        "query_type_expected": query_obj.get("query_type_expected", ""),
        "query_type_actual": plan.query_type,
        "query_type_match": query_obj.get("query_type_expected", "") == plan.query_type,
        "plan_summary": {
            "query_type": plan.query_type,
            "time_filter": plan.time_filter,
            "num_focus_groups": len(plan.focus_groups),
            "focus_groups": [
                {
                    "focuses": fg.focuses,
                    "required_targets": fg.required_targets,
                    "sub_queries": [
                        {"query": sq.query[:60], "targets": sq.targets,
                         "tags": sq.tags, "indexes": sq.indexes}
                        for sq in fg.sub_queries
                    ]
                }
                for fg in plan.focus_groups
            ],
        },
        "top5_chunks": [
            {
                "rank": i + 1,
                "score": round(c.score, 4),
                "tier": c.confidence_tier,
                "source": c.source_tool,
                "index": c.metadata.get("index_name", ""),
                "focus_overlap": c.metadata.get("focus_overlap", 0),
                "sim_emb": c.metadata.get("sim_emb", 0),
                "heading": (c.heading_path or "")[:80],
                "text": c.text[:150].replace("\n", " "),
            }
            for i, c in enumerate(final_chunks[:5])
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 摘要打印
# ─────────────────────────────────────────────────────────────────────────────

def _print_summary(results: List[Dict[str, Any]]) -> None:
    print(f"\n{'='*80}")
    print(f"{'ID':<4} {'Query':<38} {'Total':>7} {'Plan':>6} {'Retr':>6} {'MRR@10':>8} {'R@5':>6} {'Focus':>7} {'Final':>6}")
    print(f"{'─'*80}")
    mrr_list, recall_list = [], []
    for r in results:
        t = r.get("timing", {})
        q = r.get("quality", {})
        v = r.get("volume", {})
        mrr = q.get("mrr_at_10", 0)
        rec = q.get("recall_at_5", 0)
        mrr_list.append(mrr)
        recall_list.append(rec)
        print(
            f"{r['query_id']:<4} "
            f"{r['query'][:37]:<38} "
            f"{t.get('total_ms',0):>6}ms "
            f"{t.get('plan_ms',0):>5}ms "
            f"{t.get('retrieve_ms',0):>5}ms "
            f"{mrr:>8.4f} "
            f"{rec:>6.3f} "
            f"{q.get('focus_satisfied','?'):>7} "
            f"{v.get('final_chunks',0):>6}"
        )
    print(f"{'─'*80}")
    if mrr_list:
        print(f"{'MEAN':<44} {'':>13} {sum(mrr_list)/len(mrr_list):>8.4f} {sum(recall_list)/len(recall_list):>6.3f}")
    print(f"{'='*80}\n")

    # query_type 匹配统计
    qt_results = [r for r in results if "query_type_expected" in r and r.get("query_type_expected")]
    if qt_results:
        qt_match = sum(1 for r in qt_results if r.get("query_type_match"))
        print(f"query_type accuracy: {qt_match}/{len(qt_results)} ({qt_match/len(qt_results)*100:.0f}%)")
        for r in qt_results:
            if not r.get("query_type_match"):
                print(f"  ⚠️  Q{r['query_id']}: expected={r['query_type_expected']} actual={r['query_type_actual']}")
        print()

    # source 分布汇总
    all_src: Dict[str, int] = {}
    for r in results:
        for src, cnt in r.get("source_distribution", {}).items():
            all_src[src] = all_src.get(src, 0) + cnt
    total_chunks = sum(all_src.values())
    if total_chunks:
        print("Final chunk source distribution (all queries):")
        for src, cnt in sorted(all_src.items(), key=lambda x: -x[1]):
            pct = cnt / total_chunks * 100
            print(f"  {src:<12}: {cnt:>4} chunks ({pct:.1f}%)")
        print()


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

async def main(args: argparse.Namespace) -> None:
    query_file = args.query_file or "test_query_v2.json"
    test_json = ROOT / "tests" / query_file
    with open(test_json, encoding="utf-8") as f:
        data = json.load(f)

    queries = data["queries"]
    if args.ids:
        id_set = set(int(x) for x in args.ids.split(","))
        queries = [q for q in queries if q["id"] in id_set]

    # 配置可用源
    available_sources: List[str] = []
    if not args.no_feishu and os.getenv("FEISHU_UAT"):
        available_sources.append("feishu")
    if get_configured_indexes():
        available_sources.append("bailian")
    if not available_sources:
        print("⚠️  No sources available. Check .env for FEISHU_UAT / BAILIAN_INDEX_MAP.")
        return

    eval_cfg = EvalConfig(
        min_good_chunks=5,
        min_relevance_score=0.3,
        hard_timeout_seconds=15.0,
        min_sources=args.min_sources if hasattr(args, 'min_sources') else 2,
        min_per_focus=1,
        min_per_focus_select=2,
        top_k_chunks=12,
        use_embedding_for_select=True,
        embedding_sim_threshold=0.4,
        embedding_dim=512,
        emb_weight=0.4,
        kw_weight=0.15,
    )

    print(f"\n{'='*60}")
    print(f"Available sources : {available_sources}")
    print(f"min_sources       : {eval_cfg.min_sources}")
    print(f"Queries           : {[q['id'] for q in queries]}")
    print(f"{'='*60}\n")

    results = []
    for q_obj in queries:
        print(f"Running Q{q_obj['id']}: {q_obj['query'][:70]}...")
        try:
            r = await _run_one(q_obj, list(available_sources), eval_cfg)
        except Exception as e:
            logger.exception("Q%d failed: %s", q_obj["id"], e)
            r = {"query_id": q_obj["id"], "query": q_obj["query"][:80], "error": str(e)}
        results.append(r)
        # 即时输出
        t = r.get("timing", {})
        q = r.get("quality", {})
        print(f"  ✓ {t.get('total_ms',0)}ms | MRR@10={q.get('mrr_at_10',0):.4f} | "
              f"focus={q.get('focus_satisfied','?')} | "
              f"final={r.get('volume',{}).get('final_chunks',0)} chunks")
        if "top5_chunks" in r:
            for c in r["top5_chunks"][:3]:
                print(f"    [{c['rank']}] score={c['score']} tier={c['tier']} "
                      f"src={c['source']}/{c.get('index','')} overlap={c['focus_overlap']}")
                print(f"         {c['text'][:100]}")

    _print_summary(results)

    # 保存结果
    output_dir = ROOT / ".test_output"
    output_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = output_dir / f"bench_{ts}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": ts,
            "available_sources": available_sources,
            "eval_config": {
                "min_sources": eval_cfg.min_sources,
                "min_good_chunks": eval_cfg.min_good_chunks,
                "min_per_focus": eval_cfg.min_per_focus,
                "use_embedding_for_select": eval_cfg.use_embedding_for_select,
            },
            "results": results,
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"Results saved → {out_file}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Retrieval quality & latency benchmark")
    parser.add_argument("--no-feishu", action="store_true", help="Disable feishu source")
    parser.add_argument("--ids", type=str, default="", help="Comma-separated query IDs to test")
    parser.add_argument("--min-sources", type=int, default=2, help="EvalConfig min_sources (default 2)")
    parser.add_argument("--query-file", type=str, default="test_query_v2.json", help="Query file name in tests/")
    args = parser.parse_args()
    asyncio.run(main(args))
