"""Google SourceAgent balanced mode 端到端测试

直接调用 GoogleSourceAgent.run()，传入单个 focus，测试完整 A-G 流程。
运行: python3 test_google_balanced_e2e.py

测试配置:
- preset=balanced (source_top_k=5, sub_query_top_n=8, loops=1)
- source_timeout_s=60s
- google_download_timeout_s=40s
- google_api_timeout_s=40s
- google_enable_gmail=True
- google_enable_gmail_attachments=True (balanced preset 覆盖)
- google_order_by_mode=viewedByMeTime desc
"""

import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

sys.path.insert(0, "/Users/javendai/Documents/workplace/Multi-source_RAG_Agent")

# 设置日志级别为 INFO，捕获所有 TIMING 和 Phase 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("test_e2e")

# ── 测试查询 ──────────────────────────────────────────────
TEST_QUERIES = [
    {"id": 1,  "query": "nubia 英国站3月份DTC渠道的GMV和订单量", "difficulty": "easy", "type": "data"},
    {"id": 4,  "query": "nubia 日本市场的KOL合作名单和已确认的博主合作进度", "difficulty": "medium", "type": "data"},
    {"id": 44, "query": "REDMAGIC 11 Pro 各站点首发销量和DTC渠道表现", "difficulty": "easy", "type": "data"},
    {"id": 48, "query": "REDMAGIC 品牌在游戏手机赛道的竞品格局和用户认知度", "difficulty": "hard", "type": "concept"},
    {"id": 55, "query": "按渠道分类对比nubia和hypershell的总体销售数据", "difficulty": "hard", "type": "data"},
]


@dataclass
class QueryResult:
    query_id: int
    query: str
    total_ms: float = 0.0
    top_chunks: List[Any] = field(default_factory=list)
    loops_used: int = 0
    error: str = ""


async def run_single(qinfo: dict) -> QueryResult:
    from agents.source_agent_base import Focus
    from agents.google.google_source_agent import GoogleSourceAgent
    from tools.core.models import RetrieveConfig, SourceAgentConfig

    r = QueryResult(query_id=qinfo["id"], query=qinfo["query"])
    started = time.perf_counter()

    try:
        # 构造配置：balanced preset + 自定义超时
        rc = RetrieveConfig(preset="balanced", enabled_sources=["google"])
        sa_cfg = rc.effective_config_for("google")

        # 覆写超时参数（测试阶段放宽）
        sa_cfg = SourceAgentConfig(
            source_timeout_s=60.0,
            google_download_timeout_s=15.0,
            google_api_timeout_s=15.0,
            # 继承 balanced preset 的其他字段
            source_top_k=sa_cfg.source_top_k,
            min_loops=sa_cfg.min_loops,
            max_loops=sa_cfg.max_loops,
            sub_query_top_n=sa_cfg.sub_query_top_n,
            google_enable_gmail=sa_cfg.google_enable_gmail,
            google_enable_gmail_attachments=sa_cfg.google_enable_gmail_attachments,
            google_enable_sheet_filter=sa_cfg.google_enable_sheet_filter,
            google_enable_excel_filter=sa_cfg.google_enable_excel_filter,
            google_enable_fulltext_search=sa_cfg.google_enable_fulltext_search,
            google_order_by_mode=sa_cfg.google_order_by_mode,
            google_max_gmail_threads_per_subquery=sa_cfg.google_max_gmail_threads_per_subquery,
            google_gmail_rerank_top_n=sa_cfg.google_gmail_rerank_top_n,
            google_doc_candidates_per_sq=sa_cfg.google_doc_candidates_per_sq,
            google_slide_candidates_per_sq=sa_cfg.google_slide_candidates_per_sq,
            google_sheet_candidates_per_sq=sa_cfg.google_sheet_candidates_per_sq,
        )

        agent = GoogleSourceAgent(cfg=sa_cfg)

        logger.info("\n" + "=" * 80)
        logger.info("[TEST START] Q%d | %s | difficulty=%s | type=%s",
                    qinfo["id"], qinfo["query"], qinfo["difficulty"], qinfo["type"])
        logger.info("[TEST CONFIG] source_timeout=%.0fs | download=%.0fs | api=%.0fs | top_k=%d | sub_query_top_n=%d",
                    sa_cfg.source_timeout_s, sa_cfg.google_download_timeout_s,
                    sa_cfg.google_api_timeout_s, sa_cfg.source_top_k, sa_cfg.sub_query_top_n)
        logger.info("[TEST CONFIG] gmail=%s | attachments=%s | order_by=%s | max_threads=%d | rerank_top_n=%d",
                    sa_cfg.google_enable_gmail, sa_cfg.google_enable_gmail_attachments,
                    sa_cfg.google_order_by_mode, sa_cfg.google_max_gmail_threads_per_subquery,
                    sa_cfg.google_gmail_rerank_top_n)

        # 构造单个 focus，直接传入 query 文本
        focus = Focus(focus_id="f1", text=qinfo["query"], targets=["google"])
        result = await agent.run([focus])

        r.total_ms = result.elapsed_s * 1000.0
        r.top_chunks = result.top_chunks
        r.loops_used = result.loops_used

        # 输出结果摘要
        logger.info("[TEST RESULT] Q%d | top_chunks=%d | loops=%d | elapsed=%.0fms",
                    qinfo["id"], len(result.top_chunks), result.loops_used, r.total_ms)

        # 按类型统计 chunks
        type_stats: Dict[str, int] = {}
        for c in result.top_chunks:
            meta = c.metadata or {}
            ctype = meta.get("type", "unknown")
            type_stats[ctype] = type_stats.get(ctype, 0) + 1
        logger.info("[TEST RESULT] Q%d | chunk_type_stats=%s", qinfo["id"], type_stats)

        # 输出每个 chunk 的摘要
        for i, c in enumerate(result.top_chunks, 1):
            meta = c.metadata or {}
            logger.info("[TEST CHUNK] Q%d #%d | score=%.3f | tier=%s | type=%s | title=%s | content=%.200s",
                        qinfo["id"], i,
                        c.score if c.score is not None else 0.0,
                        c.tier or "?",
                        meta.get("type", "?"),
                        meta.get("source_name", meta.get("file_title", "?")),
                        c.content or "")

    except Exception as e:
        logger.exception("[TEST ERROR] Q%d failed: %s", qinfo["id"], e)
        r.error = str(e)
        r.total_ms = (time.perf_counter() - started) * 1000.0

    return r


async def main():
    results: List[QueryResult] = []
    for q in TEST_QUERIES:
        r = await run_single(q)
        results.append(r)

    # 汇总报告
    logger.info("\n" + "=" * 80)
    logger.info("汇总报告")
    logger.info("=" * 80)
    for r in results:
        status = "OK" if not r.error else f"ERROR: {r.error}"
        logger.info("Q%02d [%s] | chunks=%d | loops=%d | ms=%.0f | %s",
                    r.query_id, r.query[:50], len(r.top_chunks), r.loops_used, r.total_ms, status)

    # 保存详细结果到 JSON
    report = []
    for r in results:
        report.append({
            "query_id": r.query_id,
            "query": r.query,
            "status": "error" if r.error else "ok",
            "error": r.error,
            "total_ms": r.total_ms,
            "loops_used": r.loops_used,
            "top_chunks_count": len(r.top_chunks),
            "top_chunks": [
                {
                    "score": c.score,
                    "tier": c.tier,
                    "source_name": (c.metadata or {}).get("source_name", (c.metadata or {}).get("file_title", "?")),
                    "type": (c.metadata or {}).get("type", "?"),
                    "content_preview": (c.content or "")[:300],
                }
                for c in r.top_chunks
            ],
        })

    output_path = "/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/test_google_balanced_report.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info("\n详细报告已保存至: %s", output_path)


if __name__ == "__main__":
    asyncio.run(main())
