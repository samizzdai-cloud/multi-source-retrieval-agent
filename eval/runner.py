"""Experiment runner — execute retrieval pipeline with full trace capture.

Reuses the production pipeline (plan_query → fan-out → EvaluationLayer →
rerank_and_filter) without copying code.  Captures Phase 1/2/3 traces for
downstream grading.

Usage:
    from eval.runner import run_experiment
    from eval.config import BASELINE
    results = asyncio.run(run_experiment(BASELINE, queries))
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from eval.config import ExperimentConfig

logger = logging.getLogger(__name__)

# Lazy imports to keep eval/ self-contained at import time
_ROOT = Path(__file__).parent.parent


def _ensure_env():
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env", override=True)
    # Patch module-level cached env vars that may have stale values
    import os
    try:
        import feishu_read_tool.search as _fs
        _fs.FEISHU_UAT = os.getenv("FEISHU_UAT", "")
    except ImportError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Single-query execution with full trace
# ─────────────────────────────────────────────────────────────────────────────

async def _run_one(
    query_obj: Dict[str, Any],
    config: "ExperimentConfig",
) -> Dict[str, Any]:
    """Run a single query through the full pipeline, returning a rich trace."""
    from tools.query_planner import plan_query
    from tools.evaluator import EvaluationLayer
    from tools.reranker import rerank_and_filter
    from tools.models import UnifiedChunk
    from tools.embedding import EmbeddingCache
    from tools.feishu_retrieve import feishu_retrieve
    from tools.bailian_retrieve import bailian_retrieve_unified
    from tools.google_retrieve import google_retrieve
    from tools.notion_retrieve import notion_retrieve

    SOURCE_REGISTRY = {
        "feishu": feishu_retrieve,
        "bailian": bailian_retrieve_unified,
        "google": google_retrieve,
        "notion": notion_retrieve,
    }

    query = query_obj["query"]
    query_id = query_obj["id"]
    available_sources = config.available_sources
    ec = config.eval_config
    t0 = time.monotonic()

    # ── Phase 1: Query Planning ──────────────────────────────────────────
    t_plan = time.monotonic()
    planner_kwargs = {"max_sub_queries": config.planner_overrides.get("max_sub_queries", 4)}
    plan = await asyncio.to_thread(plan_query, query, available_sources, **planner_kwargs)
    plan_ms = int((time.monotonic() - t_plan) * 1000)

    focuses = [f for fg in plan.focus_groups for f in fg.focuses if f]

    # ── Phase 2: Fan-out + EvaluationLayer ───────────────────────────────
    dashscope_key = os.getenv("DASHSCOPE_API_KEY", "")
    cache = EmbeddingCache(api_key=dashscope_key, enabled=bool(dashscope_key))
    focus_embed_task = asyncio.create_task(cache.precompute_focuses(focuses))

    task_map: Dict[asyncio.Task, str] = {}
    task_group_map: Dict[asyncio.Task, int] = {}
    # Sub-query tracking: task -> (sq_query, target, fg_idx, sq_idx)
    task_sq_map: Dict[asyncio.Task, Dict[str, Any]] = {}

    # ── Retrieval overrides ──────────────────────────────────────────
    ro = getattr(config, "retrieval_overrides", {}) or {}

    feishu_uat = os.getenv("FEISHU_UAT", "")
    feishu_doc_pool = None
    feishu_table_pool = None
    feishu_page_size = ro.get("feishu_search_page_size", 20)

    if feishu_uat and "feishu" in available_sources:
        try:
            from tools.feishu_doc_pool import FeishuDocPool, PoolConfig
            # Build overridden PoolConfig if any feishu_* keys exist
            base_cfg = PoolConfig.for_query_type(plan.query_type)
            if any(k.startswith("feishu_") for k in ro):
                base_cfg.top_n_docs = ro.get("feishu_top_n_docs", base_cfg.top_n_docs)
                base_cfg.max_per_doc = ro.get("feishu_max_per_doc", base_cfg.max_per_doc)
                base_cfg.max_output = ro.get("feishu_max_output", base_cfg.max_output)
            feishu_doc_pool = FeishuDocPool(
                uat=feishu_uat, query_type=plan.query_type,
                pool_config_override=base_cfg,
            )
            # 注入 EmbeddingCache → DocPool 混合评分可用
            feishu_doc_pool.set_embedding_cache(cache, focuses)
        except Exception:
            pass
        if plan.query_type == "structured":
            try:
                from tools.table_pool import TablePool
                tb = ro.get("feishu_table_budget")
                feishu_table_pool = TablePool(
                    uat=feishu_uat,
                    max_sheets=tb or 3,
                    max_bitables=tb or 3,
                )
            except Exception:
                pass

    for gi, fg in enumerate(plan.focus_groups):
        for sq_idx, sq in enumerate(fg.sub_queries):
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
                    kwargs["page_size"] = feishu_page_size
                    if feishu_doc_pool:
                        kwargs["doc_pool"] = feishu_doc_pool
                    if feishu_table_pool:
                        kwargs["table_pool"] = feishu_table_pool
                elif target == "bailian":
                    if sq.tags:
                        kwargs["tags"] = sq.tags
                    if sq.indexes:
                        kwargs["indexes"] = sq.indexes
                    kwargs["query_type"] = plan.query_type
                    # Inject bailian retrieval overrides
                    if ro.get("bailian_rerank_top_n"):
                        kwargs["rerank_top_n_override"] = ro["bailian_rerank_top_n"]
                    if ro.get("bailian_rerank_min_score") is not None:
                        kwargs["rerank_min_score_override"] = ro["bailian_rerank_min_score"]
                task = asyncio.create_task(retriever(sq.query, **kwargs))
                task_map[task] = target
                task_group_map[task] = gi
                # Track sub_query info for this task
                task_sq_map[task] = {
                    "sq_query": sq.query,
                    "target": target,
                    "fg_idx": gi,
                    "sq_idx": sq_idx,
                    "tags": sq.tags,
                }

    t_retrieve = time.monotonic()
    evaluator = EvaluationLayer(ec, verbose=False)
    all_chunks, source_stats, focus_hits, task_results = await evaluator.collect(
        task_map,
        focuses=focuses,
        task_group_map=task_group_map,
        embedding_cache=cache,
        focus_groups=plan.focus_groups,
        query_type=plan.query_type,
    )
    retrieve_ms = int((time.monotonic() - t_retrieve) * 1000)

    # Build per-sub_query results
    sq_results: List[Dict[str, Any]] = []
    for task, sq_info in task_sq_map.items():
        task_id = id(task)
        task_result = task_results.get(task_id, {})
        # 兼容旧格式（int）和新格式（dict）
        if isinstance(task_result, dict):
            chunk_count = task_result.get("chunk_count", 0)
            elapsed_ms = task_result.get("elapsed_ms", 0)
        else:
            chunk_count = task_result
            elapsed_ms = 0
        sq_results.append({
            "sq_query": sq_info["sq_query"],
            "target": sq_info["target"],
            "fg_idx": sq_info["fg_idx"],
            "sq_idx": sq_info["sq_idx"],
            "tags": sq_info["tags"],
            "chunk_count": chunk_count,
            "elapsed_ms": elapsed_ms,  # 相对于 retrieve 开始的完成时间
            "is_zero": chunk_count == 0,
        })

    # ── Phase 3: Rerank ──────────────────────────────────────────────────
    t_rerank = time.monotonic()
    await focus_embed_task
    if cache.enabled:
        await cache.wait_pending()
    sim_matrix = cache.get_sim_matrix(focuses, all_chunks) if cache.enabled else None

    reranker_kw = {
        "top_k": config.reranker_overrides.get("top_k_chunks", ec.top_k_chunks),
        "focuses": focuses or None,
        "min_per_focus": config.reranker_overrides.get("min_per_focus_select", ec.min_per_focus_select),
        "precomputed_sim_matrix": sim_matrix,
        "emb_weight": config.reranker_overrides.get("emb_weight", ec.emb_weight),
        "kw_weight": config.reranker_overrides.get("kw_weight", ec.kw_weight),
        "query_type": plan.query_type,
        "source_weight": config.reranker_overrides.get("source_weight", ec.source_weight),
    }
    final_chunks, focus_coverages = rerank_and_filter(all_chunks, **reranker_kw)
    rerank_ms = int((time.monotonic() - t_rerank) * 1000)
    total_ms = int((time.monotonic() - t0) * 1000)

    # ── Build trace ──────────────────────────────────────────────────────
    def _chunk_to_dict(c: UnifiedChunk, idx: int) -> Dict[str, Any]:
        return {
            "idx": idx,
            "score": round(c.score, 4),
            "tier": c.confidence_tier,
            "source_tool": c.source_tool,
            "source_name": c.source_name[:80],
            "heading_path": (c.heading_path or "")[:100],
            "text": c.text[:300],
            "metadata_subset": {
                k: v for k, v in c.metadata.items()
                if k in ("index_name", "rrf_group", "focus_overlap", "sim_emb",
                         "kw_overlap", "affinity", "content_type", "source_score",
                         "table_bonus")
            },
        }

    trace = {
        "query_id": query_id,
        "query": query,
        "config_name": config.name,
        "timing": {
            "plan_ms": plan_ms,
            "retrieve_ms": retrieve_ms,
            "rerank_ms": rerank_ms,
            "total_ms": total_ms,
        },
        "plan": {
            "query_type": plan.query_type,
            "time_filter": plan.time_filter,
            "num_focus_groups": len(plan.focus_groups),
            "focus_groups": [
                {
                    "focuses": fg.focuses,
                    "required_targets": fg.required_targets,
                    "sub_queries": [
                        {"query": sq.query, "targets": sq.targets,
                         "tags": sq.tags, "indexes": sq.indexes}
                        for sq in fg.sub_queries
                    ],
                }
                for fg in plan.focus_groups
            ],
        },
        "retrieval": {
            "raw_chunk_count": len(all_chunks),
            "source_stats": dict(source_stats),
            "focus_hits": dict(focus_hits),
            # Per-sub_query results for efficiency analysis
            "sq_results": sq_results,
            "sq_zero_rate": round(sum(1 for sq in sq_results if sq["is_zero"]) / max(1, len(sq_results)), 3),
        },
        "rerank": {
            "final_chunk_count": len(final_chunks),
            "funnel_rate": round(len(final_chunks) / max(1, len(all_chunks)), 3),
            "focus_coverage": [
                {
                    "focus": fc.focus,
                    "hits": fc.hits,
                    "target": fc.target,
                    "satisfied": fc.satisfied,
                    "avg_sim_emb": fc.avg_sim_emb,
                    "avg_sim_kw": fc.avg_sim_kw,
                    "top_sources": fc.top_sources,
                    "confidence_level": fc.confidence_level,
                }
                for fc in focus_coverages
            ] if focus_coverages else [],
            # Quality signals summary
            "quality_signals": {
                "low_confidence_count": sum(1 for fc in focus_coverages if fc.confidence_level == "low") if focus_coverages else 0,
                "avg_sim_emb_overall": round(sum(fc.avg_sim_emb for fc in focus_coverages) / max(1, len(focus_coverages)), 3) if focus_coverages else 0,
                "quality_warning": any(fc.confidence_level == "low" for fc in focus_coverages) if focus_coverages else False,
            } if focus_coverages else {},
        },
        "retrieval_overrides_applied": ro,
        "final_chunks": [_chunk_to_dict(c, i) for i, c in enumerate(final_chunks)],
        # Phase 1 wide_recall: capture ALL raw chunks for grading reranker funnel loss
        "raw_chunks_all": [_chunk_to_dict(c, i) for i, c in enumerate(all_chunks)],
        # annotation from eval dataset
        "annotations": {
            "query_type_expected": query_obj.get("query_type_expected", ""),
            "difficulty": query_obj.get("difficulty", ""),
            "project": query_obj.get("project", ""),
            "expected_focus_themes": query_obj.get("expected_focus_themes", []),
            "expected_entities": query_obj.get("expected_entities", []),
        },
    }
    return trace


# ─────────────────────────────────────────────────────────────────────────────
# Experiment runner
# ─────────────────────────────────────────────────────────────────────────────

async def run_experiment(
    config: "ExperimentConfig",
    queries: List[Dict[str, Any]],
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run all queries through the pipeline and save traces.

    Returns:
        {
            "config": {...},
            "timestamp": "...",
            "traces": [...],      # per-query trace dicts
            "aggregate": {...},   # summary stats
        }
    """
    from eval.config import ExperimentConfig  # type hint only

    _ensure_env()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if output_dir is None:
        output_dir = _ROOT / ".eval_output" / config.name / ts
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save config snapshot
    config.save(output_dir / "config.json")

    traces: List[Dict[str, Any]] = []
    total_queries = len(queries)
    for idx, q in enumerate(queries, 1):
        qid = q.get("id", "?")
        print(f"\n[{idx}/{total_queries}] Processing query #{qid}: {q.get('query', '')[:50]}...")
        repeats = []
        for r in range(config.repeat):
            try:
                trace = await _run_one(q, config)
                repeats.append(trace)
                # Print timing summary
                timing = trace.get("timing", {})
                ret = trace.get("retrieval", {})
                print(f"  ✓ Done in {timing.get('total_ms', 0)}ms | "
                      f"plan={timing.get('plan_ms', 0)}ms, retrieve={timing.get('retrieve_ms', 0)}ms | "
                      f"raw_chunks={ret.get('raw_chunk_count', 0)}, sq_zero={ret.get('sq_zero_rate', 0):.0%}")
            except Exception as e:
                logger.error("Query id=%s repeat=%d failed: %s", q.get("id"), r, e)
                repeats.append({"query_id": q.get("id"), "error": str(e)})
                print(f"  ✗ Failed: {e}")
        # If repeat > 1, store all; otherwise flatten
        if config.repeat == 1:
            traces.append(repeats[0])
        else:
            traces.append({"query_id": q.get("id"), "runs": repeats})

    # ── Aggregate stats ──────────────────────────────────────────────────
    timing_totals = [t["timing"]["total_ms"] for t in traces if "timing" in t]
    final_counts = [t["rerank"]["final_chunk_count"] for t in traces if "rerank" in t]
    raw_counts = [t["retrieval"]["raw_chunk_count"] for t in traces if "retrieval" in t]

    def _stats(vals):
        if not vals:
            return {"mean": 0, "min": 0, "max": 0, "count": 0}
        import statistics
        return {
            "mean": round(statistics.mean(vals), 1),
            "min": min(vals),
            "max": max(vals),
            "stddev": round(statistics.stdev(vals), 1) if len(vals) > 1 else 0,
            "count": len(vals),
        }

    aggregate = {
        "total_queries": len(queries),
        "successful": len(timing_totals),
        "failed": len(queries) - len(timing_totals),
        "timing_ms": _stats(timing_totals),
        "final_chunks": _stats(final_counts),
        "raw_chunks": _stats(raw_counts),
    }

    result = {
        "config": config.to_dict(),
        "timestamp": ts,
        "output_dir": str(output_dir),
        "traces": traces,
        "aggregate": aggregate,
    }

    # Save full result
    (output_dir / "experiment_result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str)
    )

    # Save per-query traces individually for grader access
    traces_dir = output_dir / "traces"
    traces_dir.mkdir(exist_ok=True)
    for t in traces:
        qid = t.get("query_id", "unknown")
        (traces_dir / f"q{qid}.json").write_text(
            json.dumps(t, indent=2, ensure_ascii=False, default=str)
        )

    return result
