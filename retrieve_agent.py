"""RetrieveAgent v1——多源并行检索编排器（spec_plan.md v1 §2）。

职责：
1. **Global Plan**: 用现有 ``tools.query_planner.plan_query`` 产出扁平 ``focuses[{text, targets}]``，
   转为 ``List[Focus(focus_id, text)]``（全局 focuses）。
2. **SourceDispatcher**: 并发启动 enabled_sources 的 SourceAgent.run(focuses)，
   ``asyncio.gather(..., return_exceptions=True)`` 保护单源故障不拖垮全局。
3. **MergeStage**: 跨源去重合并（同 ChunkEntry.id 走 ``merge``），按 focus_id 分组输出。
4. **State 写回**: ``retrieval_result`` 兼容 AnswerAgent 的旧 schema
   （chunks[].text / source_name / heading_path / confidence_tier）。

Preset 驱动：lite/balanced/deep_dive 三档，支持 feishu + bailian + google + notion 多源并发。
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
import time
import uuid
from typing import AsyncGenerator, Dict, List, Optional

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.genai import types as genai_types
from typing_extensions import override

from tools.core.models import ChunkEntry, RetrieveConfig, SourceAgentConfig, SourceConfig
from tools.core.query_planner import plan_query

from agents.source_agent_base import Focus, SourceAgent, SourceAgentResult
from tools.core.eval_layer import build_eval_layer
from tools.core.focus_board import FocusBoard
from tools.core.final_assembler import (
    assemble_final_chunks,
    render_final_payload,
)


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Global planner：v2.1 直接返回 flat focuses（FG 下线）
# ─────────────────────────────────────────────────────────────────────
def _prepare_focuses(
    query: str,
    available_sources: List[str],
    bailian_allowed_indexes: Optional[List[str]] = None,
) -> List[Focus]:
    """调用 query_planner，把 ``QueryPlan.focuses`` 映射到 runtime ``Focus``。

    v2.1 QueryPlan 已是 flat 结构（PlanFocus{text, targets}），本函数仅负责：
    - 补上 runtime ``focus_id``（``focus_0`` / ``focus_1`` ...）
    - 对 targets 再做一次交集兜底（planner 内已做，这里是 defense-in-depth）

    SourceAgent 各自的 plan_only LLM 再基于 focus.text + 源专业规则产出
    sub_queries（含 tags / indexes / time_filter），不依赖 planner 下发 hints。
    """
    try:
        plan = plan_query(
            query,
            available_sources,
            bailian_allowed_indexes=bailian_allowed_indexes,
            session_context="",
        )
    except Exception as e:
        logger.warning("plan_query failed (%s), fallback to single focus", e)
        return [Focus(focus_id="focus_0", text=query, targets=list(available_sources))]

    focuses: List[Focus] = []
    for idx, pf in enumerate(plan.focuses):
        targets = [t for t in (pf.targets or []) if t in available_sources]
        if not targets:
            targets = list(available_sources)
        focuses.append(Focus(
            focus_id=f"focus_{idx}",
            text=pf.text,
            targets=targets,
        ))

    if not focuses:
        return [Focus(focus_id="focus_0", text=query, targets=list(available_sources))]
    return focuses


# 兼容旧名字（防外部 import）
_flatten_focuses = _prepare_focuses


# ─────────────────────────────────────────────────────────────────────
# MergeStage：跨源去重 + 按 focus 分组 + 渲染 AnswerAgent 兼容 JSON
# ─────────────────────────────────────────────────────────────────────
_TIER_RANK = {"critical": 0, "important": 1, "noise": 2, None: 3}


def _normalize_dedupe_heading(heading: str) -> str:
    heading = (heading or "").lower().strip()
    heading = re.sub(r"\s+", " ", heading)
    return heading


def _normalize_dedupe_text(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\d[\d,./:%-]*", "0", text)
    text = re.sub(r"[$¥€£]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:420]


def _is_near_duplicate(a: ChunkEntry, b: ChunkEntry) -> bool:
    if a.source != b.source:
        return False

    meta_a = a.metadata or {}
    meta_b = b.metadata or {}
    heading_a = _normalize_dedupe_heading(str(meta_a.get("heading_path", "") or ""))
    heading_b = _normalize_dedupe_heading(str(meta_b.get("heading_path", "") or ""))
    if heading_a and heading_b and heading_a != heading_b:
        return False

    text_a = _normalize_dedupe_text(a.content or "")
    text_b = _normalize_dedupe_text(b.content or "")
    if not text_a or not text_b:
        return False
    if text_a == text_b:
        return True

    ratio = difflib.SequenceMatcher(None, text_a, text_b).ratio()
    return ratio >= 0.94


def _suppress_near_duplicates(chunks: List[ChunkEntry]) -> List[ChunkEntry]:
    deduped: List[ChunkEntry] = []
    merged_count = 0
    for chunk in chunks:
        merged = False
        for idx, kept in enumerate(deduped):
            if _is_near_duplicate(kept, chunk):
                deduped[idx] = kept.merge(chunk)
                merged = True
                merged_count += 1
                break
        if not merged:
            deduped.append(chunk)

    if merged_count:
        logger.info(
            "RetrieveAgent: near-duplicate suppression merged %d chunks (%d -> %d)",
            merged_count,
            len(chunks),
            len(deduped),
        )
    return deduped


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
    merged = sorted(
        pool.values(),
        key=lambda c: (_TIER_RANK.get(c.tier, 3), -(c.score or 0.0)),
    )
    return _suppress_near_duplicates(merged)


def _log_observation(
    merged: List[ChunkEntry],
    per_source: List[SourceAgentResult],
    focuses: List[Focus],
    elapsed_ms: int,
) -> None:
    """观测指标打日志（不进 payload）。"""
    enabled_sources = [r.source for r in per_source]
    chunks_by_focus: Dict[str, Dict[str, int]] = {
        f.focus_id: {src: 0 for src in enabled_sources} for f in focuses
    }
    for r in per_source:
        for c in r.top_chunks:
            for fid in c.target_focuses:
                if fid in chunks_by_focus and r.source in chunks_by_focus[fid]:
                    chunks_by_focus[fid][r.source] += 1
    zero_pairs = [
        f"{fid}/{src}"
        for fid, per_src in chunks_by_focus.items()
        for src, n in per_src.items()
        if n == 0
    ]
    if zero_pairs:
        logger.info(
            "RetrieveAgent: chunks_by_focus zero-recall pairs=%s (total=%d/%d)",
            zero_pairs, len(zero_pairs),
            len(chunks_by_focus) * max(len(enabled_sources), 1),
        )
    source_stats = {r.source: len(r.top_chunks) for r in per_source}
    logger.info(
        "RetrieveAgent: source_stats=%s elapsed_ms=%d merged_chunks=%d",
        source_stats, elapsed_ms, len(merged),
    )


# ─────────────────────────────────────────────────────────────────────
# SourceDispatcher
# ─────────────────────────────────────────────────────────────────────
async def _dispatch_sources(
    sources: Dict[str, SourceAgent],
    focuses: List[Focus],
    focus_board: Optional[FocusBoard] = None,
) -> List[SourceAgentResult]:
    """按 focus.targets 把 focuses 路由到对应 SourceAgent 后并发执行。

    每源超时各自独立（``SourceAgent.cfg.source_timeout_s``），未命中任何 focus
    的源被整体跳过（返回空结果但保留在 per_source 里，供调用方统计）。

    P4: 在每源完成后立即向 ``focus_board`` 回填，跨源共享 focus 满足状态。
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
        # P4: 把 focus_board 注入 source_agent（plan_eval focus-aware 用）
        if focus_board is not None:
            setattr(agent, "_focus_board", focus_board)
        try:
            result = await asyncio.wait_for(agent.run(fs), timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.warning("[%s] timed out after %.1fs", name, timeout_s)
            return SourceAgentResult(source=name, top_chunks=[], loops_used=0, elapsed_s=timeout_s)
        except Exception as e:
            logger.exception("[%s] failed: %s", name, e)
            return SourceAgentResult(source=name, top_chunks=[], loops_used=0, elapsed_s=timeout_s)
        finally:
            if focus_board is not None:
                setattr(agent, "_focus_board", None)
        # P4: 完成即上报到 board（即便 0 chunks 也走，方便 contributors 记录）
        if focus_board is not None and result.top_chunks:
            try:
                await focus_board.report(name, result.top_chunks)
            except Exception as e:
                logger.exception("[%s] focus_board.report failed: %s", name, e)
        return result

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

        # 1. Global plan → focuses (v2.1 flat, FG retired)
        plan_started = time.perf_counter()
        
        # 将 bailian 的限制名单透传给 Global Planner
        bailian_allowed = None
        if "bailian" in self.sources:
            # self.sources["bailian"] 是 BailianSourceAgent 的实例
            bailian_allowed = getattr(self.sources["bailian"], "_allowed_indexes", None)

        # feishu open_id: 配置优先注入；无配置时不请求，lazy 到 FeishuSourceAgent 内部按需获取
        if "feishu" in self.sources:
            configured_open_id = self.source_config.get("feishu_open_id", "FEISHU_OPEN_ID")
            if configured_open_id:
                self.sources["feishu"]._current_open_id = configured_open_id
                logger.info("RetrieveAgent: Injected configured Feishu open_id")

        # Google Drive 顶级空间懒加载：仅当 SourceAgent 内部分发到 personal_recent
        # 类型 sub_query 时才发起 list_drive_spaces_async，普通 query 不付出此代价。

        focuses = await asyncio.to_thread(
            _prepare_focuses,
            query,
            enabled,
            bailian_allowed,
        )
        logger.info(
            "TIMING retrieve.prepare_focuses focuses=%d ms=%.1f",
            len(focuses),
            (time.perf_counter() - plan_started) * 1000.0,
        )
        logger.info("RetrieveAgent: %d focuses produced", len(focuses))

        # 2. Dispatch（每源超时独立走 SourceAgent.cfg.source_timeout_s）
        # P4: 实例化 FocusBoard，跨源共享 focus 满足状态（plan_eval focus-aware 与早停依赖）
        focus_board = FocusBoard.from_focuses(focuses, quota=self.config.focus_quota)
        dispatch_started = time.perf_counter()
        per_source = await _dispatch_sources(self.sources, focuses, focus_board=focus_board)
        # 兜底标记：所有源完成后未达 quota 的 focus → noise_only
        for fid in focus_board.unsatisfied_focus_ids():
            focus_board.mark_terminal(fid, status="noise_only")
        logger.info("FocusBoard final: %s", focus_board.snapshot())
        logger.info(
            "TIMING retrieve.dispatch sources=%d ms=%.1f",
            len(self.sources),
            (time.perf_counter() - dispatch_started) * 1000.0,
        )

        # 3. Merge
        merge_started = time.perf_counter()
        merged = _merge_across_sources(per_source)
        logger.info(
            "TIMING retrieve.merge merged=%d ms=%.1f",
            len(merged),
            (time.perf_counter() - merge_started) * 1000.0,
        )

        # 3b. 出口选择：
        # - lite/balanced: 只走规则装填，不触发 final assembler LLM
        # - deep_dive: 跳过 final assembler，直接走 eval_layer
        assemble_started = time.perf_counter()
        final_top_k = self.config.final_top_k(len(focuses))
        focus_quota = self.config.focus_quota

        if self.config.eval_layer_active:
            eval_layer = build_eval_layer(self.config.eval_layer_effective_mode)
            selected = eval_layer.rank(merged, focuses, final_top_k)
            logger.info(
                "TIMING retrieve.eval_layer candidates=%d picked=%d cap=%d mode=%s ms=%.1f",
                len(merged), len(selected), final_top_k, self.config.eval_layer_effective_mode,
                (time.perf_counter() - assemble_started) * 1000.0,
            )
        else:
            selected = await assemble_final_chunks(
                chunks=merged,
                focuses=focuses,
                final_top_k=final_top_k,
                focus_quota=focus_quota,
                preset=self.config.preset,
                query=query,
                llm_config=None,
                llm_invoker=None,
            )
            logger.info(
                "TIMING retrieve.assemble candidates=%d picked=%d cap=%d path=rule ms=%.1f",
                len(merged), len(selected), final_top_k,
                (time.perf_counter() - assemble_started) * 1000.0,
            )

        elapsed_ms = int((time.monotonic() - start) * 1000)
        _log_observation(merged, per_source, focuses, elapsed_ms)
        payload = render_final_payload(query, selected, focuses)
        logger.info(
            "RetrieveAgent: done in %dms, picked=%d / merged=%d (cap=%d)",
            elapsed_ms, len(selected), len(merged), final_top_k,
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
    """按配置装配 SourceAgent 字典。"""
    cfg = config or RetrieveConfig(enabled_sources=["bailian"])
    sc = source_config or SourceConfig(load_from_env=True)

    from agents.bailian.bailian_source_agent import BailianSourceAgent
    from agents.google.google_source_agent import GoogleSourceAgent
    from agents.notion.notion_source_agent import NotionSourceAgent
    from tools.bailian.bailian_retrieve import get_all_configured_indexes

    sources: Dict[str, SourceAgent] = {}
    for src in cfg.enabled_sources:
        sa_cfg = cfg.effective_config_for(src)
        if src == "bailian":
            all_configured = get_all_configured_indexes()
            allowed_str = sc.get("bailian_allowed_indexes", "BAILIAN_ALLOWED_INDEXES")
            if allowed_str:
                allowed_names = [n.strip() for n in allowed_str.split(",") if n.strip()]
                allowed = [name for name in allowed_names if name in all_configured]
            else:
                allowed = list(all_configured.keys())
                
            sources[src] = BailianSourceAgent(
                cfg=sa_cfg,
                allowed_indexes=allowed,
            )
        elif src == "feishu":
            sources[src] = _build_feishu_source_agent(sa_cfg, sc, preset=cfg.preset)
        elif src == "google":
            # 提前检查并获取 Google OAuth2 Token
            try:
                from google_workspace_tool.auth_utils import get_credentials
                from tools.google.google_retrieve import _SCOPES
                get_credentials(scopes=_SCOPES)
                logger.info("Google credentials validated successfully.")
            except Exception as e:
                logger.error("Failed to initialize Google credentials: %s", e)
                raise RuntimeError("Google Workspace auth failed. Please authorize in terminal.") from e

            # Google Workspace 检索（Drive 全文搜索 + 分块读取）
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
    sa_cfg: SourceAgentConfig, sc: SourceConfig, preset: str = "balanced",
) -> SourceAgent:
    """构造 FeishuSourceAgent，并注入 FeishuDocPool / TablePool。"""
    from agents.feishu.feishu_source_agent import FeishuSourceAgent
    from tools.feishu.feishu_doc_pool import FeishuDocPool
    from tools.feishu.table_pool import TablePool

    uat = sc.get("feishu_uat", "FEISHU_UAT")

    doc_pool = None
    table_pool = None
    if uat:
        doc_pool = FeishuDocPool(uat=uat)
        table_pool = TablePool(uat=uat)
    else:
        logger.warning("FeishuSourceAgent: FEISHU_UAT empty, proceeding without pools")

    return FeishuSourceAgent(
        cfg=sa_cfg,
        uat=uat,
        doc_pool=doc_pool,
        table_pool=table_pool,
        preset=preset,
    )

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tools.core.models import AgentConfig

def build_retrieve_agent_from_config(
    agent_config: "AgentConfig",
    name: str = "retrieve_v1",
) -> RetrieveAgent:
    """统一外部入口：从 AgentConfig 一键构建 RetrieveAgent。

    调用方只需传一个 AgentConfig，无需了解内部 RetrieveConfig / SourceConfig 分层。

    示例：
        from tools.core.models import AgentConfig
        from retrieve_agent import build_retrieve_agent_from_config

        agent = build_retrieve_agent_from_config(AgentConfig(
            preset="balanced",
            sources=["feishu", "bailian"],
            feishu_uat="t-xxx",
            bailian_indexes="nubia:hzu9kj1c5x,hypershell:pdws24t6vm",
        ))
    """
    from tools.core.models import AgentConfig  # noqa: PLC0415
    return build_retrieve_agent(
        config=agent_config.to_retrieve_config(),
        source_config=agent_config.to_source_config(),
        name=name,
    )
