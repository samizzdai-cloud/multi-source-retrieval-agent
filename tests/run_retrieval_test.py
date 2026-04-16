"""Retrieval pipeline 端到端测试脚本

读取 tests/test_query.json，对每个 query 逐环节执行检索链路并记录中间数据。
输出到 .test_output/query_XX.json 及 .test_output/summary.json。

运行方式（从项目根目录）：
    python tests/run_retrieval_test.py
"""

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

# 确保项目根目录在 sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# 加载 .env
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("retrieval_test")

from tools.query_planner import plan_query
from tools.evaluator import EvaluationLayer, EvalConfig
from tools.reranker import (
    dedup_chunks,
    deny_list_filter,
    rrf_merge,
    select_by_focus,
    apply_confidence_tiers,
    compress_chunks,
)
from tools.models import UnifiedChunk
from tools.feishu_retrieve import feishu_retrieve
from tools.bailian_retrieve import bailian_retrieve_unified, get_configured_indexes
from tools.google_retrieve import google_retrieve
from tools.notion_retrieve import notion_retrieve

SOURCE_REGISTRY = {
    "feishu": feishu_retrieve,
    "bailian": bailian_retrieve_unified,
    "google": google_retrieve,
    "notion": notion_retrieve,
}


def _get_available_sources() -> List[str]:
    available = []
    if os.getenv("FEISHU_UAT"):
        available.append("feishu")
    if get_configured_indexes():
        available.append("bailian")
    return available or ["feishu"]


def _rerank_with_stats(
    chunks: List[UnifiedChunk],
    focuses: List[str],
    precomputed_sim_matrix: "List[List[float]] | None" = None,
) -> Tuple[List[UnifiedChunk], Dict[str, Any]]:
    """逐步执行 reranker pipeline，返回 (final_chunks, stats_dict)。"""
    stats: Dict[str, Any] = {}

    stats["input"] = len(chunks)
    chunks = dedup_chunks(chunks)
    stats["after_dedup"] = len(chunks)

    chunks = deny_list_filter(chunks)
    stats["after_deny_filter"] = len(chunks)

    chunks = rrf_merge(chunks)
    stats["after_rrf"] = len(chunks)

    if focuses:
        chunks = select_by_focus(
            chunks, focuses, min_per_focus=2,
            precomputed_sim_matrix=precomputed_sim_matrix,
            emb_weight=0.4, kw_weight=0.15,
        )
        stats["after_select_by_focus"] = len(chunks)
        mode = "hybrid(emb+kw)" if precomputed_sim_matrix else "keyword"
        stats["select_mode"] = mode
        # 记录混合评分细节
        stats["sim_emb_avg"] = round(
            sum(c.metadata.get("sim_emb", 0) for c in chunks) / max(1, len(chunks)), 3
        )
        stats["sim_kw_avg"] = round(
            sum(c.metadata.get("sim_kw", 0) for c in chunks) / max(1, len(chunks)), 3
        )

    # focus_overlap 分布
    overlaps = [c.metadata.get("focus_overlap", 0) for c in chunks]
    stats["focus_overlap_distribution"] = {
        "zero": sum(1 for x in overlaps if x == 0),
        "one_to_three": sum(1 for x in overlaps if 1 <= x <= 3),
        "four_plus": sum(1 for x in overlaps if x >= 4),
    }
    any_match = any(x > 0 for x in overlaps)
    stats["any_focus_match"] = any_match

    if focuses and chunks and not any_match:
        stats["zero_match_fallback_triggered"] = True
        stats["final"] = 0
        return [], stats

    stats["zero_match_fallback_triggered"] = False

    chunks = apply_confidence_tiers(chunks)
    tier_dist: Dict[str, int] = {}
    for c in chunks:
        tier_dist[c.confidence_tier] = tier_dist.get(c.confidence_tier, 0) + 1
    stats["tier_distribution"] = tier_dist

    chunks = [c for c in chunks if c.confidence_tier != "noise"]
    stats["after_noise_filter"] = len(chunks)

    if focuses:
        chunks = compress_chunks(chunks, focuses)

    stats["final"] = len(chunks)
    return chunks, stats


def _diagnose(
    query: str,
    plan,
    source_stats: Dict[str, int],
    all_chunks: List[UnifiedChunk],
    reranker_stats: Dict[str, Any],
    available_sources: List[str],
) -> List[str]:
    issues = []

    # 各源结果为空（source_stats key 可能是 "source:query_prefix" 格式）
    for src in available_sources:
        src_total = sum(v for k, v in source_stats.items() if k.startswith(src))
        if src_total == 0:
            issues.append(f"⚠️ {src} 0 结果：关键词可能过宽或该源无相关文档")

    # zero-match fallback
    if reranker_stats.get("zero_match_fallback_triggered"):
        issues.append("🚫 Reranker zero-match fallback：所有 chunks 与 focus 完全不匹配，返回空")
    elif all_chunks and not reranker_stats.get("any_focus_match"):
        issues.append("⚠️ focus_overlap 全为 0：检索内容与 focus 意图无交集")

    # 漏斗比
    raw = reranker_stats.get("input", 0)
    final = reranker_stats.get("final", 0)
    if raw > 0 and final == raw:
        issues.append(f"ℹ️ Reranker 无过滤：{raw} 进 {final} 出，所有 chunks 均通过")
    if raw > 0 and final == 0 and not reranker_stats.get("zero_match_fallback_triggered"):
        issues.append("⚠️ Reranker 过滤后为空（非 zero-match）：可能阈值过严")

    # 对比查询实体对称性（基于 focus_groups）
    if len(plan.focus_groups) >= 2:
        entities_covered = set()
        for fg in plan.focus_groups:
            for sq in fg.sub_queries:
                entities_covered.update(sq.indexes)
        if len(entities_covered) == 1:
            issues.append(
                f"⚠️ 多个 focus_group 但只检索了 1 个实体知识库（{entities_covered}）"
            )

    # feishu query 词数检查
    for fg in plan.focus_groups:
        for sq in fg.sub_queries:
            if "feishu" in sq.targets:
                word_count = len(sq.query.split())
                if word_count > 4:
                    issues.append(
                        f"⚠️ feishu sub-query 词数过多（{word_count} 词）：'{sq.query[:40]}'"
                    )

    if not issues:
        issues.append("✅ 未检测到明显问题")

    return issues


async def _run_one(
    query_id: int,
    query: str,
    available_sources: List[str],
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "query_id": query_id,
        "query": query,
        "available_sources": available_sources,
    }
    t0 = time.monotonic()

    # ── 1. Query Planning ────────────────────────────────────────
    plan = await asyncio.to_thread(plan_query, query, available_sources)
    result["plan"] = asdict(plan)
    result["plan_analysis"] = {
        "num_focus_groups": len(plan.focus_groups),
        "query_type": plan.query_type,
        "time_filter": plan.time_filter,
        "focus_groups_summary": [
            {
                "focuses": fg.focuses,
                "sub_queries": [
                    {"query": sq.query, "targets": sq.targets, "tags": sq.tags, "indexes": sq.indexes}
                    for sq in fg.sub_queries
                ],
            }
            for fg in plan.focus_groups
        ],
    }

    # ── 2. Fan-out + focus embedding 并行启动 ────────────────────
    focuses = [f for fg in plan.focus_groups for f in fg.focuses if f]
    dashscope_key = os.getenv("DASHSCOPE_API_KEY", "")

    from tools.embedding import EmbeddingCache
    cache = EmbeddingCache(api_key=dashscope_key, enabled=bool(dashscope_key))
    focus_embed_task = asyncio.create_task(cache.precompute_focuses(focuses))

    task_map: Dict[asyncio.Task, str] = {}
    task_group_map: Dict[asyncio.Task, int] = {}

    for gi, fg in enumerate(plan.focus_groups):
        for sq in fg.sub_queries:
            for target in sq.targets:
                retriever = SOURCE_REGISTRY.get(target)
                if not retriever:
                    continue
                kwargs: Dict[str, Any] = {}
                if target == "feishu" and plan.time_filter:
                    kwargs["time_filter"] = plan.time_filter
                elif target == "bailian":
                    if sq.tags:
                        kwargs["tags"] = sq.tags
                    if sq.indexes:
                        kwargs["indexes"] = sq.indexes
                task = asyncio.create_task(retriever(sq.query, **kwargs))
                task_map[task] = f"{target}:{sq.query[:30]}"
                task_group_map[task] = gi

    # ── 3. EvaluationLayer（embedding cache 渐进填充）─────────────
    eval_config = EvalConfig(
        min_good_chunks=5,
        min_relevance_score=0.3,
        hard_timeout_seconds=15.0,
        min_sources=1,
        min_per_focus=1,
    )
    evaluator = EvaluationLayer(eval_config, verbose=True)
    await focus_embed_task  # 确保 focus embedding 就绪
    all_chunks, source_stats, focus_hits, _ = await evaluator.collect(
        task_map, focuses=focuses, task_group_map=task_group_map,
        embedding_cache=cache,
    )

    result["source_stats"] = source_stats
    result["raw_chunks_count"] = len(all_chunks)

    # 各源 score 分布（按 source_tool 聚合，与 task label 无关）
    source_quality: Dict[str, Any] = {}
    for c in all_chunks:
        src = c.source_tool  # "feishu" | "bailian" | ...
        if src not in source_quality:
            source_quality[src] = {"count": 0, "scores": [], "score_gte_05": 0}
        source_quality[src]["count"] += 1
        source_quality[src]["scores"].append(round(c.score, 3))
        if c.score >= 0.5:
            source_quality[src]["score_gte_05"] += 1
    # 规范化 source_stats：将 "source:query" 格式合并为纯 source 名
    normalized_source_stats: Dict[str, int] = {}
    for k, v in source_stats.items():
        src_key = k.split(":")[0] if ":" in k else k
        normalized_source_stats[src_key] = normalized_source_stats.get(src_key, 0) + v
    result["source_stats"] = normalized_source_stats
    result["source_quality"] = source_quality
    result["focus_coverage"] = focus_hits

    # ── 4. 等 embedding cache 完成 + 获取 sim_matrix ─────────────
    sim_matrix = None
    if cache.enabled:
        await cache.wait_pending()
        sim_matrix = cache.get_sim_matrix(focuses, all_chunks)
        logger.info("EmbeddingCache: sim_matrix ready (%s)",
                     f"{len(sim_matrix)}×{len(sim_matrix[0])}" if sim_matrix else "None")

    # ── 5. Reranker ──────────────────────────────────────────────
    final_chunks, reranker_stats = _rerank_with_stats(
        list(all_chunks), focuses, precomputed_sim_matrix=sim_matrix,
    )
    result["reranker_stats"] = reranker_stats

    result["final_chunks"] = [
        {
            "rank": i + 1,
            "score": round(c.score, 4),
            "confidence_tier": c.confidence_tier,
            "source_tool": c.source_tool,
            "source_name": c.source_name,
            "index_name": c.metadata.get("index_name", ""),
            "heading_path": (c.heading_path or "")[:120],
            "focus_overlap": c.metadata.get("focus_overlap", 0),
            "sim_emb": c.metadata.get("sim_emb", 0),
            "sim_kw": c.metadata.get("sim_kw", 0),
            "filtered_by_tags": c.metadata.get("filtered_by_tags", []),
            "text_preview": c.text[:200].replace("\n", " "),
        }
        for i, c in enumerate(final_chunks)
    ]

    # 额外分析：各知识库 chunks 分布（对比查询均衡性指标）
    index_dist: Dict[str, int] = {}
    for c in final_chunks:
        idx = c.metadata.get("index_name", c.source_tool)
        index_dist[idx] = index_dist.get(idx, 0) + 1
    result["final_chunk_index_dist"] = index_dist

    # 计划分析：tags 使用情况
    tags_used = []
    for fg in plan.focus_groups:
        for sq in fg.sub_queries:
            if sq.tags:
                tags_used.extend(sq.tags)
    result["tags_used_in_plan"] = sorted(set(tags_used))

    result["total_time_ms"] = int((time.monotonic() - t0) * 1000)

    # ── 5. Diagnosis ─────────────────────────────────────────────
    result["diagnosis"] = _diagnose(
        query, plan, source_stats, all_chunks, reranker_stats, available_sources
    )

    return result


async def main() -> None:
    test_json = ROOT / "tests" / "test_query.json"
    with open(test_json, encoding="utf-8") as f:
        data = json.load(f)

    output_dir = ROOT / ".test_output"
    output_dir.mkdir(exist_ok=True)

    available_sources = _get_available_sources()
    queries = data["queries"]

    print(f"\n{'='*60}")
    print(f"Available sources : {available_sources}")
    print(f"Queries to run    : {len(queries)}")
    print(f"Output directory  : {output_dir}")
    print(f"{'='*60}\n")

    all_results = []

    for q in queries:
        qid = q["id"]
        query_text = q["query"]
        print(f"\n{'─'*60}")
        print(f"Query #{qid}: {query_text[:80]}")
        print(f"{'─'*60}")

        try:
            result = await _run_one(qid, query_text, available_sources)
        except Exception:
            logger.exception("Query #%d failed", qid)
            result = {
                "query_id": qid,
                "query": query_text,
                "error": "See terminal for traceback",
            }

        all_results.append(result)

        out_file = output_dir / f"query_{qid:02d}.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)

        print(f"\n✅ Saved → {out_file}")
        for d in result.get("diagnosis", []):
            print(f"  {d}")

    # 汇总
    summary = {
        "available_sources": available_sources,
        "total_queries": len(all_results),
        "results": [
            {
                "query_id": r.get("query_id"),
                "query_preview": r.get("query", "")[:80],
                "total_time_ms": r.get("total_time_ms"),
                "raw_chunks": r.get("raw_chunks_count"),
                "final_chunks": r.get("reranker_stats", {}).get("final"),
                "select_mode": r.get("reranker_stats", {}).get("select_mode"),
                "focus_satisfied": sum(1 for v in r.get("focus_coverage", {}).values() if v > 0),
                "focus_total": len(r.get("focus_coverage", {})),
                "final_chunk_index_dist": r.get("final_chunk_index_dist", {}),
                "tags_used": r.get("tags_used_in_plan", []),
                "source_stats": r.get("source_stats"),
                "zero_match_fallback": r.get("reranker_stats", {}).get("zero_match_fallback_triggered"),
                "diagnosis": r.get("diagnosis", []),
            }
            for r in all_results
        ],
    }
    summary_file = output_dir / "summary.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n{'='*60}")
    print(f"📊 Summary → {summary_file}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
