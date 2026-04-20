"""RetrieveAgent v1——多源并行检索编排器（spec_plan.md v1 §2）。

职责：
1. **Global Plan**: 用现有 ``tools.query_planner.plan_query`` 产出 focus_groups，
   扁平化为 ``List[Focus(focus_id, text)]``（全局 focuses）。
2. **SourceDispatcher**: 并发启动 enabled_sources 的 SourceAgent.run(focuses)，
   ``asyncio.gather(..., return_exceptions=True)`` 保护单源故障不拖垮全局。
3. **MergeStage**: 跨源去重合并（同 ChunkEntry.id 走 ``merge``），按 focus_id 分组输出。
4. **State 写回**: ``retrieval_result`` 兼容 AnswerAgent 的旧 schema
   （chunks[].text / source_name / heading_path / confidence_tier）。

Phase 1：只启用 bailian；eval_layer 关闭；min=max=1。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import AsyncGenerator, Dict, List, Optional

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.genai import types as genai_types
from typing_extensions import override

from tools.models import ChunkEntry, RetrieveConfig, SourceAgentConfig, SourceConfig
from tools.query_planner import plan_query

from agents.source_agent_base import Focus, SourceAgent, SourceAgentResult
from agents.sub_agents.eval_layer import build_eval_layer


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Global planner：复用 plan_query，扁平化 focus_groups → List[Focus]
# ─────────────────────────────────────────────────────────────────────
def _flatten_focuses(query: str, available_sources: List[str]) -> List[Focus]:
    """调用 query_planner，把 ``focus_groups[{focuses, targets}]`` 扁平化为 focuses。

    v2 Global Planner 只负责「语义拆解 + 源路由」：
    - 每个 FG 的 ``focuses`` 是一组语义 focus 文本
    - 每个 FG 的 ``targets`` 是这组 focus 应该查的源（AND 路由，非 required_targets）

    SourceAgent 各自的 plan_only LLM 再基于 focus.text + 源专业规则产出
    sub_queries（含 tags / indexes / time_filter），不依赖 planner 下发 hints。
    """
    try:
        plan = plan_query(query, available_sources, session_context="")
    except Exception as e:
        logger.warning("plan_query failed (%s), fallback to single focus", e)
        return [Focus(focus_id="focus_0", text=query, targets=list(available_sources))]

    focuses: List[Focus] = []
    idx = 0
    for fg in plan.focus_groups:
        fg_targets = [t for t in (fg.targets or []) if t in available_sources]
        if not fg_targets:
            fg_targets = list(available_sources)
        for text in fg.focuses:
            focuses.append(Focus(
                focus_id=f"focus_{idx}",
                text=text,
                targets=list(fg_targets),
            ))
            idx += 1

    if not focuses:
        return [Focus(focus_id="focus_0", text=query, targets=list(available_sources))]
    return focuses


# ─────────────────────────────────────────────────────────────────────
# MergeStage：跨源去重 + 按 focus 分组 + 渲染 AnswerAgent 兼容 JSON
# ─────────────────────────────────────────────────────────────────────
_TIER_RANK = {"critical": 0, "important": 1, "noise": 2, None: 3}


def _merge_across_sources(
    results: List[SourceAgentResult],
) -> List[ChunkEntry]:
    """按 ChunkEntry.id 跨源合并，保留 tier 最高的版本。"""
    pool: Dict[str, ChunkEntry] = {}
    for r in results:
        for c in r.top_chunks:
            existing = pool.get(c.id)
            pool[c.id] = existing.merge(c) if existing else c
    # 按 tier 优先、score 次之排序
    return sorted(
        pool.values(),
        key=lambda c: (_TIER_RANK.get(c.tier, 3), -(c.score or 0.0)),
    )


def _render_for_answer(
    query: str,
    merged: List[ChunkEntry],
    per_source: List[SourceAgentResult],
    focuses: List[Focus],
    elapsed_ms: int,
) -> Dict:
    """渲染成 AnswerAgent 兼容的 RetrievalResult 形状。

    chunks 字段映射（与旧 UnifiedChunk 对齐）：
      text           = ChunkEntry.content
      source_name    = metadata["source_name"]
      source_url     = metadata["source_url"]
      source_tool    = ChunkEntry.source
      heading_path   = metadata["heading_path"]
      score          = ChunkEntry.score
      confidence_tier= ChunkEntry.tier
      metadata       = {...} + {target_focuses, sub_query, chunk_id}
    """
    chunks_out = []
    for c in merged:
        meta = dict(c.metadata or {})
        chunks_out.append({
            "text": c.content,
            "source_name": meta.get("source_name", ""),
            "source_url": meta.get("source_url", ""),
            "source_tool": c.source,
            "heading_path": meta.get("heading_path", ""),
            "score": c.score or 0.0,
            "confidence_tier": c.tier or "",
            "metadata": {
                **meta,
                "chunk_id": c.id,
                "sub_query": c.sub_query,
                "target_focuses": sorted(c.target_focuses),
            },
        })

    source_stats = {r.source: len(r.top_chunks) for r in per_source}

    # focus_coverage：每个 focus_id → 命中 chunk 数
    focus_coverage = {f.focus_id: 0 for f in focuses}
    for c in merged:
        for fid in c.target_focuses:
            if fid in focus_coverage:
                focus_coverage[fid] += 1

    # [S2.3] per-source × per-focus 召回分布
    # 用 per_source（各源原始 top_chunks，未被 eval_layer 截断）归因，
    # 对 Sprint 3 S3.1 「零召回 focus 降级」最有参考价值；
    # merged 维度由 focus_coverage 覆盖，两者互补。
    #
    # 结构：{focus_id: {source: count, ...}, ...}
    # 形状稳定 —— 所有 focus × 所有 source 都初始化为 0，
    # 便于消费方（eval 脚本、日志 grep）直接迭代，不必防缺键。
    enabled_sources = [r.source for r in per_source]
    chunks_by_focus: Dict[str, Dict[str, int]] = {
        f.focus_id: {src: 0 for src in enabled_sources} for f in focuses
    }
    for r in per_source:
        for c in r.top_chunks:
            for fid in c.target_focuses:
                if fid in chunks_by_focus and r.source in chunks_by_focus[fid]:
                    chunks_by_focus[fid][r.source] += 1

    # 零召回警报：一眼可见哪些 (focus, source) 对没命中 —— Sprint 3 降级判据。
    zero_pairs = [
        f"{fid}/{src}"
        for fid, per_src in chunks_by_focus.items()
        for src, n in per_src.items()
        if n == 0
    ]
    if zero_pairs:
        logger.info(
            "RetrieveAgent: chunks_by_focus zero-recall pairs=%s (total=%d/%d)",
            zero_pairs,
            len(zero_pairs),
            len(chunks_by_focus) * max(len(enabled_sources), 1),
        )

    return {
        "query": query,
        "chunks": chunks_out,
        "source_stats": source_stats,
        "total_sources_queried": len(per_source),
        "total_chunks_before_filter": sum(source_stats.values()),
        "total_time_ms": elapsed_ms,
        "focus_coverage": focus_coverage,
        "chunks_by_focus": chunks_by_focus,   # [S2.3] 观测指标
        "focuses": [{"focus_id": f.focus_id, "text": f.text} for f in focuses],
        # plan 字段保留占位（AnswerAgent 没强依赖结构，只偶尔用）
        "plan": None,
    }


# ─────────────────────────────────────────────────────────────────────
# SourceDispatcher
# ─────────────────────────────────────────────────────────────────────
async def _dispatch_sources(
    sources: Dict[str, SourceAgent],
    focuses: List[Focus],
) -> List[SourceAgentResult]:
    """按 focus.targets 把 focuses 路由到对应 SourceAgent 后并发执行。

    每源超时各自独立（``SourceAgent.cfg.source_timeout_s``），未命中任何 focus
    的源被整体跳过（返回空结果但保留在 per_source 里，供调用方统计）。
    """
    # 按源拆 focuses
    per_source_focuses: Dict[str, List[Focus]] = {name: [] for name in sources}
    for f in focuses:
        # 若 focus.targets 为空，视为 broadcast 到所有源（planner 没下发路由时的兜底）
        targets = f.targets or list(sources.keys())
        for t in targets:
            if t in per_source_focuses:
                per_source_focuses[t].append(f)

    async def _one(name: str, agent: SourceAgent) -> SourceAgentResult:
        fs = per_source_focuses.get(name) or []
        if not fs:
            logger.info("[%s] no focus routed, skip", name)
            return SourceAgentResult(source=name, top_chunks=[], loops_used=0, elapsed_s=0.0)
        timeout_s = agent.cfg.source_timeout_s
        try:
            return await asyncio.wait_for(agent.run(fs), timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.warning("[%s] timed out after %.1fs", name, timeout_s)
        except Exception as e:
            logger.exception("[%s] failed: %s", name, e)
        return SourceAgentResult(source=name, top_chunks=[], loops_used=0, elapsed_s=timeout_s)

    return await asyncio.gather(*[_one(n, a) for n, a in sources.items()])


# ─────────────────────────────────────────────────────────────────────
# RetrieveAgent（ADK BaseAgent）
# ─────────────────────────────────────────────────────────────────────
class RetrieveAgent(BaseAgent):
    """v1 RetrieveAgent：多源并行检索编排器。

    通过构造参数注入 ``RetrieveConfig`` + 已装配好的 ``sources`` dict。
    """

    # Pydantic (ADK BaseAgent) 可持有的字段
    config: RetrieveConfig = RetrieveConfig()
    sources: Dict[str, SourceAgent] = {}      # source_name → SourceAgent
    source_config: SourceConfig = SourceConfig()

    model_config = {"arbitrary_types_allowed": True}

    @override
    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        start = time.monotonic()

        # 0. 读 query
        query = ""
        if ctx.user_content and ctx.user_content.parts:
            query = "\n".join(p.text for p in ctx.user_content.parts if p.text).strip()

        if not query:
            yield self._make_event(ctx, {"query": "", "chunks": []})
            return

        enabled = list(self.sources.keys())
        logger.info("RetrieveAgent: query=%r, sources=%s", query, enabled)

        # 1. Global plan → focuses
        focuses = await asyncio.to_thread(_flatten_focuses, query, enabled)
        logger.info("RetrieveAgent: %d focuses produced", len(focuses))

        # 2. Dispatch（每源超时独立走 SourceAgent.cfg.source_timeout_s）
        per_source = await _dispatch_sources(self.sources, focuses)

        # 3. Merge
        merged = _merge_across_sources(per_source)

        # 3b. EvalLayer（Phase 4b）：按 (tier, relevance, coverage) 公式重排 + min-keep 截断
        if self.config.eval_layer_enabled and merged:
            try:
                layer = build_eval_layer(self.config.eval_layer_mode)
                before = len(merged)
                merged = layer.rank(merged, focuses, top_k=self.config.eval_layer_top_k)
                logger.info(
                    "RetrieveAgent: eval_layer mode=%s %d→%d chunks",
                    self.config.eval_layer_mode, before, len(merged),
                )
            except Exception as e:
                logger.exception("eval_layer failed, fallback to merged: %s", e)

        elapsed_ms = int((time.monotonic() - start) * 1000)

        payload = _render_for_answer(query, merged, per_source, focuses, elapsed_ms)
        logger.info(
            "RetrieveAgent: done in %dms, merged=%d chunks, source_stats=%s",
            elapsed_ms, len(merged), payload["source_stats"],
        )

        yield self._make_event(ctx, payload)

    def _make_event(self, ctx: InvocationContext, payload: Dict) -> Event:
        result_json = json.dumps(payload, ensure_ascii=False, default=str)
        return Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            content=genai_types.Content(
                role="model",
                parts=[genai_types.Part(text="")],
            ),
            actions=EventActions(state_delta={"retrieval_result": result_json}),
        )


# ─────────────────────────────────────────────────────────────────────
# 工厂：按 RetrieveConfig 装配好 RetrieveAgent
# ─────────────────────────────────────────────────────────────────────
def build_retrieve_agent(
    config: Optional[RetrieveConfig] = None,
    source_config: Optional[SourceConfig] = None,
    name: str = "retrieve_v1",
) -> RetrieveAgent:
    """按配置装配 SourceAgent 字典。

    v2：Global Planner 只出 focus_groups(focuses, targets)，各源 plan_only LLM
    自主产出 sub_queries + tags/indexes/time_filter。不再有 tag_strategy 开关。
    """
    cfg = config or RetrieveConfig(enabled_sources=["bailian"])
    sc = source_config or SourceConfig(load_from_env=True)

    from agents.bailian_source_agent import BailianSourceAgent
    from agents.google_source_agent import GoogleSourceAgent
    from agents.notion_source_agent import NotionSourceAgent
    from tools.bailian_retrieve import get_configured_indexes

    sources: Dict[str, SourceAgent] = {}
    for src in cfg.enabled_sources:
        sa_cfg = cfg.effective_config_for(src)
        if src == "bailian":
            allowed = list(get_configured_indexes().keys())
            sources[src] = BailianSourceAgent(
                cfg=sa_cfg,
                allowed_indexes=allowed,
            )
        elif src == "feishu":
            sources[src] = _build_feishu_source_agent(sa_cfg, sc)
        elif src == "google":
            # Phase 4 骨架：底层 google_retrieve 是 stub，调用会返回空
            sources[src] = GoogleSourceAgent(cfg=sa_cfg)
        elif src == "notion":
            # Phase 4 骨架：底层 notion_retrieve 是 stub，调用会返回空
            sources[src] = NotionSourceAgent(cfg=sa_cfg)
        else:
            logger.warning("source %r not implemented yet, skipping", src)

    if not sources:
        raise RuntimeError("RetrieveAgent: no sources enabled")

    return RetrieveAgent(
        name=name,
        description="v1 多源并行检索（spec_plan v1）",
        config=cfg,
        sources=sources,
        source_config=sc,
    )


def _build_feishu_source_agent(
    sa_cfg: SourceAgentConfig, sc: SourceConfig,
) -> SourceAgent:
    """构造 FeishuSourceAgent，并注入 FeishuDocPool / TablePool。

    query_type 固定 "mixed"（文档 + 表格都查），PoolConfig 走该模式的默认值。
    若 FEISHU_UAT 未配置，退化为无共享池模式（feishu_retrieve 内部会返回空）。
    """
    from agents.feishu_source_agent import FeishuSourceAgent
    from tools.feishu_doc_pool import FeishuDocPool
    from tools.table_pool import TablePool

    uat = sc.get("feishu_uat", "FEISHU_UAT")
    query_type = "mixed"

    doc_pool = None
    table_pool = None
    if uat:
        doc_pool = FeishuDocPool(uat=uat, query_type=query_type)
        table_pool = TablePool(uat=uat)
    else:
        logger.warning("FeishuSourceAgent: FEISHU_UAT empty, proceeding without pools")

    return FeishuSourceAgent(
        cfg=sa_cfg,
        uat=uat,
        query_type=query_type,
        doc_pool=doc_pool,
        table_pool=table_pool,
    )
