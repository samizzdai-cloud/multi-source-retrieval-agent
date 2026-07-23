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
import os
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

from tools.core.models import (
    BailianIndexCatalog,
    ChunkEntry,
    RetrieveConfig,
    SourceAgentConfig,
    SourceConfig,
)
from tools.core.query_planner import plan_query
from tools.core.global_planner import PlannerFailure, plan_global
from tools.core.planner_runner import (
    NormalizedPlan,
    build_default_validators,
    normalize_plan,
)

from agents.source_agent_base import Focus, SourceAgent, SourceAgentResult, SubQuery
from tools.core.eval_layer import build_eval_layer
from tools.core.focus_board import FocusBoard
from tools.core.gtm_profile import GTMProfileRepository, ProfileGovernanceAgent, build_shadow_report
from tools.core.final_assembler import (
    assemble_final_chunks,
    enforce_focus_quota,
    render_final_payload,
)
from tools.core.evidence_role_funnel import current as current_role_funnel
from tools.core.global_coverage import snapshot_global_coverage
from tools.core.global_plan_eval import evaluate_merged_plan


logger = logging.getLogger(__name__)


def _apply_runtime_config(sc: SourceConfig) -> None:
    """把显式 SourceConfig 注入运行时依赖，优先于环境变量。"""
    google_api_key = sc.get("google_api_key", "GOOGLE_API_KEY")
    google_credentials_path = sc.get("google_credentials_path", "GOOGLE_CREDENTIALS_PATH")
    google_token_path = sc.get("google_token_path", "GOOGLE_TOKEN_PATH")

    # DeepSeek: google_api_key 可复用为 DEEPSEEK_API_KEY（兼容中转/统一 API key 场景）
    if google_api_key:
        os.environ.setdefault("DEEPSEEK_API_KEY", google_api_key)

    from tools.bailian.bailian_retrieve import configure_bailian_runtime
    configure_bailian_runtime(
        workspace_id=sc.get("bailian_workspace_id", "BAILIAN_WORKSPACE_ID"),
        access_key_id=sc.get("bailian_access_key_id", "ALIBABA_CLOUD_ACCESS_KEY_ID"),
        access_key_secret=sc.get("bailian_access_key_secret", "ALIBABA_CLOUD_ACCESS_KEY_SECRET"),
        index_map=sc.get("bailian_index_map", "BAILIAN_INDEX_MAP"),
        index_id=sc.get("bailian_index_id", "BAILIAN_INDEX_ID"),
    )

    if google_credentials_path:
        os.environ["GOOGLE_CREDENTIALS_PATH"] = google_credentials_path
    if google_token_path:
        os.environ["GOOGLE_TOKEN_PATH"] = google_token_path


# ─────────────────────────────────────────────────────────────────────
# Planner v1 (legacy fallback)：仅产 flat focuses，dispatch 时各源走老 plan_only_LLM
# ─────────────────────────────────────────────────────────────────────
def _prepare_focuses_v1(
    query: str,
    available_sources: List[str],
    bailian_allowed_indexes: Optional[List[str]] = None,
) -> List[Focus]:
    """老 v1 路径：``query_planner.plan_query`` → ``List[Focus(text, targets)]``。

    仅在 v2 ``global_planner`` 整盘失败或 ``force_v1=True`` 时使用；
    各源 ``plan_only_LLM`` 会基于 focus.text 自主产出 sub_queries（与重构前一致）。
    """
    try:
        plan = plan_query(
            query,
            available_sources,
            bailian_allowed_indexes=bailian_allowed_indexes,
            session_context="",
        )
    except Exception as e:
        logger.warning("plan_query v1 failed (%s), fallback to single focus", e)
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
_prepare_focuses = _prepare_focuses_v1
_flatten_focuses = _prepare_focuses_v1


# ─────────────────────────────────────────────────────────────────────
# Planner v2.2：global_planner + planner_runner 双层（替代 v1 主链）
# ─────────────────────────────────────────────────────────────────────
def _prepare_plan(
    query: str,
    available_sources: List[str],
    config: RetrieveConfig,
    resolution_id: str = "",
) -> NormalizedPlan:
    """v2.2 主入口：global_planner 直出 GlobalPlan → runner 校验/转译 → NormalizedPlan。

    决策路径（按优先级）：
      1. ``config.global_planner.force_v1=True`` → 直接走 v1（不调 v2 LLM）
      2. 尝试 v2 ``plan_global``；若抛 ``PlannerFailure`` 且 ``fallback_to_v1=True`` → 走 v1
      3. runner.normalize_plan 后若所有 focus 在所有源都进了 replan_needed（即整盘
         语义偏差）且 ``fallback_to_v1=True`` → 走 v1
      4. 其它 → 返 NormalizedPlan（含 pre_planned_by_source + replan_needed）
    """
    gp_cfg = config.global_planner
    catalog = config.bailian_catalog
    profile_resolution = GTMProfileRepository().resolve(
        query, config.profile_rerank, resolution_id=resolution_id,
    )

    def attach(plan: NormalizedPlan) -> NormalizedPlan:
        plan.profile_resolution = profile_resolution
        plan.validation_warnings.extend(profile_resolution.warnings)
        return plan

    # 路径 1：硬开关
    if gp_cfg.force_v1:
        logger.info("global_planner: force_v1=True → using v1 path")
        return attach(_normalize_v1_to_plan(query, available_sources, catalog))

    # 路径 2：v2 LLM
    try:
        plan = plan_global(
            query=query,
            enabled_sources=available_sources,
            cfg=gp_cfg,
            bailian_catalog=catalog,
            session_context="",
            profile_context=profile_resolution.planner_prompt_block(),
        )
    except PlannerFailure as e:
        if gp_cfg.fallback_to_v1:
            logger.warning(
                "global_planner v2 failed (%s) → safe single-focus fallback",
                e,
            )
            return attach(_safe_single_focus_plan(query, available_sources))
        # 不允许回退：抛出整盘失败，retrieve_agent 上层会做兜底（单 focus 广播）
        raise

    validators = build_default_validators(bailian_catalog=catalog, enabled_sources=available_sources)
    normalized = normalize_plan(plan, validators, enabled_sources=available_sources)

    # 路径 3：整盘语义偏差 —— 所有 source 都没拿到任何 pre_planned sub_query
    all_empty = all(not v for v in normalized.pre_planned_by_source.values())
    if all_empty:
        if gp_cfg.fallback_to_v1:
            logger.warning(
                "global_planner v2 produced 0 pre_planned sub_queries across all sources "
                "(rejected=%d) → fallback_to_v1",
                normalized.rejected_count,
            )
            return attach(_safe_single_focus_plan(query, available_sources))
        logger.warning(
            "global_planner v2 produced 0 pre_planned sub_queries; fallback disabled, "
            "will rely on per-focus replan via plan_only_LLM"
        )

    return attach(normalized)


def _safe_single_focus_plan(query: str, available_sources: List[str]) -> NormalizedPlan:
    """Failure fallback that preserves user vocabulary and never invents entities."""
    focus = Focus(focus_id="focus_0", text=query, targets=list(available_sources))
    structured_markers = ("数据", "GMV", "CVR", "ROAS", "销量", "销售", "流量", "UTM", "库存", "预算")
    query_type = "structured" if any(marker.casefold() in query.casefold() for marker in structured_markers) else "concept"
    return NormalizedPlan(
        focuses=[focus],
        pre_planned_by_source={
            source: [SubQuery(query=query, target_focus="focus_0", query_type=query_type,
                              sub_query_id=f"{source}:focus_0:safe")]
            for source in available_sources
        },
        replan_needed={source: [] for source in available_sources},
        rejected_count=0,
    )


def _normalize_v1_to_plan(
    query: str,
    available_sources: List[str],
    bailian_catalog: Optional[BailianIndexCatalog],
) -> NormalizedPlan:
    """v1 路径包装成 NormalizedPlan：focuses 全部进 ``replan_needed``，
    让各源走老 plan_only_LLM；行为与重构前完全一致。

    与"完全不传 kwarg"的微妙差异：
    - 不传 kwarg 时 SourceAgent._plan 走纯老路径（无 replan_focuses）
    - 这里走 replan_focuses 路径——_plan 会调 _plan_via_plan_only_llm(replan_focuses)
    两者最终都跑老 plan_only_LLM，语义等价；走 replan_focuses 在日志上更清晰
    （能区分"v2 失败回落"与"v1 原生"）。
    """
    bailian_allowed = bailian_catalog.allowed if bailian_catalog else None
    focuses = _prepare_focuses_v1(query, available_sources, bailian_allowed)
    replan_needed: Dict[str, List[Focus]] = {s: [] for s in available_sources}
    for f in focuses:
        for tgt in f.targets:
            if tgt in replan_needed:
                replan_needed[tgt].append(f)
    return NormalizedPlan(
        focuses=focuses,
        pre_planned_by_source={s: [] for s in available_sources},
        replan_needed=replan_needed,
        rejected_count=0,
    )


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
def _timeout_result(name: str, agent: SourceAgent, timeout_s: float) -> SourceAgentResult:
    """Preserve compact progress trace when cancellation ends a source run."""
    trace = list(getattr(agent, "_trace_events", []) or [])
    trace.append({"event": "timeout", "source": name, "timeout_s": timeout_s})
    return SourceAgentResult(
        source=name, top_chunks=[], loops_used=0, elapsed_s=timeout_s, retrieval_trace=trace,
    )


async def _dispatch_sources(
    sources: Dict[str, SourceAgent],
    focuses: List[Focus],
    focus_board: Optional[FocusBoard] = None,
    pre_planned_by_source: Optional[Dict[str, List[SubQuery]]] = None,
    replan_needed: Optional[Dict[str, List[Focus]]] = None,
    profile_resolution: object | None = None,
) -> List[SourceAgentResult]:
    """按 focus.targets 把 focuses 路由到对应 SourceAgent 后并发执行。

    每源超时各自独立（``SourceAgent.cfg.source_timeout_s``），未命中任何 focus
    的源被整体跳过（返回空结果但保留在 per_source 里，供调用方统计）。

    P4: 在每源完成后立即向 ``focus_board`` 回填，跨源共享 focus 满足状态。

    v2.2 新增：
    - ``pre_planned_by_source``: 该源 planner_runner 已校验通过的 SubQuery 列表
    - ``replan_needed``: 该源被 validator 整 focus 整源 reject 的 Focus 列表
    两者都为 None 时回退老 v1 行为（SourceAgent._plan 走老 plan_only_LLM）。
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
        # v2.2 注入位
        pre_planned = (pre_planned_by_source or {}).get(name)
        replan = (replan_needed or {}).get(name)
        if not fs and not pre_planned and not replan:
            logger.info("[%s] no focus routed, skip", name)
            return SourceAgentResult(source=name, top_chunks=[], loops_used=0, elapsed_s=0.0)
        timeout_s = agent.cfg.source_timeout_s
        # P4: 把 focus_board 注入 source_agent（plan_eval focus-aware 用）
        if focus_board is not None:
            setattr(agent, "_focus_board", focus_board)
        setattr(agent, "_profile_resolution", profile_resolution)
        logger.info(
            "[%s] dispatch: focuses=%d pre_planned_sq=%d replan_focuses=%d",
            name, len(fs), len(pre_planned or []), len(replan or []),
        )
        try:
            result = await asyncio.wait_for(
                agent.run(
                    fs,
                    pre_planned_sub_queries=pre_planned,
                    replan_focuses=replan,
                ),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning("[%s] timed out after %.1fs", name, timeout_s)
            drain_timeout_s = agent.cfg.cancel_drain_timeout_s
            remaining = await agent.drain_inflight(timeout_s=drain_timeout_s)
            result = _timeout_result(name, agent, timeout_s)
            result.retrieval_trace.append({
                "event": "cancel_drain", "source": name,
                "remaining_sync_io": remaining, "drain_timeout_s": drain_timeout_s,
            })
            return result
        except Exception as e:
            logger.exception("[%s] failed: %s", name, e)
            return SourceAgentResult(
                source=name, top_chunks=[], loops_used=0, elapsed_s=timeout_s,
                retrieval_trace=[{
                    "event": "source_error", "source": name,
                    "error_type": type(e).__name__, "error": str(e)[:160],
                }],
            )
        finally:
            if focus_board is not None:
                setattr(agent, "_focus_board", None)
            setattr(agent, "_profile_resolution", None)
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
    profile_governance: ProfileGovernanceAgent | None = None

    model_config = {"arbitrary_types_allowed": True}

    async def aclose(self) -> None:
        """Drain request I/O and release every source-owned executor."""
        for source in self.sources.values():
            await source.drain_inflight(timeout_s=10.0)
            await source.aclose()

    @override
    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        start = time.monotonic()
        resolution_id = f"res_{uuid.uuid4().hex}"

        # 0. 读 query
        query = ""
        if ctx.user_content and ctx.user_content.parts:
            query = "\n".join(p.text for p in ctx.user_content.parts if p.text).strip()

        if not query:
            yield self._make_event(ctx, {"query": "", "chunks": []})
            return

        enabled = list(self.sources.keys())
        logger.info("RetrieveAgent: query=%r, sources=%s", query, enabled)
        yield self._make_text_event(
            ctx,
            f"[retrieve] start preset={self.config.preset} sources={','.join(enabled)}",
        )

        # 1. Global plan (v2.2: global_planner + planner_runner)
        plan_started = time.perf_counter()

        # feishu open_id: 配置优先注入；无配置时不请求，lazy 到 FeishuSourceAgent 内部按需获取
        if "feishu" in self.sources:
            configured_open_id = self.source_config.get("feishu_open_id", "FEISHU_OPEN_ID")
            if configured_open_id:
                self.sources["feishu"]._current_open_id = configured_open_id
                logger.debug("RetrieveAgent: Injected configured Feishu open_id")

        # Google Drive 顶级空间懒加载：仅当 SourceAgent 内部分发到 personal_recent
        # 类型 sub_query 时才发起 list_drive_spaces_async，普通 query 不付出此代价。

        normalized: NormalizedPlan = await asyncio.to_thread(
            _prepare_plan,
            query,
            enabled,
            self.config,
            resolution_id,
        )
        focuses = normalized.focuses
        if not focuses:
            # planner 没产任何 focus（极端 fallback 也失败）→ 单 focus 广播兜底
            logger.warning("RetrieveAgent: planner produced 0 focuses, broadcasting single focus")
            focuses = [Focus(focus_id="focus_0", text=query, targets=list(enabled))]
            normalized = NormalizedPlan(
                focuses=focuses,
                pre_planned_by_source={s: [] for s in enabled},
                replan_needed={s: list(focuses) for s in enabled},
                rejected_count=0,
            )

        logger.info(
            "TIMING retrieve.prepare_plan focuses=%d pre_planned=%s replan_needed=%s rejected=%d ms=%.1f",
            len(focuses),
            {s: len(v) for s, v in normalized.pre_planned_by_source.items()},
            {s: len(v) for s, v in normalized.replan_needed.items()},
            normalized.rejected_count,
            (time.perf_counter() - plan_started) * 1000.0,
        )
        yield self._make_text_event(
            ctx,
            f"[retrieve] plan focuses={len(focuses)} total_ms={(time.monotonic() - start) * 1000:.0f}",
        )

        # 2. Dispatch（每源超时独立走 SourceAgent.cfg.source_timeout_s）
        # P4: 实例化 FocusBoard，跨源共享 focus 满足状态（plan_eval focus-aware 与早停依赖）
        focus_board = FocusBoard.from_focuses(focuses, quota=self.config.focus_quota)
        dispatch_started = time.perf_counter()
        per_source = await _dispatch_sources(
            self.sources,
            focuses,
            focus_board=focus_board,
            pre_planned_by_source=normalized.pre_planned_by_source,
            replan_needed=normalized.replan_needed,
            profile_resolution=normalized.profile_resolution,
        )

        planner_trace = [{
            "event": "planner_validation_warning",
            "resolution_id": resolution_id,
            "warning": warning,
        } for warning in normalized.validation_warnings]
        profile_resolution = normalized.profile_resolution
        if profile_resolution is not None:
            planner_trace.append({
                "event": "profile_resolution",
                "resolution_id": resolution_id,
                "snapshot_id": profile_resolution.snapshot_id,
                "registry_snapshot_id": profile_resolution.registry_snapshot_id,
                "registry_state": profile_resolution.registry_state,
                "registry_included_count": profile_resolution.registry_included_count,
                "registry_excluded_count": profile_resolution.registry_excluded_count,
                "exact_entity_count": len(profile_resolution.exact_entity_matches),
                "exact_expression_count": len(profile_resolution.exact_expression_matches),
                "semantic_candidate_count": len(profile_resolution.semantic_expression_candidates),
                "planner_entity_count": len(profile_resolution.planner_pack.entities),
                "planner_expression_count": len(profile_resolution.planner_pack.expressions),
            })
        for focus in focuses:
            focus_queries = [
                sq for rows in normalized.pre_planned_by_source.values()
                for sq in rows if sq.target_focus == focus.focus_id
            ]
            planner_trace.append({
                "event": "planner_focus", "resolution_id": resolution_id,
                "focus": focus.focus_id, "objective": focus.text,
                "targets": list(focus.targets),
                "query_ids": [sq.sub_query_id for sq in focus_queries],
            })
        operational_events = planner_trace + [
            {**event, "resolution_id": event.get("resolution_id") or resolution_id}
            for result in per_source
            for event in (result.retrieval_trace or [])
            if isinstance(event, dict)
        ]

        # Governance is projected only after final assembly so Delta extraction
        # can join fallback/read/judgment to retained evidence.
        if self.profile_governance is None:
            self.profile_governance = ProfileGovernanceAgent()
        # FocusBoard is a dispatch/readiness control only.  It must not turn
        # an unsatisfied source quota into a final semantic `noise_only`
        # verdict; merged plan-eval below is the completion authority.
        logger.debug("FocusBoard dispatch snapshot: %s", focus_board.snapshot())
        logger.info(
            "TIMING retrieve.dispatch sources=%d ms=%.1f",
            len(self.sources),
            (time.perf_counter() - dispatch_started) * 1000.0,
        )
        source_counts = ", ".join(f"{r.source}:{len(r.top_chunks)}" for r in per_source)
        yield self._make_text_event(
            ctx,
            f"[retrieve] dispatch sources={len(self.sources)} chunks=({source_counts}) total_ms={(time.monotonic() - start) * 1000:.0f}",
        )

        # 3. Merge
        merge_started = time.perf_counter()
        merged = _merge_across_sources(per_source)
        observer = current_role_funnel()
        if observer is not None:
            observer.observe_chunks("global_retained", merged)
        logger.info(
            "TIMING retrieve.merge merged=%d ms=%.1f",
            len(merged),
            (time.perf_counter() - merge_started) * 1000.0,
        )
        operational_events.extend(snapshot_global_coverage(
            merged, focuses, self.config.focus_quota, stage="merged_pre_global_eval",
        ))
        global_plan_eval = evaluate_merged_plan(
            merged,
            focuses,
            self.config.focus_quota,
            {result.source: result.retrieval_trace or [] for result in per_source},
        )
        operational_events.append({
            **global_plan_eval.trace_event(), "resolution_id": resolution_id,
        })
        if global_plan_eval.decision == "continue":
            # The decision is explicit but not yet executable: a global retry
            # needs query-signature ownership so it cannot repeat completed
            # source I/O.  Keep it visible instead of silently launching a
            # second whole-source run.
            operational_events.append({
                "event": "global_replan_deferred",
                "resolution_id": resolution_id,
                "focus_ids": list(global_plan_eval.replan_focus_ids),
                "reason": "query_signature_dedup_not_implemented",
            })
        yield self._make_text_event(
            ctx,
            f"[retrieve] merge merged_chunks={len(merged)} total_ms={(time.monotonic() - start) * 1000:.0f}",
        )

        # 3b. 出口选择：
        # - lite/balanced: 只走规则装填，不触发 final assembler LLM
        # - deep_dive: 跳过 final assembler，直接走 eval_layer
        assemble_started = time.perf_counter()
        final_top_k = self.config.final_top_k(len(focuses))
        focus_quota = self.config.focus_quota

        if self.config.eval_layer_active:
            eval_layer = build_eval_layer(
                self.config.eval_layer_effective_mode,
                model=self.config.eval_model_override or self.config.MODEL,
            )
            # LlmEvalLayer.rank() 是同步阻塞调用（含 LLM RTT），通过 to_thread 不阻塞事件循环；
            # RuleEvalLayer.rank() 亦是同步，同样走 to_thread 保持一致（开销可忽略）。
            selected = await asyncio.to_thread(eval_layer.rank, merged, focuses, final_top_k)
            # A resilient Rule fallback is not a healthy LLM verdict. Keep
            # body-free provider attempt telemetry in the operational trace.
            operational_events.extend({
                **event, "resolution_id": resolution_id,
            } for event in list(getattr(eval_layer, "trace_events", []) or []))
            logger.info(
                "TIMING retrieve.eval_layer candidates=%d picked=%d cap=%d mode=%s ms=%.1f",
                len(merged), len(selected), final_top_k, self.config.eval_layer_effective_mode,
                (time.perf_counter() - assemble_started) * 1000.0,
            )
            assemble_path = f"eval_layer:{self.config.eval_layer_effective_mode}"
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
            assemble_path = "rule"
        # LLM global rank is ordering, not permission to silently break a
        # Focus evidence quota.  This deterministic guard consumes only
        # already judged evidence and records any cap-impossible gap.
        selected, quota_events = enforce_focus_quota(
            selected, merged, focuses, final_top_k, focus_quota,
        )
        operational_events.extend({
            **event, "resolution_id": resolution_id,
        } for event in quota_events)
        final_global_plan_eval = evaluate_merged_plan(
            selected,
            focuses,
            self.config.focus_quota,
            {result.source: result.retrieval_trace or [] for result in per_source},
        )
        operational_events.append({
            **final_global_plan_eval.trace_event(),
            "stage": "final_post_selection",
            "resolution_id": resolution_id,
        })
        yield self._make_text_event(
            ctx,
            f"[retrieve] assemble path={assemble_path} picked={len(selected)} cap={final_top_k} total_ms={(time.monotonic() - start) * 1000:.0f}",
        )

        elapsed_ms = int((time.monotonic() - start) * 1000)
        _log_observation(merged, per_source, focuses, elapsed_ms)
        payload = render_final_payload(query, selected, focuses)
        operational_events.extend(snapshot_global_coverage(
            selected, focuses, self.config.focus_quota, stage="final_post_global_eval",
        ))
        if observer is not None:
            observer.observe_chunks("final", selected)
        operational_events.extend({
            "event": "final_retained", "resolution_id": resolution_id,
            "chunk_id": chunk.id, "tier": chunk.tier,
            "focuses": sorted(chunk.target_focuses),
            "query_ids": list((chunk.metadata or {}).get("sub_query_ids") or
                              (chunk.metadata or {}).get("selected_sub_query_ids") or []),
            "route": str((chunk.metadata or {}).get("route") or
                         (chunk.metadata or {}).get("route_hint") or ""),
        } for chunk in selected if chunk.tier in {"critical", "important"})
        try:
            shadow_trace = await asyncio.wait_for(
                self.profile_governance.observe_events(operational_events), timeout=0.1,
            )
        except Exception as exc:
            logger.warning("GTM profile governance outbox append skipped/late: %s", exc)
            operational_events.append({
                "event": "governance_outbox_error", "resolution_id": resolution_id,
                "error_type": type(exc).__name__,
            })
            shadow_trace = build_shadow_report(per_source)
        logger.info("GTM profile shadow trace events=%s", shadow_trace["event_counts"])
        operational_trace = {
            "mode": "operational", "resolution_id": resolution_id,
            "events": operational_events,
            "governance_projection": {
                "mode": shadow_trace.get("mode"),
                "event_counts": shadow_trace.get("event_counts", {}),
            },
        }
        logger.info(
            "RetrieveAgent: done in %dms, picked=%d / merged=%d (cap=%d)",
            elapsed_ms, len(selected), len(merged), final_top_k,
        )

        yield self._make_event(ctx, payload, elapsed_ms=elapsed_ms, retrieval_trace=operational_trace)

    def _make_text_event(self, ctx: InvocationContext, text: str) -> Event:
        return Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            content=genai_types.Content(
                role="model",
                parts=[genai_types.Part(text=text)],
            ),
        )

    def _make_event(self, ctx: InvocationContext, payload: Dict, elapsed_ms: Optional[int] = None,
                    retrieval_trace: Optional[Dict] = None) -> Event:
        result_json = json.dumps(payload, ensure_ascii=False, default=str)
        chunks = payload.get("chunks") or []
        summary = (
            f"[retrieve] done chunks={len(chunks)} elapsed_ms={elapsed_ms}"
            if elapsed_ms is not None
            else f"[retrieve] done chunks={len(chunks)}"
        )
        return Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            content=genai_types.Content(
                role="model",
                parts=[genai_types.Part(text=summary)],
            ),
            actions=EventActions(state_delta={
                "retrieval_result": result_json,
                # Body-free, evaluation/governance-only companion state.  It
                # is deliberately outside retrieval_result so AnswerAgent sees
                # the stable full-chunk contract unchanged.
                **({"retrieval_trace": json.dumps(retrieval_trace, ensure_ascii=False, default=str)}
                   if retrieval_trace is not None else {}),
            }),
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
    os.environ["RAG_PRESET"] = cfg.preset
    _apply_runtime_config(sc)

    # 注入 eval-stage LLM 模型（覆盖 source_agent 的 eval_only/plan_only/plan_eval +
    # final_assembler LLM）。空字符串 reset 回 llm_contracts._DEFAULT_MODEL。
    # 必须在下面 SourceAgent.__init__ 之前调用——LlmAgent 在 __init__ 时把 model 烘焙进实例。
    from tools.core import llm_contracts as _llm_contracts
    _llm_contracts.set_model(cfg.eval_model_override)

    # 注入 DeepSeek thinking 配置 (flash 模型默认禁用 thinking)
    eval_thinking = cfg.eval_thinking
    if eval_thinking is not None:
        _llm_contracts.set_thinking_config(eval_thinking.to_litellm_kwargs())
    # else: 保持 _THINKING_CONFIG=None, build_llm_model() 返回普通字符串

    # Patch ADK LiteLlm response_format: DeepSeek 只支持 json_object
    _llm_contracts._ensure_response_format_patch()

    from agents.bailian.bailian_source_agent import BailianSourceAgent
    from agents.google.google_source_agent import GoogleSourceAgent
    from agents.notion.notion_source_agent import NotionSourceAgent
    from tools.bailian.bailian_retrieve import get_all_configured_indexes

    sources: Dict[str, SourceAgent] = {}
    # v2.2：bailian_catalog 在构建期一次性派生，复用给 SourceAgent 与 GlobalPlanner
    bailian_allowed_resolved: List[str] = []
    bailian_symbol_to_kb: Dict[str, str] = {}
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

            bailian_allowed_resolved = list(allowed)
            bailian_symbol_to_kb = {k: v for k, v in all_configured.items() if k in allowed}

            sources[src] = BailianSourceAgent(
                cfg=sa_cfg,
                allowed_indexes=allowed,
            )
        elif src == "feishu":
            sources[src] = _build_feishu_source_agent(sa_cfg, sc, preset=cfg.preset)
        elif src == "google":
            # OAuth is lazy: agent construction and mock smoke must work with
            # no credentials or Google auth extras installed.
            sources[src] = GoogleSourceAgent(cfg=sa_cfg)
        elif src == "notion":
            # Phase 4 骨架：底层 notion_retrieve 是 stub，调用会返回空
            sources[src] = NotionSourceAgent(cfg=sa_cfg)
        else:
            logger.warning("source %r not implemented yet, skipping", src)

    if not sources:
        raise RuntimeError("RetrieveAgent: no sources enabled")

    # v2.2：把 bailian symbolic index 信息固化到 RetrieveConfig.bailian_catalog，
    # global_planner 装 prompt 时用；调用方若已显式注入则尊重外部覆盖。
    if cfg.bailian_catalog is None and "bailian" in sources:
        cfg.bailian_catalog = BailianIndexCatalog(
            allowed=bailian_allowed_resolved,
            symbol_to_kb=bailian_symbol_to_kb,
        )

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
        # lite preset：block_api 分流文档只调 list_document_blocks，跳过
        # sheet/bitable/whiteboard/comments 扩展 API（节省 99991400 共享桶预算）
        doc_pool = FeishuDocPool(uat=uat, lite_block_only=(preset == "lite"))
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
