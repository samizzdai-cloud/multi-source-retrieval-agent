"""SourceAgent 基类——通用 loop 骨架（plan → execute → eval）。

设计要点（spec_plan.md v1 §2 / §11 Phase 1）：
- 不是 LlmAgent 子类，而是一个"编排器"，内部调用 3 个 sub_agents（pydantic output_schema）
  拿到结构化 JSON，再结合外部 execute 函数真正跑检索
- chunk_pool: 本源 loop 间累积的 id → ChunkEntry 字典；同 id 冲突走 ``ChunkEntry.merge``
- loop 边界由 ``needs_eval`` / ``enforce_loop_bounds`` 硬约束（见 ``tools/llm_contracts``）
- 单 loop 路径（min=max=1）：plan_only → execute → eval_only，不调 plan_eval
- 多 loop 路径（max>1）：plan_only → execute → plan_eval(→continue/replan → execute)*
                            → 最后一轮 eval_only 收尾
- 子类只需提供 ``execute_sub_query(sub_query, focus_id) -> list[ChunkEntry]`` 适配器

已实现单 loop（min=max=1）和多 loop（max>1）完整路径。
LLM 调用走 ``google.adk.Runner`` 单例 + ``InMemorySessionService``（零持久化）。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import functools
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Coroutine, Dict, List, Optional

from google.adk.agents.llm_agent import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from tools.core.llm_contracts import (
    AGENT_CFG_EVAL_ONLY,
    EVAL_RUBRIC_BLOCK,
    build_llm_model,
    build_partial_eval_model,
    effective_source_top_k,
    enforce_loop_bounds,
    needs_eval,
    tiers_list_to_map,
    validate_plan_eval_output,
)
from tools.core.context_budget import ContextBudget, estimate_chunk_tokens, prompt_preview_text, split_chunks
from tools.core.models import ChunkEntry, SourceAgentConfig
from tools.core.rerank import RerankError, RerankSettings, rerank_items, select_ranked_ids
from tools.core.source_profiles import SourceProfile
from tools.core.evidence_role_funnel import current as current_role_funnel


logger = logging.getLogger(__name__)

# ── 类型别名 ────────────────────────────────────────────────────────
ExecuteFn = Callable[[str, str], Coroutine[Any, Any, List[ChunkEntry]]]
"""子类提供的 execute 适配器：(sub_query, target_focus) -> List[ChunkEntry]"""


@dataclass
class Focus:
    """全局 planner 产出的单个 focus 维度（v2 精简：只带路由，不带专业 hints）。

    ``targets``: dispatcher 读该字段决定往哪些源派活；SourceAgent 内部忽略。
    其余专业参数（tags / indexes / time_filter）由各 SourceAgent 的 plan_only
    LLM 根据 focus.text 自行产出，不在 Focus 里承载。
    """
    focus_id: str
    text: str
    targets: List[str] = field(default_factory=list)


@dataclass
class SubQuery:
    """plan_only / plan_eval 产出的一条子查询（v2：支持源特有参数）。

    ``tags`` / ``indexes`` / ``time_filter`` / ``query_type`` 等为可选字段：
    - bailian 的 plan_only LLM 会填 tags (L2 + 时效) 和 indexes
    - feishu 的 plan_only LLM 会填 time_filter（"last_N_days" 或 None）和
      query_type（"structured"/"concept"，驱动 Feishu reader 路由与召回数量）
    - google 的 plan_only LLM 可以动态产出 google_enable_fulltext (bool) 与 google_order_by (str)
    """
    query: str
    target_focus: str
    tags: List[str] = field(default_factory=list)
    indexes: List[str] = field(default_factory=list)
    time_filter: Optional[str] = None
    query_type: Optional[str] = None
    google_enable_fulltext: Optional[bool] = None
    google_order_by: Optional[str] = None
    author_filter: Optional[str] = None
    sub_query_id: str = ""
    # 只由 fallback controller 填充的派生查询；不再用于默认 dual-path。
    variants: List[str] = field(default_factory=list)
    # Bounded Profile fallback provenance for trace governance only.
    variant_origins: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ChunkRefRegistry:
    """Request-local LLM references; canonical IDs never enter an eval prompt."""
    ref_to_id: Dict[str, str]
    id_to_ref: Dict[str, str]

    @classmethod
    def build(cls, chunks: List[ChunkEntry]) -> "ChunkRefRegistry":
        ref_to_id = {f"C{index}": chunk.id for index, chunk in enumerate(chunks, 1)}
        return cls(ref_to_id=ref_to_id, id_to_ref={value: key for key, value in ref_to_id.items()})

    def resolve_eval_output(self, payload: Dict[str, Any]) -> tuple[Dict[str, Any], int]:
        """Resolve only request-local refs or an exact known canonical ID.

        ``C1`` is the normal prompt contract.  Some JSON-mode providers still
        echo a canonical ID inferred from their constrained output; accepting
        it *only when it is exactly a member of this request's pool* prevents
        a formatting quirk from discarding valid evidence, while fabricated
        IDs remain rejected.
        """
        def resolve(value: object) -> Optional[str]:
            if not isinstance(value, str):
                return None
            return self.ref_to_id.get(value) or (value if value in self.id_to_ref else None)

        out = dict(payload or {})
        malformed = 0
        raw_top = out.get("top_k_chunk_ids") or []
        top_ids: List[str] = []
        for value in raw_top:
            ref = value.get("chunk_id") if isinstance(value, dict) else value
            canonical = resolve(ref)
            if canonical:
                top_ids.append(canonical)
            else:
                malformed += 1
        tiers: List[Dict[str, Any]] = []
        raw_tiers = out.get("tiers") or []
        if not isinstance(raw_tiers, list):
            raw_tiers, malformed = [], malformed + 1
        for row in raw_tiers:
            if not isinstance(row, dict):
                malformed += 1
                continue
            ref = row.get("chunk_id")
            canonical = resolve(ref)
            if not canonical:
                malformed += 1
                continue
            tiers.append({**row, "chunk_id": canonical})
        out["top_k_chunk_ids"] = top_ids
        out["tiers"] = tiers

        # Partial eval's compact response is an evidence shortlist.  Keep
        # legacy ``tiers`` support for saved fixtures and older providers.
        compact_tiers: List[Dict[str, Any]] = []
        compact_seen: set[str] = set()
        resolved_focus_shortlists: List[Dict[str, Any]] = []
        raw_focus_shortlists = out.get("focus_shortlists") or []
        if raw_focus_shortlists and not isinstance(raw_focus_shortlists, list):
            raw_focus_shortlists, malformed = [], malformed + 1
        for row in raw_focus_shortlists:
            if not isinstance(row, dict) or not str(row.get("focus_id") or "").strip():
                malformed += 1
                continue
            focus_id = str(row["focus_id"]).strip()
            resolved_row: Dict[str, Any] = {"focus_id": focus_id}
            for field_name, tier, relevance in (
                ("admission_chunk_ids", "important", 0.65),
                ("critical_chunk_ids", "critical", 0.9),
                ("important_chunk_ids", "important", 0.7),
            ):
                values: List[str] = []
                for value in row.get(field_name) or []:
                    canonical = resolve(value)
                    if not canonical:
                        malformed += 1
                        continue
                    values.append(canonical)
                    # A chunk can support more than one focus.  Globally retain
                    # it once and union focus provenance instead of rejecting it.
                    if canonical not in compact_seen:
                        compact_seen.add(canonical)
                        compact_tiers.append({
                            "chunk_id": canonical, "tier": tier, "relevance": relevance,
                            "answered_focuses": [focus_id], "angle_tag": "",
                        })
                    else:
                        for judgment in compact_tiers:
                            if judgment["chunk_id"] == canonical:
                                judgment["answered_focuses"] = sorted(set(
                                    judgment.get("answered_focuses") or []) | {focus_id}
                                )
                                if tier == "critical":
                                    judgment["tier"], judgment["relevance"] = "critical", 0.9
                                break
                resolved_row[field_name] = values
            resolved_focus_shortlists.append(resolved_row)
        out["focus_shortlists"] = resolved_focus_shortlists
        for field_name, tier, relevance in (
            ("critical_chunk_ids", "critical", 0.9),
            ("important_chunk_ids", "important", 0.7),
        ):
            raw_values = out.get(field_name) or []
            resolved_values: List[str] = []
            for value in raw_values:
                canonical = resolve(value)
                if not canonical or canonical in compact_seen:
                    malformed += 1
                    continue
                compact_seen.add(canonical)
                resolved_values.append(canonical)
                compact_tiers.append({
                    "chunk_id": canonical, "tier": tier, "relevance": relevance,
                    "answered_focuses": [], "angle_tag": "",
                })
            out[field_name] = resolved_values
        if compact_tiers:
            out["tiers"] = compact_tiers

        ledgers: List[Dict[str, Any]] = []
        for ledger in out.get("focus_ledger") or []:
            if not isinstance(ledger, dict):
                malformed += 1
                continue
            evidence_ids: List[str] = []
            for value in ledger.get("evidence_chunk_ids") or []:
                canonical = resolve(value)
                if canonical:
                    evidence_ids.append(canonical)
                else:
                    malformed += 1
            ledgers.append({**ledger, "evidence_chunk_ids": evidence_ids})
        out["focus_ledger"] = ledgers
        return out, malformed


@dataclass
class SourceAgentResult:
    """SourceAgent 收尾产物（供 MergeStage 使用）。"""
    source: str
    top_chunks: List[ChunkEntry]              # 已打 tier，按重要度排序
    loops_used: int
    elapsed_s: float
    retrieval_trace: List[dict] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────
# LlmAgent 运行小工具
# ─────────────────────────────────────────────────────────────────────
async def _run_llm_agent(
    agent: LlmAgent,
    prompt_text: str,
    app_name: str = "retrieve_v1",
) -> Dict[str, Any]:
    """用 InMemoryRunner 单发 LlmAgent，返回 output_schema 校验后的 dict。

    流程：
    1. 每次调用新建一个独立 session（InMemorySessionService 单进程内）
    2. user content = prompt_text
    3. 遍历 events，取 author==agent.name 且含 text part 的最终 model 回复
    4. json.loads 结果（LlmAgent 配了 output_schema，返回体是 JSON 文本）
    """
    runner = InMemoryRunner(agent=agent, app_name=app_name)
    user_id = f"u_{uuid.uuid4().hex[:8]}"
    session = await runner.session_service.create_session(
        app_name=app_name, user_id=user_id,
    )

    content = genai_types.Content(
        role="user", parts=[genai_types.Part(text=prompt_text)],
    )

    final_text: Optional[str] = None
    async for event in runner.run_async(
        user_id=user_id, session_id=session.id, new_message=content,
    ):
        if event.author != agent.name:
            continue
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if part.text:
                final_text = part.text

    if not final_text:
        raise RuntimeError(f"LlmAgent {agent.name} returned no text output")

    try:
        return json.loads(final_text)
    except json.JSONDecodeError as e:
        # max_output_tokens 触顶时 LLM 输出常截断在数组中段，触发
        # "Unterminated string" / "Expecting value"。先尝试修复——尽量恢复已完整的
        # top_k_chunk_ids 部分（其它字段缺失会被下游 _assemble_top_chunks 当 None 处理）。
        repaired = _repair_truncated_json(final_text)
        if repaired is not None:
            logger.warning(
                "LlmAgent %s JSON truncated, recovered %d top_k_chunk_ids (loss=%s)",
                agent.name, len(repaired.get("top_k_chunk_ids") or []), str(e)[:80],
            )
            return repaired
        logger.error("LlmAgent %s JSON 解析失败: %s\nraw=%s", agent.name, e, final_text[:500])
        raise


def _repair_truncated_json(raw: str) -> Optional[Dict[str, Any]]:
    """LLM 输出在 max_output_tokens 触顶时常被截断；本函数尽力恢复一个最小可用 dict。

    针对的截断形态（实测最常见）：
      {"top_k_chunk_ids": ["id_a", "id_b", "id_c", "id_d_unfinis...   ← 截断

    策略：
    1. 找到 ``"top_k_chunk_ids"`` 数组开头位置；
    2. 用正则提取该位置之后所有 "完整闭合" 的字符串字面量（含转义处理）；
    3. 闭合数组并以 ``{"top_k_chunk_ids": [...], "tiers": []}`` 返回，让下游正常走。

    若 raw 不含 top_k_chunk_ids 或一个完整 id 都没截到 → 返回 None，让上游报原错。
    """
    import re
    key_match = re.search(r'"top_k_chunk_ids"\s*:\s*\[', raw)
    if key_match is None:
        return None
    arr_start = key_match.end()
    # 提取该位置之后所有完整的双引号字符串（含转义）。最后一段若开了引号没闭合，正则
    # 自动跳过——这正是我们想要的（丢弃截断的尾段）。
    str_pattern = re.compile(r'"((?:[^"\\]|\\.)*)"')
    recovered_ids: List[str] = []
    pos = arr_start
    # 用 finditer 从 pos 开始扫，遇到非完整字符串自动停止
    while True:
        m = str_pattern.search(raw, pos)
        if m is None:
            break
        # 边界保护：若两个完整字符串之间出现 ``]``（数组结束），停止扫描
        between = raw[pos:m.start()]
        if "]" in between:
            break
        recovered_ids.append(m.group(1))
        pos = m.end()
    if not recovered_ids:
        return None
    return {"top_k_chunk_ids": recovered_ids, "tiers": [], "decision": "stop"}


# ─────────────────────────────────────────────────────────────────────
# Prompt 拼装（与 sub_agent 的 instruction 模板占位符保持一致）
# ─────────────────────────────────────────────────────────────────────
def _focuses_block(focuses: List[Focus]) -> str:
    return "\n".join(f"- {f.focus_id}: {f.text}" for f in focuses)


def _chunks_block(
    chunks: List[ChunkEntry],
    max_chars: int = 300,
    focuses: Optional[List["Focus"]] = None,
    refs: Optional[ChunkRefRegistry] = None,
) -> str:
    """渲染候选 chunks 列表供 LLM 评估。

    表格类 chunk（content 含 markdown 表格）使用更大的摘要窗口（2000 字），
    让 LLM 能看到实际数据行，而不只是表头。普通 chunk 使用 300 字。
    历史：500 → 2000（2025-Q4）—— Sheet1 类多行小表 1500+ 字时,500 字会切掉
    "原神/星穹铁道..." 等非首行数据 → eval LLM 误判为 noise。2000 字基本覆盖
    单 sheet section 全部行。token 成本约 +1.5K/chunk × 7 = ~10K，可接受。
    """
    if not chunks:
        return "(empty)"

    def _render_one(c: ChunkEntry) -> str:
        # Keep the rendered prompt body aligned with ContextBudget.
        snippet = prompt_preview_text(c)
        meta = c.metadata or {}
        route = meta.get("route") or meta.get("route_hint") or ""
        source_label = meta.get("source_name") or ""
        heading = meta.get("heading_path") or ""
        modified = (
            meta.get("modified_time")
            or meta.get("modifiedTime")
            or meta.get("drive_modified")
            or ""
        )
        created = meta.get("created_time") or meta.get("createdTime") or ""
        sq_display = (c.sub_query or "")[:35]
        extra = f" | route={route}" if route else ""
        extra += f" | src={source_label[:30]}" if source_label else ""
        extra += f" | modified={str(modified)[:10]}" if modified else ""
        extra += f" | created={str(created)[:10]}" if created else ""
        extra += f" | heading={heading[:40]}" if heading else ""
        extra += f" | sq={sq_display}" if sq_display else ""
        return (
            f"- chunk_ref={refs.id_to_ref.get(c.id, c.id) if refs else c.id} | focuses={sorted(c.target_focuses)} "
            f"| score={c.score}{extra} | {snippet}"
        )

    # 扁平渲染（兼容老路径）
    if not focuses:
        return "\n".join(_render_one(c) for c in chunks)

    # 分组渲染：按 focus 顺序，每个 chunk 只归属第一个命中的 focus
    placed: set = set()
    lines: List[str] = []
    for f in focuses:
        members = [c for c in chunks if f.focus_id in c.target_focuses and c.id not in placed]
        if not members:
            continue
        lines.append(f"## {f.focus_id}: {f.text}")
        for c in members:
            lines.append(_render_one(c))
            placed.add(c.id)

    # 兜底组：没匹配任何 focus 的 chunk
    leftover = [c for c in chunks if c.id not in placed]
    if leftover:
        lines.append("## Unassigned (target_focuses 为空或未在 focuses 列表)")
        for c in leftover:
            lines.append(_render_one(c))

    return "\n".join(lines)


def _sub_query_budget(n_focuses: int, source_top_k: int) -> int:
    """**[v1 fallback / replan path only]** sub_queries 总数上限。

    v2.2 主路径由 global_planner 自行控制 sub_query 数量，此函数仅在
    ``_plan_via_plan_only_llm`` (v1 fallback + replan 兜底) 里通过 prompt
    告知老 plan_only_LLM 该源的预算上限。

    公式：``max(2, min(n_focuses*2, source_top_k*2))`` —— 随 focus 数线性增长，
    天花板由 source_top_k 决定。
    """
    return max(2, min(n_focuses * 2, source_top_k * 2))


def _build_plan_only_prompt(
    focuses: List[Focus],
    profile: SourceProfile,
    source_top_k: int,
    extra_rules_block: str = "",
    sq_budget_override: Optional[int] = None,
) -> str:
    """拼 plan_only 用户消息。``extra_rules_block`` 供子类注入源专业规则。"""
    sq_budget = (
        sq_budget_override
        if sq_budget_override is not None
        else _sub_query_budget(len(focuses), source_top_k)
    )
    parts = [
        "required_response_format: JSON object",
        f"focuses_block:\n{_focuses_block(focuses)}",
        f"source_profile_block:\n{profile.to_prompt_block()}",
        f"current_date: {date.today().isoformat()}",
        f"source_top_k: {source_top_k}",
        f"sub_query_total_cap: {sq_budget}  (sub_queries 总数不得超过该上限)",
    ]
    if extra_rules_block:
        parts.append(f"source_query_rules_block:\n{extra_rules_block}")
    return "\n\n".join(parts)


def _build_eval_only_prompt(
    focuses: List[Focus], chunks: List[ChunkEntry], source_top_k: int, refs: Optional[ChunkRefRegistry] = None,
) -> str:
    return (
        "required_response_format: JSON object\n\n"
        "Reference contract: every top_k_chunk_ids item and every tiers[].chunk_id must be "
        "the chunk_ref (C1, C2, ...) printed in chunks_block. Never output a canonical chunk ID.\n\n"
        f"focuses_block:\n{_focuses_block(focuses)}\n\n"
        f"current_date: {date.today().isoformat()}\n\n"
        f"chunks_block:\n{_chunks_block(chunks, focuses=focuses, refs=refs)}\n\n"
        f"source_top_k: {source_top_k}"
    )


def _build_partial_eval_prompt(
    focuses: List[Focus],
    chunks: List[ChunkEntry],
    batch_id: str,
    refs: Optional[ChunkRefRegistry] = None,
    max_critical_per_focus: int = 6,
    max_important_per_focus: int = 12,
) -> str:
    refs = refs or ChunkRefRegistry.build(chunks)
    return (
        f"required_response_format: JSON object\n"
        f"batch_id: {batch_id}\n"
        "Reference contract: use only chunk_ref values (C1, C2, ...) printed in chunks_block. "
        "Return focus_shortlists grouped by focus_id. For each focus, admission_chunk_ids is mandatory "
        "and must contain 1-18 refs that should proceed to plan_eval; it must equal the union of the "
        "critical_chunk_ids and important_chunk_ids. For each focus return at most "
        f"{max_critical_per_focus} critical refs and {max_important_per_focus} important refs. "
        "If uncertain, put the best candidate in important rather than returning an empty group. "
        "A ref may occur in more than one focus only when it directly supports both. "
        "Omission means not_shortlisted, not a semantic noise judgment. "
        "Never output a canonical chunk ID.\n"
        f"focuses_block:\n{_focuses_block(focuses)}\n\n"
        f"chunks_block:\n{_chunks_block(chunks, focuses=focuses, refs=refs)}\n\n"
        "A chunk is noise only when it is unrelated to every focus; "
        "do not mark a complementary cross-document evidence chunk as noise merely because it is incomplete alone.\n\n"
        + EVAL_RUBRIC_BLOCK
    )


def _slim_focus_status_for_prompt(
    focus_status: Optional[Dict[str, Dict[str, Any]]],
) -> Optional[Dict[str, Dict[str, Any]]]:
    """裁剪 FocusBoard.snapshot() 给 LLM 看的字段。

    只保留 plan_eval 的 **replan 决策** 真正需要的信号：
      - ``status``: satisfied / pending / noise_only —— LLM 判断要不要再 replan
      - ``contributors``: 哪些源已经贡献过 —— 让 LLM 知道是否已有外援覆盖

    主动剥离的字段：``critical`` / ``important`` / ``noise`` / ``targets``
    这些是 quota 计数（早停调度内部状态），不应作为 tier 评分的暗示信号。
    eval 必须基于 chunks_block 客观打分，与跨源 quota 进度无关。
    """
    if not focus_status:
        return focus_status
    return {
        fid: {
            "status": s.get("status"),
            "contributors": list(s.get("contributors") or []),
        }
        for fid, s in focus_status.items()
    }


def _build_plan_eval_prompt(
    focuses: List[Focus],
    chunks: List[ChunkEntry],
    loop_n: int,
    min_loops: int,
    max_loops: int,
    source_top_k: int,
    focus_status: Optional[Dict[str, Dict[str, Any]]] = None,
    partial_ledgers: Optional[List[Dict[str, Any]]] = None,
    refs: Optional[ChunkRefRegistry] = None,
) -> str:
    """构建 plan_eval 用户消息。

    focus_status 经 ``_slim_focus_status_for_prompt`` 裁剪后注入——只让 LLM
    看到"是否已被别源覆盖"与"哪些源贡献过"，**不**暴露 quota 计数。
    """
    parts = [
        "required_response_format: JSON object",
        "Reference contract: every top_k_chunk_ids item and every tiers[].chunk_id must be the "
        "chunk_ref (C1, C2, ...) printed in chunks_block. Never output a canonical chunk ID.",
        f"focuses_block:\n{_focuses_block(focuses)}",
        f"current_date: {date.today().isoformat()}",
        f"chunks_block:\n{_chunks_block(chunks, focuses=focuses, refs=refs)}",
        f"loop_info: loop_n={loop_n}, min_loops={min_loops}, max_loops={max_loops}",
        f"source_top_k: {source_top_k}",
    ]
    slim_status = _slim_focus_status_for_prompt(focus_status)
    if slim_status:
        parts.append(
            "focus_status (跨源覆盖状态；status=satisfied 的 focus 不要再 replan):\n"
            + json.dumps(slim_status, ensure_ascii=False)
        )
    if partial_ledgers:
        parts.append("partial_eval_evidence_ledgers (batch-local; combine before judging a gap):\n"
                     + json.dumps(partial_ledgers, ensure_ascii=False))
    return "\n\n".join(parts)


def _tiered_top_ids(
    tiers_map: Dict[str, Dict[str, Any]],
    chunk_pool: Dict[str, ChunkEntry],
    limit: int,
) -> List[str]:
    """Safely finalize a stop decision when its IDs were omitted.

    This only consumes the model's existing ``critical``/``important``
    judgments. It never promotes an unjudged or noise chunk, and stable pool
    order is the final tie-breaker, preserving native retrieval order.
    """
    tier_order = {"critical": 0, "important": 1}
    ranked: List[tuple[int, float, int, str]] = []
    for order, chunk_id in enumerate(chunk_pool):
        judgment = tiers_map.get(chunk_id) or {}
        tier = judgment.get("tier")
        if tier not in tier_order:
            continue
        try:
            relevance = float(judgment.get("relevance", 0.0) or 0.0)
        except (TypeError, ValueError):
            relevance = 0.0
        ranked.append((tier_order[tier], -relevance, order, chunk_id))
    ranked.sort()
    return [chunk_id for _, _, _, chunk_id in ranked[:limit]]


# ─────────────────────────────────────────────────────────────────────
# SourceAgent 基类
# ─────────────────────────────────────────────────────────────────────
class SourceAgent:
    """SourceAgent 抽象基类——管理单源 loop 生命周期。

    子类需实现 ``execute_sub_query``，或在构造时传入 ``execute_fn``。
    """

    source: str = ""                          # 子类覆盖："feishu" / "bailian" / ...

    # 默认每源专属线程池大小；子类可覆盖（GoogleSourceAgent=20, FeishuSourceAgent=16, ...）
    DEFAULT_EXECUTOR_WORKERS: int = 12

    def __init__(
        self,
        profile: SourceProfile,
        cfg: SourceAgentConfig,
        execute_fn: Optional[ExecuteFn] = None,
        executor_workers: Optional[int] = None,
    ):
        """构造 SourceAgent 基类。

        子类需要覆写 _build_xxx_agent 钩子来提供源特定的 LLM Agents。
        若 _build_plan_only_agent 返回 None，此时子类必须覆写
        ``_plan_sub_queries(focuses)`` 用确定性逻辑直接把 focus 映射成 sub_queries。

        executor_workers: 该源专属 ThreadPoolExecutor 的 worker 数。None 时用类默认。
        所有 ``self._to_thread`` / 通过 ``self._executor`` 提交的 to_thread 调用都会走
        本源池子，避免与其他源在 Python 默认全局池里争抢。
        """
        self.profile = profile
        self.cfg = cfg
        self.plan_only = self._build_plan_only_agent()
        self.eval_only = self._build_eval_only_agent()
        self.plan_eval = self._build_plan_eval_agent()
        self.partial_eval = self._build_partial_eval_agent()
        self._partial_ledgers: List[Dict[str, Any]] = []
        self._partial_pending_tasks: List[asyncio.Task[Dict[str, Any]]] = []
        self._partial_buffer_ids: List[str] = []
        self._partial_buffer_tokens: int = 0
        self._partial_submitted_ids: set[str] = set()
        self._execute_fn = execute_fn
        # 运行时注入：execute_sub_query 通过 target_focus 回查对应 Focus 对象
        self._current_focus_lookup: Dict[str, Focus] = {}
        self._sub_query_seq: int = 0
        # Multi-focus 动态 top_k：run() 入口算 base + (n_focuses-1)*2 → 注入 eval prompts 与
        # _assemble_top_chunks 截断；schema cap 已在 __init__ 时膨胀，永远 ≥ effective。
        # run() 中按本次 focuses 重算并赋值，子方法通过 self 读取（多 loop 共享）。
        self._effective_source_top_k: int = self.cfg.source_top_k
        # 源专属线程池——所有 self._to_thread 调用都走这里，避免全局默认池竞争
        workers = int(executor_workers if executor_workers is not None else self.DEFAULT_EXECUTOR_WORKERS)
        self._executor: concurrent.futures.ThreadPoolExecutor = concurrent.futures.ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix=f"src-{self.source or 'unknown'}",
        )
        self._inflight_futures: set[asyncio.Future[Any]] = set()

    async def _to_thread(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """``asyncio.to_thread`` 的源专属版本：路由到 ``self._executor``。

        签名与 ``asyncio.to_thread`` 一致（含 contextvars 传播）；多源场景下避免争抢
        Python 默认全局线程池。
        """
        loop = asyncio.get_event_loop()
        ctx = contextvars.copy_context()
        func_call = functools.partial(ctx.run, fn, *args, **kwargs)
        future = loop.run_in_executor(self._executor, func_call)
        self._inflight_futures.add(future)
        future.add_done_callback(self._inflight_futures.discard)
        return await asyncio.shield(future)

    async def drain_inflight(self, timeout_s: float = 10.0) -> int:
        """Bound cancellation drain for synchronous source I/O."""
        pending = [future for future in self._inflight_futures if not future.done()]
        if not pending:
            return 0
        _done, remaining = await asyncio.wait(pending, timeout=max(0.0, timeout_s))
        # Do not cancel executor futures: cancelling the asyncio facade does
        # not stop the underlying thread and would falsely mark it quiescent.
        return len(remaining)

    async def aclose(self) -> None:
        """退出时调用，释放线程池资源。"""
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

    def _build_plan_only_agent(self) -> Optional[LlmAgent]:
        """构造用于第一轮规划的 LLM Agent，子类覆写。"""
        return None

    def _build_eval_only_agent(self) -> LlmAgent:
        """构造用于结果收尾评估的 LLM Agent，子类必须覆写。"""
        raise NotImplementedError("SourceAgent must implement _build_eval_only_agent")

    def _build_plan_eval_agent(self) -> Optional[LlmAgent]:
        """构造用于多轮中间评估的 LLM Agent，子类按需覆写。"""
        return None

    def _build_partial_eval_agent(self) -> LlmAgent:
        shortlist = self.cfg.eval_shortlist
        return LlmAgent(
            name=f"{self.source}_partial_eval",
            model=build_llm_model(),
            instruction=("You are a retrieval evidence admission evaluator. "
                         "Return only focus-grouped critical/important shortlist refs and compact per-focus ledgers; "
                         "do not emit a judgment for every supplied chunk.\n\n"
                         + EVAL_RUBRIC_BLOCK),
            output_schema=build_partial_eval_model(
                max_critical_per_focus=shortlist.max_critical_per_focus,
                max_important_per_focus=shortlist.max_important_per_focus,
            ),
            generate_content_config=AGENT_CFG_EVAL_ONLY,
        )
    async def execute_sub_query(
        self, sub_query: str, target_focus: str,
    ) -> List[ChunkEntry]:
        """子类 override 或通过 execute_fn 注入。默认抛错。

        ⚠️ 这是**旧版**两参签名。v2 起建议子类改用 ``execute_sub_query_full`` 接收
        ``SubQuery`` 整个对象（可读 tags / indexes / time_filter）。基类会自动
        dispatch：子类若实现 ``execute_sub_query_full``，基类只走它。
        """
        if self._execute_fn is None:
            raise NotImplementedError(f"{type(self).__name__} must implement execute_sub_query")
        return await self._execute_fn(sub_query, target_focus)

    async def execute_sub_query_full(self, sq: "SubQuery") -> List[ChunkEntry]:
        """v2 执行入口：接收完整 SubQuery（可读 tags / indexes / time_filter）。

        默认实现 fallback 到老的 ``execute_sub_query(query, target_focus)``，
        子类想消费源专属参数时覆写此方法即可。
        """
        return await self.execute_sub_query(sq.query, sq.target_focus)

    # ── 子类覆写点：sub_query 总数预算（注入到 plan_only prompt） ─
    def _compute_sub_query_total_cap(self, n_focuses: int) -> int:
        """**[v1 fallback / replan path only]** 返回 plan_only prompt 中的
        sub_query_total_cap 值。

        v2.2 主路径（pre_planned_sub_queries）**不**调用此方法——sub_query 数量
        由 global_planner 直接决定。仅在 ``_plan_via_plan_only_llm``（v1 fallback
        与 per-focus replan 兜底）链路使用。
        """
        return _sub_query_budget(n_focuses, self.cfg.source_top_k)

    # ── 子类覆写点：注入源专业规则到 plan_only prompt ────────────
    def _source_query_rules_block(self) -> str:
        """**[v1 fallback / replan path only]** 返回该源的 query 编写规则文本，
        供 ``_plan_via_plan_only_llm`` 拼接进 plan_only prompt。

        v2.2 主路径（pre_planned_sub_queries）由 global_planner 装载 enabled
        源规则，**不**走此分支。仅 v1 fallback / replan 兜底时启用。
        """
        return ""

    def _post_process_sub_queries(
        self,
        focuses: List[Focus],
        sub_queries: List[SubQuery],
    ) -> List[SubQuery]:
        """规划后 callback。

        设计意图：
        - 保持 ``_plan`` 主链简洁：focus -> plan_only -> assign ids -> callback
        - 允许子类做最小限度的收束、校验、去重，而不用覆写整段 ``_plan`` 流程
        - 默认不做任何处理
        """
        _ = focuses
        return sub_queries

    def _assign_sub_query_ids(self, sub_queries: List[SubQuery]) -> List[SubQuery]:
        """为运行时 sub_queries 分配稳定的本地 id。"""
        for sq in sub_queries:
            if sq.sub_query_id:
                continue
            self._sub_query_seq += 1
            sq.sub_query_id = f"{self.source}:{sq.target_focus}:sq{self._sub_query_seq}"
        return sub_queries

    async def _plan_via_plan_only_llm(self, focuses: List[Focus]) -> List[SubQuery]:
        """**[v1 fallback / replan path only]** 调用老 plan_only_LLM 现场产 sub_queries。

        触发：
        1. v2 ``global_planner`` 整盘失败且 ``fallback_to_v1=True`` —— 整盘走这里；
        2. ``planner_runner`` 把某 focus 在该源全部 sub_query reject → 进 replan，
           只对该 focus 走这里做兜底（局部 replan）。

        v2.2 主路径（pre_planned_sub_queries 非空）**不**调用此方法。
        """
        if self.plan_only is None:
            return []
        prompt = _build_plan_only_prompt(
            focuses, self.profile, self.cfg.source_top_k,
            extra_rules_block=self._source_query_rules_block(),
            sq_budget_override=self._compute_sub_query_total_cap(len(focuses)),
        )
        out = await _run_llm_agent(self.plan_only, prompt)
        return [
            SubQuery(
                query=q["query"],
                target_focus=q["target_focus"],
                tags=list(q.get("tags") or []),
                indexes=list(q.get("indexes") or []),
                time_filter=q.get("time_filter"),
                query_type=q.get("query_type"),
                google_enable_fulltext=q.get("google_enable_fulltext"),
                google_order_by=q.get("google_order_by"),
                author_filter=q.get("author_filter"),
            )
            for q in out.get("sub_queries", [])
        ]

    async def _plan(
        self,
        focuses: List[Focus],
        pre_planned_sub_queries: Optional[List[SubQuery]] = None,
        replan_focuses: Optional[List[Focus]] = None,
    ) -> List[SubQuery]:
        """统一规划入口。v2.2 双路径：

        - 优先使用 ``pre_planned_sub_queries``（来自 global_planner + planner_runner，
          已经过该源 Validator 校验）；
        - 同时若 ``replan_focuses`` 非空，则用老 plan_only_LLM 对这部分 focus 局部修复，
          结果合并进总 sub_queries；
        - 两者都为空时 → 走纯老路径（v1 fallback / 老调用方未传 kwargs）。

        子类的 ``_post_process_sub_queries`` 仍会作为兜底跑一遍，保留与 v1 一致的行为。
        """
        sub_queries: List[SubQuery] = []
        if pre_planned_sub_queries:
            sub_queries.extend(pre_planned_sub_queries)

        # 局部 replan：仅给被 validator 整 focus 整源 reject 掉的 focus 走老 LLM
        if replan_focuses:
            try:
                recovered = await self._plan_via_plan_only_llm(replan_focuses)
            except Exception as e:
                logger.warning(
                    "[%s] _plan replan_via_plan_only_llm failed (%d focuses): %s",
                    self.source, len(replan_focuses), e,
                )
                recovered = []
            sub_queries.extend(recovered)

        # 老路径兜底：双 kwarg 都为空时，与 v1 行为完全一致
        if not sub_queries and pre_planned_sub_queries is None and replan_focuses is None:
            sub_queries = await self._plan_via_plan_only_llm(focuses)

        if not sub_queries:
            return []

        sub_queries = self._assign_sub_query_ids(sub_queries)
        sub_queries = self._post_process_sub_queries(focuses, sub_queries)
        self._trace_events.extend({
            "event": "query", "source": self.source, "focus": sq.target_focus,
            "query_id": sq.sub_query_id, "query_type": sq.query_type,
            "api_query": sq.query,
        } for sq in sub_queries)
        return sub_queries

    # ── 公开入口 ──────────────────────────────────────────────
    async def run(
        self,
        focuses: List[Focus],
        pre_planned_sub_queries: Optional[List[SubQuery]] = None,
        replan_focuses: Optional[List[Focus]] = None,
    ) -> SourceAgentResult:
        """跑完整个 SourceAgent loop，返回 top_chunks。

        v2.2 新增 kwargs：
        - ``pre_planned_sub_queries``: planner_runner 已校验的本源 SubQuery 列表
        - ``replan_focuses``: 该源该 focus 全部 sub_query 被 reject 的 focus，
          走老 plan_only_LLM 局部修复
        两个 kwarg 均不传时，行为与 v1 完全一致（_plan 内部走老 plan_only_LLM）。
        """
        still_running = await self.drain_inflight(timeout_s=0.0)
        if still_running:
            raise RuntimeError(f"source_not_quiescent:{still_running}")
        start = asyncio.get_event_loop().time()
        chunk_pool: Dict[str, ChunkEntry] = {}
        # 注入 focus 回查表，供 execute_sub_query 读 focus 的完整字段（time_hint/entity_hint 等）
        self._current_focus_lookup = {f.focus_id: f for f in focuses}
        self._sub_query_seq = 0
        self._partial_ledgers = []
        self._partial_pending_tasks = []
        self._partial_buffer_ids = []
        self._partial_buffer_tokens = 0
        self._partial_submitted_ids = set()
        self._trace_events = []
        # Multi-focus 动态 effective top_k：本源被路由到 N 个 focus 时，扩容到 base+(N-1)*2，
        # 保证 eval_only / plan_eval 有足够预算给每个 focus 留至少 1 个名额。
        self._effective_source_top_k = effective_source_top_k(self.cfg.source_top_k, len(focuses))
        if self._effective_source_top_k != self.cfg.source_top_k:
            logger.info(
                "[%s] effective_source_top_k: base=%d n_focuses=%d → effective=%d",
                self.source, self.cfg.source_top_k, len(focuses), self._effective_source_top_k,
            )

        # 单 loop（min=max=1）走简化路径；其余走多 loop。v2.2 入参 (pre_planned /
        # replan_focuses) 作为显式 kwargs 透传，避免借 self 中转隐式 state。
        if self.cfg.min_loops == 1 and self.cfg.max_loops == 1:
            result = await self._run_single_loop(
                focuses, chunk_pool,
                pre_planned_sub_queries=pre_planned_sub_queries,
                replan_focuses=replan_focuses,
            )
        else:
            if self.plan_eval is None:
                raise RuntimeError(
                    f"[{self.source}] max_loops={self.cfg.max_loops} > 1 but plan_eval agent is None"
                )
            result = await self._run_multi_loop(
                focuses, chunk_pool,
                pre_planned_sub_queries=pre_planned_sub_queries,
                replan_focuses=replan_focuses,
            )

        elapsed = asyncio.get_event_loop().time() - start
        logger.info(
            "[%s] done: loops=%d/%d (min=%d), top_chunks=%d, elapsed=%.2fs",
            self.source, result.loops_used, self.cfg.max_loops, self.cfg.min_loops,
            len(result.top_chunks), elapsed,
        )
        result.elapsed_s = elapsed
        # A compact, body-free trace is handed to asynchronous profile
        # governance by the orchestrator.  Chunks contribute IDs, headings and
        # tiers only; evidence text remains in the answer path.
        result.retrieval_trace = list(self._trace_events) + [
            {
                "event": "source_retained",
                "source": self.source,
                "chunk_id": chunk.id,
                "tier": chunk.tier,
                "focuses": sorted(chunk.target_focuses),
                "query_ids": list((chunk.metadata or {}).get("sub_query_ids") or
                                  (chunk.metadata or {}).get("selected_sub_query_ids") or []),
                "route": str((chunk.metadata or {}).get("route") or
                             (chunk.metadata or {}).get("route_hint") or ""),
                "heading": str(chunk.metadata.get("heading_path", "")),
                "range": str(chunk.metadata.get("range", "")),
                "document": str(chunk.metadata.get("source_name", chunk.metadata.get("title", ""))),
            }
            for chunk in result.top_chunks
        ]
        observer = current_role_funnel()
        if observer is not None:
            observer.observe_chunks("source_retained", result.top_chunks)
        return result

    # ── 单 loop 路径：plan(pre_planned + replan / 老 LLM) → execute → eval_only ─
    async def _run_single_loop(
        self,
        focuses: List[Focus],
        chunk_pool: Dict[str, ChunkEntry],
        pre_planned_sub_queries: Optional[List[SubQuery]] = None,
        replan_focuses: Optional[List[Focus]] = None,
    ) -> SourceAgentResult:
        # A. 规划 sub_queries：
        #    - v2.2 链路：使用 pre_planned_sub_queries（+ replan 局部修复）
        #    - v1 兼容链路：两 kwarg 均 None 时回老 plan_only_LLM
        sub_queries = await self._plan(
            focuses,
            pre_planned_sub_queries=pre_planned_sub_queries,
            replan_focuses=replan_focuses,
        )
        if not sub_queries:
            logger.warning("[%s] produced 0 sub_queries", self.source)
            return SourceAgentResult(self.source, [], loops_used=1, elapsed_s=0.0)

        logger.info("[%s] plan produced %d sub_queries", self.source, len(sub_queries))

        # B. execute（并发跑所有 sub_query）
        await self._execute_batch(sub_queries, chunk_pool, loop_n=1)
        await self._rerank_new_chunks(focuses, chunk_pool, loop_n=1)
        await self._partial_eval_if_needed(focuses, chunk_pool, loop_n=1)

        if not chunk_pool:
            logger.warning("[%s] execute produced 0 chunks, skip eval_only", self.source)
            return SourceAgentResult(self.source, [], loops_used=1, elapsed_s=0.0)

        # C. eval_only 收尾（单 loop 场景下按 spec §2.4 必跑一次）
        # FocusBoard 的职责是取消尚未开始 / 正在飞行的 I/O sub-query；
        # 一旦 API 读取完成、chunks 已进 chunk_pool，无论 focus 是否被外部 satisfied，
        # eval_only 都必须执行，否则已完成读取的结果会被丢弃。
        top_chunks = await self._eval_only_finalize(focuses, chunk_pool)
        return SourceAgentResult(self.source, top_chunks, loops_used=1, elapsed_s=0.0)

    # ── 多 loop 路径：plan_only → execute → (plan_eval → execute)* → eval_only ──
    async def _run_multi_loop(
        self,
        focuses: List[Focus],
        chunk_pool: Dict[str, ChunkEntry],
        pre_planned_sub_queries: Optional[List[SubQuery]] = None,
        replan_focuses: Optional[List[Focus]] = None,
    ) -> SourceAgentResult:
        """多 loop 检索路径（spec §2.4）。

        - loop_n=1: plan_only 产出首轮 sub_queries → execute
        - loop_n=2..: plan_eval 判 continue/stop
            - continue: 用 replan_queries 再跑一轮 execute
            - stop: 用 plan_eval 返回的 top_k_chunk_ids + tiers 组装结果；
              若返回为空则兜底跑 eval_only
        - loop_n == max_loops: 强制 stop；若 plan_eval 未给 top_k → 兜底跑 eval_only
        """
        min_loops, max_loops = self.cfg.min_loops, self.cfg.max_loops
        assert self.plan_eval is not None  # run() 已校验

        # loop 1: plan → execute（v2.2：使用 pre_planned + 局部 replan；老路径回退）
        sub_queries = await self._plan(
            focuses,
            pre_planned_sub_queries=pre_planned_sub_queries,
            replan_focuses=replan_focuses,
        )
        if not sub_queries:
            logger.warning("[%s] loop=1 produced 0 sub_queries", self.source)
            return SourceAgentResult(self.source, [], loops_used=1, elapsed_s=0.0)
        logger.info("[%s] loop=1 plan produced %d sub_queries", self.source, len(sub_queries))
        await self._execute_batch(sub_queries, chunk_pool)
        await self._rerank_new_chunks(focuses, chunk_pool, loop_n=1)
        await self._partial_eval_if_needed(focuses, chunk_pool, loop_n=1)

        loop_n = 1

        # loop 2..max_loops: plan_eval（中间轮决策）
        while loop_n < max_loops:
            loop_n += 1

            if not chunk_pool:
                logger.warning("[%s] loop=%d chunk_pool empty → stop early", self.source, loop_n)
                break

            # P4: 读 FocusBoard 快照（如有）—— focus-aware plan_eval
            board = getattr(self, "_focus_board", None)
            focus_status = board.snapshot() if board is not None else None

            # P4: 若所有 targeted focus 都已被别的源 SATISFIED，提前 stop（跳过 plan_eval LLM）
            if board is not None and focuses:
                targeted_fids = {f.focus_id for f in focuses}
                if targeted_fids and all(board.is_satisfied(fid) for fid in targeted_fids):
                    logger.info(
                        "[%s] loop=%d all %d targeted focuses externally satisfied → stop early",
                        self.source, loop_n, len(targeted_fids),
                    )
                    break

            registry = ChunkRefRegistry.build(list(chunk_pool.values()))
            pe_prompt = _build_plan_eval_prompt(
                focuses, list(chunk_pool.values()),
                loop_n=loop_n, min_loops=min_loops, max_loops=max_loops,
                source_top_k=self._effective_source_top_k,
                focus_status=focus_status,
                partial_ledgers=self._partial_ledgers,
                refs=registry,
            )
            try:
                pe_out = await _run_llm_agent(self.plan_eval, pe_prompt)
            except Exception as exc:
                # A plan-eval outage must not discard already-read evidence.
                # Final eval_only retains the active pool and leaves a compact
                # failure marker for the benchmark/profile governance trace.
                logger.warning("[%s] loop=%d plan_eval failed; fall back to eval_only: %s",
                               self.source, loop_n, exc)
                self._trace_events.append({
                    "event": "plan_eval_error", "source": self.source,
                    "loop": loop_n, "error_type": type(exc).__name__,
                })
                break
            pe_out, malformed_refs = registry.resolve_eval_output(pe_out)
            if malformed_refs:
                self._trace_events.append({"event": "eval_contract_error", "source": self.source,
                                           "loop": loop_n, "malformed_chunk_refs": malformed_refs})
                logger.warning("[%s] loop=%d rejected %d malformed LLM chunk refs", self.source, loop_n, malformed_refs)
            pe_out = validate_plan_eval_output(pe_out)

            judgments = tiers_list_to_map(pe_out.get("tiers"))
            # Explicit noise without focus coverage is trace-only.  It can be
            # pruned before a *continue* replan, but must remain addressable
            # until a same-turn stop response has consumed its top_k contract.
            # Otherwise valid IDs become false "hallucinations" after we pop
            # them from chunk_pool.
            prunable_noise_ids = [
                cid for cid, judgment in judgments.items()
                if judgment.get("tier") == "noise"
                and not judgment.get("answered_focuses")
                and cid in chunk_pool
            ]

            raw_decision = pe_out.get("decision", "stop")
            decision = enforce_loop_bounds(raw_decision, loop_n, min_loops, max_loops)
            removed: List[str] = []
            if decision == "continue":
                for cid in prunable_noise_ids:
                    chunk_pool.pop(cid, None)
                removed = prunable_noise_ids
                if removed:
                    logger.info("[%s] loop=%d removed %d explicit noise chunks before replan",
                                self.source, loop_n, len(removed))
            self._trace_events.append({
                "event": "plan_eval", "source": self.source, "loop": loop_n,
                "decision": decision, "raw_decision": raw_decision,
                "replan_query_count": len(pe_out.get("replan_queries") or []),
                "active_chunk_count": len(chunk_pool), "removed_noise_count": len(removed),
                "focus_decisions": list(pe_out.get("focus_decisions") or []),
                "contract_normalizations": list(pe_out.get("contract_normalizations") or []),
            })
            logger.info(
                "[%s] loop=%d plan_eval raw=%s enforced=%s",
                self.source, loop_n, raw_decision, decision,
            )

            if decision == "continue":
                replan = [
                    SubQuery(
                        query=q["query"],
                        target_focus=q["target_focus"],
                        tags=list(q.get("tags") or []),
                        indexes=list(q.get("indexes") or []),
                        time_filter=q.get("time_filter"),
                        query_type=q.get("query_type"),
                        google_enable_fulltext=q.get("google_enable_fulltext"),
                        google_order_by=q.get("google_order_by"),
                        author_filter=q.get("author_filter"),
                    )
                    for q in (pe_out.get("replan_queries") or [])
                ]
                # P4: 过滤掉已 satisfied focus 的 replan（即便 LLM 漏读 focus_status）
                if board is not None:
                    before_n = len(replan)
                    replan = [sq for sq in replan if not board.is_satisfied(sq.target_focus)]
                    if len(replan) < before_n:
                        logger.info(
                            "[%s] loop=%d dropped %d replan_queries targeting satisfied focuses",
                            self.source, loop_n, before_n - len(replan),
                        )
                replan = self._assign_sub_query_ids(replan)
                if not replan:
                    logger.warning("[%s] loop=%d continue with empty replan → stop", self.source, loop_n)
                    break
                logger.info("[%s] loop=%d replan %d sub_queries", self.source, loop_n, len(replan))
                self._trace_events.extend({
                    "event": "query", "source": self.source, "loop": loop_n,
                    "focus": sq.target_focus, "query_id": sq.sub_query_id,
                    "query_type": sq.query_type, "api_query": sq.query,
                    "replan": True,
                } for sq in replan)
                await self._execute_batch(replan, chunk_pool, loop_n=loop_n)
                await self._rerank_new_chunks(focuses, chunk_pool, loop_n=loop_n)
                await self._partial_eval_if_needed(focuses, chunk_pool, loop_n=loop_n)
                continue

            # decision == "stop" → 尝试直接用 plan_eval 的 top_k
            top_ids: List[str] = pe_out.get("top_k_chunk_ids") or []
            tiers_map: Dict[str, Dict] = tiers_list_to_map(pe_out.get("tiers"))
            # Preserve every completed plan-eval judgment in the trace even if
            # the provider omitted final IDs. Profile governance needs this to
            # distinguish an evaluated evidence gap from a timeout.
            self._trace_events.extend({
                "event": "judgment", "source": self.source, "loop": loop_n,
                "chunk_id": chunk_id, "tier": judgment.get("tier"),
                "answered_focuses": list(judgment.get("answered_focuses") or []),
            } for chunk_id, judgment in tiers_map.items())
            observer = current_role_funnel()
            if observer is not None:
                observer.observe_chunks(
                    "plan_eval", [chunk_pool[cid] for cid in tiers_map if cid in chunk_pool],
                    tier_by_id={cid: str(item.get("tier") or "") for cid, item in tiers_map.items()},
                )
            # ``top_k`` can contain noise as an evaluation footprint.  Never
            # let it reach user-facing assembly, and record the exclusion
            # instead of deleting it first and later calling it hallucinated.
            selected_noise_ids = [
                item.get("chunk_id") if isinstance(item, dict) else item
                for item in top_ids
                if (item.get("chunk_id") if isinstance(item, dict) else item) in prunable_noise_ids
            ]
            if selected_noise_ids:
                self._trace_events.append({
                    "event": "selected_noise_excluded", "source": self.source,
                    "loop": loop_n, "chunk_ids": selected_noise_ids,
                })
                logger.info("[%s] loop=%d excluded %d selected noise chunks from final output",
                            self.source, loop_n, len(selected_noise_ids))
                top_ids = [
                    item for item in top_ids
                    if (item.get("chunk_id") if isinstance(item, dict) else item) not in prunable_noise_ids
                ]
            if not top_ids:
                top_ids = _tiered_top_ids(tiers_map, chunk_pool, self._effective_source_top_k)
                if top_ids:
                    logger.info(
                        "[%s] loop=%d plan_eval omitted top_k; finalized %d existing judgments",
                        self.source, loop_n, len(top_ids),
                    )
            if top_ids:
                normalized_top_ids = [item.get("chunk_id") if isinstance(item, dict) else item for item in top_ids]
                missing_judgments = [
                    chunk_id for chunk_id in normalized_top_ids
                    if isinstance(chunk_id, str) and chunk_id in chunk_pool and chunk_id not in tiers_map
                ]
                # A stop without a tier for every selected chunk violates the
                # preview/full contract.  Do not leak unjudged chunks into the
                # answer path; eval_only is the safe bounded fallback.
                if missing_judgments:
                    logger.warning(
                        "[%s] loop=%d plan_eval stop omitted %d selected chunk judgments; fallback eval_only",
                        self.source, loop_n, len(missing_judgments),
                    )
                    self._trace_events.append({
                        "event": "plan_eval_contract_error", "source": self.source,
                        "loop": loop_n, "missing_selected_judgments": len(missing_judgments),
                    })
                    break
                top_chunks = self._assemble_top_chunks(top_ids, tiers_map, chunk_pool)
                self._log_per_focus_tier_distribution(focuses, top_chunks)
                return SourceAgentResult(self.source, top_chunks, loops_used=loop_n, elapsed_s=0.0)
            # plan_eval stop 但没给 top_k → 走 eval_only 兜底
            logger.info("[%s] loop=%d plan_eval stop without top_k → fallback eval_only", self.source, loop_n)
            break

        # 收尾：eval_only
        if not chunk_pool:
            return SourceAgentResult(self.source, [], loops_used=loop_n, elapsed_s=0.0)
        top_chunks = await self._eval_only_finalize(focuses, chunk_pool)
        return SourceAgentResult(self.source, top_chunks, loops_used=loop_n, elapsed_s=0.0)

    # ── 公用小工具：eval_only 收尾 + top_k 组装 ────────────────
    async def _eval_only_finalize(
        self, focuses: List[Focus], chunk_pool: Dict[str, ChunkEntry],
    ) -> List[ChunkEntry]:
        started = time.perf_counter()
        registry = ChunkRefRegistry.build(list(chunk_pool.values()))
        eval_prompt = _build_eval_only_prompt(
            focuses, list(chunk_pool.values()), self._effective_source_top_k, refs=registry,
        )
        eval_out = await _run_llm_agent(self.eval_only, eval_prompt)
        eval_out, malformed_refs = registry.resolve_eval_output(eval_out)
        if malformed_refs:
            self._trace_events.append({"event": "eval_contract_error", "source": self.source,
                                       "malformed_chunk_refs": malformed_refs})
            logger.warning("[%s] eval_only rejected %d malformed LLM chunk refs", self.source, malformed_refs)
        top_ids: List[str] = eval_out.get("top_k_chunk_ids", [])
        tiers_map: Dict[str, Dict] = tiers_list_to_map(eval_out.get("tiers"))
        self._trace_events.extend({
            "event": "judgment", "source": self.source, "chunk_id": chunk_id,
            "tier": judgment.get("tier"),
            "answered_focuses": list(judgment.get("answered_focuses") or []),
        } for chunk_id, judgment in tiers_map.items())
        observer = current_role_funnel()
        if observer is not None:
            observer.observe_chunks(
                "plan_eval", [chunk_pool[cid] for cid in tiers_map if cid in chunk_pool],
                tier_by_id={cid: str(item.get("tier") or "") for cid, item in tiers_map.items()},
            )
        chunks = self._assemble_top_chunks(top_ids, tiers_map, chunk_pool)
        # Eval-only may deliberately put a weak focus's best noise candidate
        # in top_k as an evaluation footprint.  The footprint already exists
        # in ``judgment`` trace events; it must not become user-facing context
        # or reach cross-source global eval.
        selected_noise = [chunk.id for chunk in chunks if chunk.tier == "noise"]
        if selected_noise:
            self._trace_events.append({
                "event": "selected_noise_excluded", "source": self.source,
                "phase": "eval_only", "chunk_ids": selected_noise,
            })
            logger.info("[%s] eval_only excluded %d selected noise chunks from final output",
                        self.source, len(selected_noise))
            chunks = [chunk for chunk in chunks if chunk.tier != "noise"]
        # 不在 source 层做"per-focus 保底升级"——EVAL_RUBRIC_BLOCK 的多 focus
        # 全面评估守卫已让 LLM 客观地覆盖每个 focus；最终 tier 维持 LLM 判断，
        # 早停 / quota 仅在 FocusBoard 调度层使用，不修改输出。
        chunks = self._post_process_top_chunks(focuses, chunks)
        # 观察日志：每 focus 实际拿到的 tier 分布，便于事后分析（不修改 chunks）
        self._log_per_focus_tier_distribution(focuses, chunks)
        logger.info(
            "TIMING source.eval_only source=%s input_chunks=%d selected=%d ms=%.1f",
            self.source,
            len(chunk_pool),
            len(chunks),
            (time.perf_counter() - started) * 1000.0,
        )
        return chunks

    async def _rerank_new_chunks(
        self, focuses: List[Focus], chunk_pool: Dict[str, ChunkEntry], *, loop_n: int,
    ) -> None:
        """Compress newly-read evidence before partial eval, eval_only, or plan_eval.

        Scores are only compared within a focus and modality.  Retained chunks
        are marked, so a later replan loop never repeatedly thins prior evidence.
        """
        if not self.cfg.rerank_enabled or not chunk_pool:
            return
        pending = [c for c in chunk_pool.values() if not (c.metadata or {}).get("rerank_processed")]
        focus_text = {f.focus_id: f.text for f in focuses}
        groups: Dict[tuple[str, str], List[ChunkEntry]] = {}
        for chunk in pending:
            for focus_id in set(chunk.target_focuses) & set(focus_text):
                groups.setdefault((focus_id, self._rerank_group(chunk)), []).append(chunk)
        if not groups:
            return
        try:
            env_settings = RerankSettings.from_env()
            settings = RerankSettings(
                api_key=env_settings.api_key, workspace_id=env_settings.workspace_id,
                model=env_settings.model, timeout_s=self.cfg.rerank_timeout_s,
                instruct=env_settings.instruct, endpoint=env_settings.endpoint,
            )
            keep_ids: set[str] = set()
            group_events: List[Dict[str, Any]] = []
            for (focus_id, modality), chunks in groups.items():
                ranked = await asyncio.to_thread(
                    rerank_items, focus_text[focus_id],
                    [(c.id, self._rerank_text(c)) for c in chunks], settings=settings,
                )
                selected = select_ranked_ids(
                    ranked, keep_ratio=self.cfg.rerank_keep_ratio,
                    score_threshold=self.cfg.rerank_score_threshold,
                )
                keep_ids.update(selected)
                scores = {item.item_id: item.relevance_score for item in ranked}
                for chunk in chunks:
                    per_focus_scores = dict(chunk.metadata.get("rerank_scores") or {})
                    per_focus_scores[focus_id] = scores.get(chunk.id)
                    chunk.metadata["rerank_scores"] = per_focus_scores
                    chunk.metadata["rerank_group"] = modality
                group_events.append({"focus": focus_id, "group": modality,
                                     "input": len(chunks), "retained": len(selected)})
        except RerankError as exc:
            self._trace_events.append({"event": "rerank_error", "source": self.source,
                                       "loop": loop_n, "error_type": type(exc).__name__})
            logger.warning("[%s] rerank failed; retaining original pool: %s", self.source, exc)
            return
        removed = [chunk.id for chunk in pending if chunk.id not in keep_ids]
        for chunk in pending:
            chunk.metadata["rerank_processed"] = True
        for chunk_id in removed:
            chunk_pool.pop(chunk_id, None)
        self._trace_events.extend({
            "event": "rerank_decision", "source": self.source, "loop": loop_n,
            "chunk_id": chunk.id, "group": chunk.metadata.get("rerank_group", "document"),
            "scores": dict(chunk.metadata.get("rerank_scores") or {}), "retained": chunk.id in keep_ids,
            "focuses": sorted(chunk.target_focuses),
        } for chunk in pending)
        observer = current_role_funnel()
        if observer is not None:
            observer.observe_chunks(
                "rerank_retained", [chunk for chunk in pending if chunk.id in keep_ids],
            )
        self._trace_events.append({"event": "rerank", "source": self.source, "loop": loop_n,
                                   "groups": group_events, "removed_count": len(removed),
                                   "retained_count": len(pending) - len(removed)})
        logger.info("[%s] loop=%d post-io rerank groups=%d input=%d removed=%d", self.source,
                    loop_n, len(groups), len(pending), len(removed))

    @staticmethod
    def _rerank_group(chunk: ChunkEntry) -> str:
        meta = chunk.metadata or {}
        explicit = str(meta.get("rerank_group") or meta.get("content_type") or meta.get("doc_type") or "").lower()
        if explicit in {"table", "sheet", "bitable", "spreadsheet", "excel", "csv"} or "table" in explicit:
            return "table"
        if str(meta.get("source_tool") or "").lower() in {"sheet", "excel", "csv"}:
            return "table"
        if any(x in chunk.id.lower() for x in (":sheet:", ":table:")):
            return "table"
        return "document"

    def _rerank_text(self, chunk: ChunkEntry) -> str:
        return prompt_preview_text(chunk)[:max(256, int(self.cfg.rerank_max_document_chars))]

    async def _partial_eval_if_needed(
        self, focuses: List[Focus], chunk_pool: Dict[str, ChunkEntry], *, loop_n: int,
    ) -> None:
        """Evaluate oversized pools in stable batches before global eval/plan_eval.

        This method never chooses final top-K or replan.  It removes only
        explicitly judged, focus-free noise and carries compact ledgers forward.
        Source-specific I/O can later call the same method while reads are still
        in flight; the current base hook guarantees correct semantics first.
        """
        await self._drain_partial_evals(chunk_pool)
        # Chunks submitted during I/O are already represented by their pending
        # judgments.  Only unsent chunks need a synchronous safeguard here.
        unsent = [c for cid, c in chunk_pool.items() if cid not in self._partial_submitted_ids]
        shortlist_cfg = self.cfg.eval_shortlist
        if not shortlist_cfg.enabled:
            return
        # Admission is a pool-level decision, not a side effect of provider
        # batch size.  A 59-chunk pool must not silently skip shortlist merely
        # because it fits into one 80-chunk request.
        if len(unsent) < shortlist_cfg.trigger_chunk_count and not self._partial_submitted_ids:
            return
        batches = split_chunks(
            unsent,
            ContextBudget(max_chunks_per_batch=shortlist_cfg.batch_chunk_limit),
        )
        logger.info("[%s] loop=%d partial-eval batches=%d chunks=%d", self.source, loop_n,
                    len(batches), len(unsent))

        async def _one(label: str, batch: List[ChunkEntry], depth: int = 0) -> Dict[str, Any]:
            registry = ChunkRefRegistry.build(batch)
            prompt = _build_partial_eval_prompt(
                focuses, batch, f"{self.source}:loop{loop_n}:batch{label}", refs=registry,
                max_critical_per_focus=shortlist_cfg.max_critical_per_focus,
                max_important_per_focus=shortlist_cfg.max_important_per_focus,
            )
            output = await _run_llm_agent(self.partial_eval, prompt)
            resolved, malformed = registry.resolve_eval_output(output)
            focus_shortlists = list(resolved.get("focus_shortlists") or [])
            self._trace_events.append({
                "event": "partial_eval_response_shape", "source": self.source,
                "batch_id": f"{self.source}:loop{loop_n}:batch{label}",
                "focus_group_count": len(focus_shortlists),
                "nonempty_focus_group_count": sum(1 for item in focus_shortlists if item.get("admission_chunk_ids")),
                "selected_ref_count": sum(len(set(item.get("admission_chunk_ids") or [])) for item in focus_shortlists),
                "ledger_count": len(resolved.get("focus_ledger") or []),
            })
            if malformed:
                self._trace_events.append({
                    "event": "partial_eval_contract_error", "source": self.source,
                    "batch_id": f"{self.source}:loop{loop_n}:batch{label}",
                    "malformed_chunk_refs": malformed,
                })
            resolved["_submitted_chunk_ids"] = [chunk.id for chunk in batch]
            return resolved

        outputs = await asyncio.gather(*[
            _one(str(i), batch) for i, batch in enumerate(batches, 1)
        ])
        submitted: set[str] = set()
        shortlisted: set[str] = set()
        for output in outputs:
            submitted.update(output.get("_submitted_chunk_ids") or [])
            for cid, judgment in tiers_list_to_map(output.get("tiers")).items():
                shortlisted.add(cid)
                self._trace_events.append({
                    "event": "judgment", "source": self.source, "phase": "partial_eval",
                    "chunk_id": cid, "tier": judgment.get("tier"),
                    "answered_focuses": list(judgment.get("answered_focuses") or []),
                })
                chunk = chunk_pool.get(cid)
                if chunk is not None:
                    chunk.tier = judgment.get("tier")
                    chunk.metadata["relevance"] = judgment.get("relevance")
                    chunk.metadata["answered_focuses"] = list(
                        judgment.get("answered_focuses") or []
                    )
                    chunk.metadata["angle_tag"] = judgment.get("angle_tag", "")
                    chunk.metadata["shortlisted_for_focuses"] = list(
                        judgment.get("answered_focuses") or []
                    )
            for ledger in output.get("focus_ledger") or []:
                self._partial_ledgers.append(dict(ledger))
        # An empty compact response is an admission-model outage/underflow, not
        # evidence that every supplied chunk is noise.  Fail open to the merged
        # plan_eval and preserve an explicit trace marker.  Normal non-empty
        # shortlist responses still remove only omitted entries as designed.
        if submitted and not shortlisted:
            self._trace_events.append({
                "event": "partial_eval_no_selection", "source": self.source, "loop": loop_n,
                "submitted_count": len(submitted), "action": "retain_for_plan_eval",
            })
            return
        removed = submitted - shortlisted
        for cid in removed:
            chunk = chunk_pool.get(cid)
            if chunk is not None:
                chunk.metadata["admission_status"] = "not_shortlisted"
            chunk_pool.pop(cid, None)
        observer = current_role_funnel()
        if observer is not None:
            observer.observe_chunks(
                "partial_shortlisted", [chunk_pool[cid] for cid in shortlisted if cid in chunk_pool],
            )
        self._trace_events.append({
            "event": "partial_shortlist", "source": self.source, "loop": loop_n,
            "submitted_count": len(submitted), "shortlisted_count": len(shortlisted),
            "not_shortlisted_count": len(removed),
            "trigger_chunk_count": shortlist_cfg.trigger_chunk_count,
            "batch_chunk_limit": shortlist_cfg.batch_chunk_limit,
            "focus_shortlists": [
                {"focus_id": item.get("focus_id"),
                 "critical_count": len(item.get("critical_chunk_ids") or []),
                 "important_count": len(item.get("important_chunk_ids") or [])}
                for output in outputs for item in (output.get("focus_shortlists") or [])
            ],
        })
        logger.info("[%s] loop=%d partial-eval not_shortlisted=%d retained=%d", self.source,
                    loop_n, len(removed), len(chunk_pool))

    async def _offer_chunk_for_context_eval(
        self, entry: ChunkEntry, chunk_pool: Dict[str, ChunkEntry], *, loop_n: int = 1,
    ) -> None:
        """Non-blocking I/O hook.  Freeze a batch once its prompt budget fills."""
        self._trace_events.append({
            "event": "read", "source": self.source, "chunk_id": entry.id,
            "focuses": sorted(entry.target_focuses),
            "query_ids": list((entry.metadata or {}).get("sub_query_ids") or
                              (entry.metadata or {}).get("selected_sub_query_ids") or []),
            "route": str((entry.metadata or {}).get("route") or
                         (entry.metadata or {}).get("route_hint") or ""),
            "document": str(entry.metadata.get("source_name", entry.metadata.get("title", ""))),
            "heading": str(entry.metadata.get("heading_path", "")),
            "range": str(entry.metadata.get("range", "")),
            "content_sha256": hashlib.sha256(entry.content.encode("utf-8")).hexdigest(),
            "content_chars": len(entry.content),
            "preview_chars": len(entry.preview_content or ""),
            "preview_fallback": not bool(entry.preview_content),
        })
        observer = current_role_funnel()
        if observer is not None:
            # Gold anchors intentionally inspect full content; preview is only
            # an LLM-eval budget surface and must never decide this match.
            observer.observe_chunks("read", [entry])
        # Streaming partial-eval would otherwise see the pre-rerank pool.
        if self.cfg.rerank_enabled:
            return
        if entry.id in self._partial_submitted_ids or entry.id in self._partial_buffer_ids:
            return
        self._partial_buffer_ids.append(entry.id)
        self._partial_buffer_tokens += estimate_chunk_tokens(entry)
        if self._partial_buffer_tokens < ContextBudget().flush_threshold:
            return
        ids = list(self._partial_buffer_ids)
        batch = [chunk_pool[cid] for cid in ids if cid in chunk_pool]
        self._partial_buffer_ids, self._partial_buffer_tokens = [], 0
        self._partial_submitted_ids.update(c.id for c in batch)
        batch_id = f"{self.source}:loop{loop_n}:stream{len(self._partial_pending_tasks)+1}"

        async def _evaluate() -> Dict[str, Any]:
            registry = ChunkRefRegistry.build(batch)
            output = await _run_llm_agent(
                self.partial_eval,
                _build_partial_eval_prompt(
                    focuses=list(self._current_focus_lookup.values()),
                    chunks=batch,
                    batch_id=batch_id,
                    refs=registry,
                    max_critical_per_focus=self.cfg.eval_shortlist.max_critical_per_focus,
                    max_important_per_focus=self.cfg.eval_shortlist.max_important_per_focus,
                ),
            )
            resolved, malformed = registry.resolve_eval_output(output)
            focus_shortlists = list(resolved.get("focus_shortlists") or [])
            self._trace_events.append({
                "event": "partial_eval_response_shape", "source": self.source,
                "batch_id": batch_id,
                "focus_group_count": len(focus_shortlists),
                "nonempty_focus_group_count": sum(1 for item in focus_shortlists if item.get("admission_chunk_ids")),
                "selected_ref_count": sum(len(set(item.get("admission_chunk_ids") or [])) for item in focus_shortlists),
                "ledger_count": len(resolved.get("focus_ledger") or []),
            })
            if malformed:
                self._trace_events.append({
                    "event": "partial_eval_contract_error", "source": self.source,
                    "batch_id": batch_id, "malformed_chunk_refs": malformed,
                })
            resolved["_submitted_chunk_ids"] = [chunk.id for chunk in batch]
            return resolved

        self._partial_pending_tasks.append(asyncio.create_task(_evaluate(), name=batch_id))
        logger.info("[%s] scheduled partial-eval %s chunks=%d", self.source, batch_id, len(batch))

    async def _drain_partial_evals(self, chunk_pool: Dict[str, ChunkEntry]) -> None:
        if not self._partial_pending_tasks:
            return
        tasks, self._partial_pending_tasks = self._partial_pending_tasks, []
        outputs = await asyncio.gather(*tasks, return_exceptions=True)
        for output in outputs:
            if isinstance(output, Exception):
                logger.warning("[%s] partial-eval task failed: %s", self.source, output)
                continue
            submitted = set(output.get("_submitted_chunk_ids") or [])
            selected = set(tiers_list_to_map(output.get("tiers")))
            for cid, judgment in tiers_list_to_map(output.get("tiers")).items():
                self._trace_events.append({
                    "event": "judgment", "source": self.source, "phase": "partial_eval",
                    "chunk_id": cid, "tier": judgment.get("tier"),
                    "answered_focuses": list(judgment.get("answered_focuses") or []),
                })
                chunk = chunk_pool.get(cid)
                if chunk is not None:
                    chunk.tier = judgment.get("tier")
                    chunk.metadata["relevance"] = judgment.get("relevance")
                    chunk.metadata["answered_focuses"] = list(
                        judgment.get("answered_focuses") or []
                    )
                    chunk.metadata["angle_tag"] = judgment.get("angle_tag", "")
            for cid in submitted - selected:
                chunk_pool.pop(cid, None)
            self._partial_ledgers.extend(dict(x) for x in (output.get("focus_ledger") or []))

    def _post_process_top_chunks(
        self,
        focuses: List[Focus],
        chunks: List[ChunkEntry],
    ) -> List[ChunkEntry]:
        _ = focuses
        return chunks

    def _log_per_focus_tier_distribution(
        self,
        focuses: List[Focus],
        chunks: List[ChunkEntry],
    ) -> None:
        """诊断日志：记录每 focus 的 tier 分布与 0-chunk 警告。

        **只读、不改 chunks**——eval 的 tier 是客观结果，我们不在 source 层
        做"保底升级"。本日志用于事后分析 eval 是否漏评 / 是否需要在 router
        层多分配源。

        对于本源被路由但 ``top_k_chunk_ids`` 完全没出现的 focus，会单独打
        ``coverage_gap`` warning，方便排查 eval 是否客观地把候选都判为 noise，
        还是直接没纳入评估（后者可能要看 instruction 或 chunks_block 渲染）。
        """
        if not focuses:
            return

        per_focus: Dict[str, Dict[str, int]] = {
            f.focus_id: {"critical": 0, "important": 0, "noise": 0, "untiered": 0, "total": 0}
            for f in focuses
        }
        for c in chunks:
            related = set(c.target_focuses or set()) | set(
                (c.metadata or {}).get("answered_focuses") or []
            )
            for fid in related:
                if fid not in per_focus:
                    continue
                per_focus[fid]["total"] += 1
                if c.tier == "critical":
                    per_focus[fid]["critical"] += 1
                elif c.tier == "important":
                    per_focus[fid]["important"] += 1
                elif c.tier == "noise":
                    per_focus[fid]["noise"] += 1
                else:
                    per_focus[fid]["untiered"] += 1

        for fid, stats in per_focus.items():
            if stats["total"] == 0:
                logger.warning(
                    "[%s] eval coverage_gap: focus=%s got 0 chunks in selection "
                    "(check if eval excluded the focus's candidates or if retrieval returned nothing for it)",
                    self.source, fid,
                )
            else:
                logger.info(
                    "[%s] eval_tier_dist focus=%s critical=%d important=%d noise=%d untiered=%d total=%d",
                    self.source, fid,
                    stats["critical"], stats["important"], stats["noise"], stats["untiered"], stats["total"],
                )

    def _assemble_top_chunks(
        self,
        top_ids: List[str],
        tiers_map: Dict[str, Dict],
        chunk_pool: Dict[str, ChunkEntry],
    ) -> List[ChunkEntry]:
        """组装 LLM 选出的 top_k，带两层 call-back 校验（防 LLM 幻觉）。

        截断上限：``self._effective_source_top_k`` —— multi-focus 场景下 base 之上
        每多 1 个 focus 加 2 名额。schema cap 已在 __init__ 时膨胀到 base+EXTRA。
        """
        allowed_focus_ids = set(self._current_focus_lookup.keys())
        out: List[ChunkEntry] = []
        for raw_id in top_ids[: self._effective_source_top_k]:
            # Provider/schema variants sometimes return {"chunk_id": "..."}
            # despite the string contract. Normalize, then enforce the pool
            # whitelist instead of allowing an unhashable value to abort source output.
            cid = raw_id.get("chunk_id") if isinstance(raw_id, dict) else raw_id
            if not isinstance(cid, str) or not cid:
                logger.warning("[%s] malformed top_k chunk id rejected: %r", self.source, raw_id)
                continue
            entry = chunk_pool.get(cid)
            if entry is None:
                # 幻觉 chunk_id：LLM 编了个不存在的 id
                logger.warning("[%s] hallucinated chunk_id rejected: %s", self.source, cid)
                continue
            info = tiers_map.get(cid) or {}
            tier = info.get("tier")
            rel = info.get("relevance")
            raw_ans = info.get("answered_focuses") or []

            # answered_focuses 白名单：过滤非法 focus_id（LLM 偶发幻觉出不存在的 focus）
            valid_ans = [fid for fid in raw_ans if fid in allowed_focus_ids]
            if len(valid_ans) != len(raw_ans):
                dropped = [fid for fid in raw_ans if fid not in allowed_focus_ids]
                logger.warning(
                    "[%s] chunk=%s: dropped invalid answered_focuses=%s (allowed=%s)",
                    self.source, cid, dropped, sorted(allowed_focus_ids),
                )

            # 1) 打 tier
            entry = entry.with_tier(tier)
            # 2) 覆盖 score + 写 metadata
            new_meta = dict(entry.metadata or {})
            if valid_ans:
                new_meta["answered_focuses"] = list(valid_ans)
            # P2: angle_tag —— 同角度去重的唯一依据
            angle_tag = (info.get("angle_tag") or "").strip().lower()
            if angle_tag:
                new_meta["angle_tag"] = angle_tag
            entry = ChunkEntry(
                id=entry.id,
                source=entry.source,
                content=entry.content,
                preview_content=entry.preview_content,
                metadata=new_meta,
                score=float(rel) if rel is not None else entry.score,
                sub_query=entry.sub_query,
                target_focuses=entry.target_focuses,
                tier=entry.tier,
            )
            out.append(entry)
        return out

    # ── 并发执行 sub_queries ──────────────────────────────────
    async def _execute_batch(
        self, sub_queries: List[SubQuery], chunk_pool: Dict[str, ChunkEntry], *, loop_n: int = 1,
    ) -> None:
        # P5: 提交前 gate —— focus_board 已 SATISFIED 的 target_focus 直接跳过 I/O
        board = getattr(self, "_focus_board", None)
        if board is not None:
            before_n = len(sub_queries)
            sub_queries = [sq for sq in sub_queries if not board.is_satisfied(sq.target_focus)]
            if len(sub_queries) < before_n:
                logger.info(
                    "[%s] _execute_batch: skipped %d sub_queries (focus already satisfied externally)",
                    self.source, before_n - len(sub_queries),
                )
        if not sub_queries:
            return

        async def _one(sq: SubQuery) -> List[ChunkEntry]:
            # P5 (event-driven): 在飞行 cancel —— 用 asyncio.wait FIRST_COMPLETED 同时
            # 监听 fetch_task 与 focus 满足事件，触发哪个就响应哪个，无 polling 开销
            try:
                if board is None:
                    return await self.execute_sub_query_full(sq)
                sat_event = board.satisfied_event(sq.target_focus)
                if sat_event is None or sat_event.is_set():
                    # 事件不可用或 focus 已经满足 —— 直接跳过
                    if sat_event is not None and sat_event.is_set():
                        logger.info(
                            "[%s] sub_query gate: focus=%s already satisfied → skip",
                            self.source, sq.target_focus,
                        )
                        return []
                    return await self.execute_sub_query_full(sq)

                fetch_task = asyncio.create_task(self.execute_sub_query_full(sq))
                sat_task = asyncio.create_task(sat_event.wait())
                done, pending = await asyncio.wait(
                    {fetch_task, sat_task}, return_when=asyncio.FIRST_COMPLETED,
                )

                if fetch_task in done:
                    # fetch 先完成 —— 取消 sat_task 并返回结果
                    sat_task.cancel()
                    return fetch_task.result()

                # focus satisfied 信号先到 —— 取消 fetch
                fetch_task.cancel()
                try:
                    await fetch_task
                except (asyncio.CancelledError, Exception):
                    pass
                logger.info(
                    "[%s] in-flight cancel sq=%r (focus=%s satisfied externally)",
                    self.source, sq.query[:60], sq.target_focus,
                )
                return []
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("[%s] sub_query %r failed: %s", self.source, sq.query, e)
                return []

        batches = await asyncio.gather(*[_one(sq) for sq in sub_queries])
        for sq, chunks in zip(sub_queries, batches):
            self._trace_events.append({
                "event": "candidate", "source": self.source, "loop": loop_n,
                "focus": sq.target_focus, "query_id": sq.sub_query_id,
                "query_type": sq.query_type, "api_query": sq.query,
                "candidate_count": len(chunks),
                "candidates": [
                    {"chunk_id": c.id, "title": str(c.metadata.get("source_name", c.metadata.get("title", ""))),
                     "type": str(c.metadata.get("resource_type", c.metadata.get("type", ""))), "api_rank": index}
                    for index, c in enumerate(chunks, 1)
                ],
            })
            for c in chunks:
                # 保证 target_focus / sub_query 标记正确
                entry = ChunkEntry(
                    id=c.id,
                    source=c.source,
                    content=c.content,
                    preview_content=c.preview_content,
                    metadata=dict(c.metadata),
                    score=c.score,
                    sub_query=c.sub_query or sq.query,
                    target_focuses=set(c.target_focuses) | {sq.target_focus},
                    tier=c.tier,
                )
                existing = chunk_pool.get(entry.id)
                chunk_pool[entry.id] = existing.merge(entry) if existing else entry
                await self._offer_chunk_for_context_eval(entry, chunk_pool, loop_n=loop_n)

    # ── 静态引用（Phase 2 用） ──────────────────────────────────
    @staticmethod
    def _loop_should_continue(
        decision: str, loop_n: int, min_loops: int, max_loops: int,
    ) -> bool:
        return enforce_loop_bounds(decision, loop_n, min_loops, max_loops) == "continue"

    @staticmethod
    def _needs_eval(loop_n: int, min_loops: int, max_loops: int) -> bool:
        return needs_eval(loop_n, min_loops, max_loops)
