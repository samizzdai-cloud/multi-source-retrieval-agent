"""RetrievalAgent — 多源并行检索编排器 (Custom BaseAgent)

零自然语言回复，只返回结构化 RetrievalResult JSON。
2-Phase pipeline:
  Phase 1: Query Planning (1x LLM ~1-2s)
  Phase 2: Parallel Retrieval (零 LLM, asyncio.as_completed + EvaluationLayer)
  Phase 3: Merge + RRF Rerank + top-K
"""

import asyncio
import json
import logging
import time
from dataclasses import asdict
from typing import AsyncGenerator, Callable, Coroutine, Dict, List

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.genai import types
from typing_extensions import override

from tools.models import FocusGroup, QueryPlan, RetrievalResult, SourceConfig, UnifiedChunk
from tools.query_planner import plan_query
from tools.evaluator import EvaluationLayer, EvalConfig
from tools.reranker import rerank_and_filter

# ── Retrieve 函数注册表 ───────────────────────────────────────────

from tools.feishu_retrieve import feishu_retrieve
from tools.bailian_retrieve import bailian_retrieve_unified, get_configured_indexes
from tools.google_retrieve import google_retrieve
from tools.notion_retrieve import notion_retrieve

logger = logging.getLogger(__name__)

# source_name → async retrieve function
# 每个函数签名: async def xxx(query: str, **kwargs) -> List[UnifiedChunk]
SOURCE_REGISTRY: Dict[str, Callable[..., Coroutine]] = {
    "feishu": feishu_retrieve,
    "bailian": bailian_retrieve_unified,
    "google": google_retrieve,
    "notion": notion_retrieve,
}


def _get_available_sources(sc: SourceConfig | None = None) -> List[str]:
    """返回当前已配置的可用检索源列表。基于 SourceConfig 判断，凭证为空则不启用。"""
    sc = sc or SourceConfig()
    available = []
    if sc.get("feishu_uat", "FEISHU_UAT"):
        available.append("feishu")
    if sc.get("bailian_index_map", "BAILIAN_INDEX_MAP") or get_configured_indexes():
        available.append("bailian")
    return available or ["feishu"]  # fallback


def _extract_query(ctx: InvocationContext) -> str:
    """从 InvocationContext 中提取用户查询文本。"""
    if ctx.user_content and ctx.user_content.parts:
        return "\n".join(p.text for p in ctx.user_content.parts if p.text).strip()
    return ""


class RetrievalAgent(BaseAgent):
    """多源并行检索 Agent。

    继承 ADK BaseAgent，override _run_async_impl()。
    零 LLM token 消耗（除 1x query planning）。
    输出: RetrievalResult JSON。
    """

    eval_config: EvalConfig = EvalConfig()
    source_config: SourceConfig = SourceConfig()

    @override
    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        start_time = time.monotonic()
        sc = self.source_config

        # ── 0. 读取 query ────────────────────────────────────────
        query = _extract_query(ctx)
        if not query:
            yield self._make_event(ctx, RetrievalResult(query=""))
            return

        available_sources = _get_available_sources(sc)
        logger.info("RetrievalAgent: query=%r, sources=%s", query, available_sources)

        # ── 1. Query Planning (1x LLM) ──────────────────────────
        # session_context 预留：后续可从 ctx.session.state 提取历史对话摘要
        session_context = ""
        plan = await asyncio.to_thread(plan_query, query, available_sources, session_context=session_context)
        total_sqs = sum(len(g.sub_queries) for g in plan.focus_groups)
        logger.info(
            "RetrievalAgent: plan has %d focus_groups (%d sub_queries), type=%s, time_filter=%s",
            len(plan.focus_groups), total_sqs, plan.query_type, plan.time_filter,
        )

        # ── 2. Fan-out: focus_group → sub_query × target → parallel tasks
        #
        # ── 2a. Focus embedding（提前创建，与检索并行 + 注入 DocPool）──
        focuses = [f for fg in plan.focus_groups for f in fg.focuses if f]
        dashscope_key = sc.get("dashscope_api_key", "DASHSCOPE_API_KEY")

        from tools.embedding import EmbeddingCache
        cache = EmbeddingCache(
            api_key=dashscope_key,
            enabled=self.eval_config.use_embedding_for_select,
            sim_threshold=self.eval_config.embedding_sim_threshold,
        )
        focus_embed_task = asyncio.create_task(cache.precompute_focuses(focuses))

        # feishu tasks 共享一个 FeishuDocPool（跨 sub_query 文档去重 + API 调度）
        feishu_uat = sc.get("feishu_uat", "FEISHU_UAT")
        feishu_doc_pool = None
        feishu_table_pool = None
        if feishu_uat:
            from tools.feishu_doc_pool import FeishuDocPool
            feishu_doc_pool = FeishuDocPool(uat=feishu_uat, query_type=plan.query_type)
            # 注入 EmbeddingCache → DocPool 混合评分可用
            feishu_doc_pool.set_embedding_cache(cache, focuses)
            # TablePool：跨 sub_query 共享 sheet/bitable 缓存（仅 data/mixed 时有意义）
            if plan.query_type in ("data", "mixed"):
                from tools.table_pool import TablePool
                feishu_table_pool = TablePool(uat=feishu_uat)

        task_map: Dict[asyncio.Task, str] = {}
        task_group_map: Dict[asyncio.Task, int] = {}
        for gi, fg in enumerate(plan.focus_groups):
            for sq in fg.sub_queries:
                for target in sq.targets:
                    retriever = SOURCE_REGISTRY.get(target)
                    if not retriever:
                        logger.warning("Unknown source: %s", target)
                        continue
                    kwargs = {}
                    if target == "feishu":
                        kwargs["uat"] = feishu_uat
                        if plan.time_filter:
                            kwargs["time_filter"] = plan.time_filter
                        kwargs["query_type"] = plan.query_type
                        kwargs["focus_group_idx"] = gi
                        kwargs["focuses"] = fg.focuses
                        if feishu_doc_pool:
                            kwargs["doc_pool"] = feishu_doc_pool
                        if feishu_table_pool:
                            kwargs["table_pool"] = feishu_table_pool
                    elif target == "bailian":
                        if sq.tags:
                            kwargs["tags"] = sq.tags
                        if sq.indexes:
                            kwargs["indexes"] = sq.indexes
                        kwargs["query_type"] = plan.query_type  # [E5] concept 型降低 rerank 阈值
                    coro = retriever(sq.query, **kwargs)
                    task = asyncio.create_task(coro)
                    task_map[task] = target
                    task_group_map[task] = gi

        if not task_map:
            yield self._make_event(ctx, RetrievalResult(query=query, plan=plan))
            return

        logger.info("RetrievalAgent: dispatching %d parallel tasks", len(task_map))

        # ── 3. EmbeddingCache 已在 Step 2a 创建，focus_embed_task 已启动 ──

        # ── 4. Progressive collection (EvaluationLayer) ──────────
        # Queue bridge: evaluator runs as background task; per-focus progress
        # events are yielded as each focus becomes satisfied.
        progress_queue: asyncio.Queue = asyncio.Queue()
        satisfied_set: set = set()     # focuses already reported
        satisfied_seq_box = [0]        # mutable counter (list trick for nonlocal)

        def _on_progress(source, new_chunks, all_chunks, source_stats, elapsed,
                         focus_hits=None, all_focuses=None):
            for f in (all_focuses or []):
                if f not in satisfied_set and (focus_hits or {}).get(f, 0) >= self.eval_config.min_per_focus:
                    satisfied_set.add(f)
                    satisfied_seq_box[0] += 1
                    progress_queue.put_nowait({"seq": satisfied_seq_box[0], "focus": f})

        evaluator = EvaluationLayer(self.eval_config, on_progress=_on_progress, verbose=False)
        collect_task = asyncio.create_task(
            evaluator.collect(
                task_map, focuses=focuses, task_group_map=task_group_map,
                embedding_cache=cache, focus_groups=plan.focus_groups,
                query_type=plan.query_type,
            )
        )

        while not collect_task.done():
            try:
                p = await asyncio.wait_for(progress_queue.get(), timeout=0.5)
                yield self._make_focus_event(ctx, p["seq"], p["focus"])
            except asyncio.TimeoutError:
                pass

        # drain any events produced in the final batch before collect_task finished
        while not progress_queue.empty():
            p = progress_queue.get_nowait()
            yield self._make_focus_event(ctx, p["seq"], p["focus"])

        all_chunks, source_stats, focus_hits, _ = await collect_task

        total_before_filter = len(all_chunks)

        # ── 5. 等 embedding 全部完成 → Reranker 消费 sim_matrix ──
        await focus_embed_task
        await cache.wait_pending()
        sim_matrix = cache.get_sim_matrix(focuses, all_chunks)

        final_chunks, focus_coverages = rerank_and_filter(
            all_chunks,
            top_k=self.eval_config.top_k_chunks,
            focuses=focuses or None,
            min_per_focus=self.eval_config.min_per_focus_select,
            max_per_doc_per_focus=self.eval_config.max_per_doc_per_focus,
            precomputed_sim_matrix=sim_matrix,
            emb_weight=self.eval_config.emb_weight,
            kw_weight=self.eval_config.kw_weight,
            query_type=plan.query_type,
            source_weight=self.eval_config.source_weight,
        )
        # Log detailed focus coverage for iteration analysis (zero runtime cost)
        if focus_coverages:
            unsatisfied_pct = sum(1 for fc in focus_coverages if not fc.satisfied) / len(focus_coverages) * 100
            logger.info("focus_coverage_summary: %.0f%% satisfied (%d/%d)",
                       100 - unsatisfied_pct, sum(1 for fc in focus_coverages if fc.satisfied), len(focus_coverages))

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        result = RetrievalResult(
            query=query,
            chunks=final_chunks,
            source_stats=source_stats,
            total_sources_queried=len(task_map),
            total_chunks_before_filter=total_before_filter,
            total_time_ms=elapsed_ms,
            plan=plan,
            focus_coverage=focus_hits,
        )

        logger.info(
            "RetrievalAgent: done in %dms — %d chunks (from %d raw), sources=%s",
            elapsed_ms, len(final_chunks), total_before_filter, source_stats,
        )

        yield self._make_event(ctx, result)

    def _make_event(self, ctx: InvocationContext, result: RetrievalResult) -> Event:
        """构建最终 ADK Event。
        - state_delta: 仅存 RetrievalResult JSON，供下游 AnswerAgent 读取
        - content: 简短完成状态，不暴露 JSON 给 ADK UI
        """
        result_json = json.dumps(asdict(result), ensure_ascii=False, default=str)
        state_delta = {"retrieval_result": result_json}

        return Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            content=types.Content(
                role="model",
                parts=[types.Part(text="")],
            ),
            actions=EventActions(state_delta=state_delta),
        )

    def _make_focus_event(self, ctx: InvocationContext, seq: int, focus_text: str) -> Event:
        """构建单个 focus 满足时的进度 Event（无 state_delta，纯展示）。

        去掉 focus 文本开头的动词前缀（获取/了解/追踪/分析/查找等），
        直接展示核心内容，如"Nothing 品牌 2026 年全年的产品发布规划和路..."
        """
        _LEADING_VERBS = (
            "获取", "了解", "查找", "追踪", "分析", "调查",
            "收集", "检索", "查询", "整理", "研究", "确认",
        )
        display = focus_text
        for verb in _LEADING_VERBS:
            if display.startswith(verb):
                display = display[len(verb):].lstrip(" ：:")
                break
        truncated = display[:40] + "..." if len(display) > 40 else display
        text = f"知识已调取（{seq}）：{truncated}"
        return Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            content=types.Content(
                role="model",
                parts=[types.Part(text=text)],
            ),
            actions=EventActions(),
        )
