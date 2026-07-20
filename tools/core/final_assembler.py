"""Final Assembler (P3)——retrieve_agent 出口的统一装填器。

职责：
1. 同角度降级（pure rule）：同 focus 内 angle_tag 计数 > 2 的 critical
   按 relevance 升序，把最低 relevance 的降为 important，重复直到 ≤ 2。
2. 轮转配额制（pure rule）：按 focus 顺序优先满足 per_focus_quota，再按
   focus 顺序轮转补齐到 final_top_k 硬上限（不足则输出实际数量）。
3. LLM eval_layer（可选）：把候选交给 Gemini Lite 重排，输出 chunk_id 索引；
   失败/超时 → 退回纯规则路径。
4. 程序回填：按 retrieve_agent 出口 schema 拼最终 dict。
   LLM 不接触最终 schema，schema 与 LLM 解耦。

设计原则：
- final_top_k 是硬上限，候选不足时输出实际数量，可少不可多。
- LLM 仅返回 {focus_id: [chunk_id]}，不重复 chunk 内容（节省 token + 防幻觉）。
- 同角度降级与 focus 轮转都是纯函数，单测友好。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from tools.core.models import ChunkEntry


logger = logging.getLogger(__name__)


_TIER_RANK = {"critical": 0, "important": 1, "noise": 2, None: 3}


# ─────────────────────────────────────────────────────────────────────
# 1. 同角度降级
# ─────────────────────────────────────────────────────────────────────
def demote_redundant_angles(
    chunks: List[ChunkEntry],
    max_per_angle: int = 2,
) -> List[ChunkEntry]:
    """对每个 focus 内 angle_tag 出现次数 > max_per_angle 的 critical chunks，
    按 relevance 升序把最低的降为 important。返回新列表（不原地修改）。

    - 仅降级 critical → important（不动 important / noise）
    - 相同 angle_tag 缺失（空字符串）的 chunks 不参与去重（视为不同角度）
    - 同一 chunk 可能命中多个 focus；每个 focus 独立判断降级，但实际降级动作落在
      ChunkEntry.tier（共享）。这里采取"任一 focus 触发降级即降级"的保守策略。

    时间复杂度 O(n log n)。
    """
    if not chunks:
        return chunks

    # 收集所有需要被降级的 chunk_id
    to_demote: set = set()

    # 按 focus_id 分组：仅看每条 chunk 的 target_focuses
    by_focus_angle: Dict[Tuple[str, str], List[ChunkEntry]] = {}
    for c in chunks:
        if c.tier != "critical":
            continue
        angle = ((c.metadata or {}).get("angle_tag") or "").strip().lower()
        if not angle:
            continue
        for fid in (c.target_focuses or set()):
            by_focus_angle.setdefault((fid, angle), []).append(c)

    for (fid, angle), group in by_focus_angle.items():
        if len(group) <= max_per_angle:
            continue
        # 按 relevance 升序，前 N 条（多余部分）标记降级
        group_sorted = sorted(group, key=lambda c: (c.score or 0.0))
        n_demote = len(group) - max_per_angle
        for c in group_sorted[:n_demote]:
            to_demote.add(c.id)
            logger.debug(
                "demote_redundant_angles: focus=%s angle=%s id=%s rel=%.3f",
                fid, angle, c.id[:50], c.score or 0.0,
            )

    if not to_demote:
        return chunks

    out: List[ChunkEntry] = []
    for c in chunks:
        if c.id in to_demote and c.tier == "critical":
            out.append(c.with_tier("important"))
        else:
            out.append(c)
    logger.info("demote_redundant_angles: demoted %d critical → important", len(to_demote))
    return out


# ─────────────────────────────────────────────────────────────────────
# 2. 轮转配额制
# ─────────────────────────────────────────────────────────────────────
def _bucket_by_focus(
    chunks: List[ChunkEntry],
    focuses: List[Any],  # 需要 .focus_id 属性
) -> Dict[str, List[ChunkEntry]]:
    """按 focus_id 分桶；每桶内按 (tier_rank, -relevance) 排序。"""
    buckets: Dict[str, List[ChunkEntry]] = {f.focus_id: [] for f in focuses}
    for c in chunks:
        # 优先从 answered_focuses 取，否则用 target_focuses
        answered = set((c.metadata or {}).get("answered_focuses") or [])
        focus_ids = answered or set(c.target_focuses or set())
        for fid in focus_ids:
            if fid in buckets:
                buckets[fid].append(c)
    for fid in buckets:
        buckets[fid].sort(key=lambda c: (_TIER_RANK.get(c.tier, 3), -(c.score or 0.0)))
    return buckets


def round_robin_fill(
    chunks: List[ChunkEntry],
    focuses: List[Any],
    final_top_k: int,
    focus_quota: Dict[str, int],
) -> List[ChunkEntry]:
    """轮转配额制装填。

    Step 1：按 focus 顺序，每 focus 取 critical_min 条 critical + important_min 条 important。
    Step 2：按 focus 顺序轮转补齐到 final_top_k；每轮每 focus 取 1 条最高 tier 的剩余。
    Step 3：硬截断到 final_top_k。

    返回保持 focus 顺序的合并列表。
    """
    if not chunks or not focuses or final_top_k <= 0:
        return []

    buckets = _bucket_by_focus(chunks, focuses)
    critical_min = max(0, int(focus_quota.get("critical_min", 0)))
    important_min = max(0, int(focus_quota.get("important_min", 0)))

    picked_ids: set = set()
    out: List[ChunkEntry] = []

    # Step 1: 满足每 focus 的 quota
    for f in focuses:
        bucket = buckets.get(f.focus_id, [])
        crit_taken = 0
        imp_taken = 0
        for c in bucket:
            if c.id in picked_ids:
                continue
            if c.tier == "critical" and crit_taken < critical_min:
                out.append(c); picked_ids.add(c.id); crit_taken += 1
            elif c.tier == "important" and imp_taken < important_min:
                out.append(c); picked_ids.add(c.id); imp_taken += 1
            if crit_taken >= critical_min and imp_taken >= important_min:
                break
            if len(out) >= final_top_k:
                break
        if len(out) >= final_top_k:
            break

    # Step 2: 轮转补齐到 final_top_k
    while len(out) < final_top_k:
        added_this_round = False
        for f in focuses:
            if len(out) >= final_top_k:
                break
            bucket = buckets.get(f.focus_id, [])
            for c in bucket:
                if c.id in picked_ids:
                    continue
                if c.tier == "noise":
                    continue                        # noise 永不入选
                out.append(c); picked_ids.add(c.id)
                added_this_round = True
                break
        if not added_this_round:
            break                                   # 所有 focus 桶都空 → 候选耗尽

    # 硬截断（防御）
    if len(out) > final_top_k:
        out = out[:final_top_k]
    return out


# ─────────────────────────────────────────────────────────────────────
# 3. LLM eval_layer（输入合约 + 输出合约 + 兜底）
# ─────────────────────────────────────────────────────────────────────
@dataclass
class LlmAssemblerConfig:
    """LLM eval_layer 调用配置。"""
    enabled: bool = False
    timeout_s: float = 15.0
    content_preview_chars: int = 200
    # 触发阈值：candidates > final_top_k 时才调 LLM；False 则总是调
    trigger_only_when_overflow: bool = True


_LLM_INSTRUCTION = """你是检索装填器。从 candidates 中为每个 focus 挑选最能回答它的 chunks。

## 输入（user message 是一段 JSON 字符串）
- query: 用户原始 query
- preset: lite / balanced / deep_dive
- final_top_k: 最终输出硬上限（不可超过）
- focus_quota: {critical_min, important_min} 每 focus 满足判据
- focuses: [{focus_id, text}]
- candidates: 候选 chunk 列表，每条含 (chunk_id, source, tier, relevance,
  answered_focuses, angle_tag, content_preview)

## 选择规则
1. 优先满足每 focus 的 quota（critical_min / important_min）
2. **尽可能装填到 final_top_k**：只要候选不是「绝对低质量」（明显偏题 / 严重过期 /
   重复信息 / relevance < 0.3），就应入选；不要主动控量
3. 在满足 quota 后，**角度多样性优先**：同 focus 内尽量选不同 angle_tag，但若候选
   不足以装满，**重复 angle_tag 也可以入选**（angle 多样是加分项，不是硬约束）
4. 不主动丢弃信号：若 candidates ≥ final_top_k，**理论上应输出 final_top_k 条**；
   只有当大量候选都明显低质量时才允许输出 < final_top_k
5. 总数硬上限 final_top_k，绝不超出

## 输出 schema（严格 JSON，仅返回 chunk_id 索引；不要复制 content）
{
  "selections": [
    {"focus_id": "focus_0", "chunk_ids": ["chunk_id_a", "chunk_id_b"]},
    {"focus_id": "focus_1", "chunk_ids": []}
  ],
  "rationale": "一句话说明本次选择的关键考量"
}
"""


def build_final_assembler_agent():
    """构造装填器 LlmAgent（lazy import，避免在测试 / 无 ADK 环境时强依赖）。"""
    from google.adk.agents.llm_agent import LlmAgent
    from tools.core.llm_contracts import MODEL, FinalAssemblerOutput
    return LlmAgent(
        name="final_assembler",
        model=MODEL,
        instruction=_LLM_INSTRUCTION,
        output_schema=FinalAssemblerOutput,
    )


def _make_llm_input(
    query: str,
    preset: str,
    final_top_k: int,
    focuses: List[Any],
    focus_quota: Dict[str, int],
    candidates: List[ChunkEntry],
    preview_chars: int,
) -> str:
    """组装 LLM 输入 prompt（JSON-in-text 形式）。"""
    payload = {
        "query": query,
        "preset": preset,
        "final_top_k": final_top_k,
        "focus_quota": focus_quota,
        "focuses": [{"focus_id": f.focus_id, "text": f.text} for f in focuses],
        "candidates": [
            {
                "chunk_id": c.id,
                "source": c.source,
                "tier": c.tier or "",
                "relevance": round(c.score or 0.0, 3),
                "answered_focuses": list((c.metadata or {}).get("answered_focuses") or []),
                "angle_tag": (c.metadata or {}).get("angle_tag") or "",
                "content_preview": (c.preview_content or c.content or "")[:preview_chars],
            }
            for c in candidates
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _parse_llm_output(
    raw: Dict[str, Any],
    candidates_by_id: Dict[str, ChunkEntry],
    focuses: List[Any],
    final_top_k: int,
) -> Optional[List[ChunkEntry]]:
    """解析 LLM 输出 → 按 focus 顺序展开 chunks 列表。

    校验：
    - selections 必须是 dict；focus_id 必须在 focuses 内
    - chunk_id 必须存在于 candidates；不存在的丢弃
    - 总数 > final_top_k 时硬截断
    返回 None 表示解析失败需走兜底。
    """
    selections_raw = raw.get("selections")
    # 支持两种形态：List[{focus_id, chunk_ids}]（Gemini schema 友好）/ Dict[focus_id, [...]]（手写）
    selections: Dict[str, List[str]] = {}
    if isinstance(selections_raw, list):
        for entry in selections_raw:
            if not isinstance(entry, dict):
                continue
            fid = entry.get("focus_id")
            ids = entry.get("chunk_ids") or []
            if isinstance(fid, str) and isinstance(ids, list):
                selections[fid] = [x for x in ids if isinstance(x, str)]
    elif isinstance(selections_raw, dict):
        for fid, ids in selections_raw.items():
            if isinstance(fid, str) and isinstance(ids, list):
                selections[fid] = [x for x in ids if isinstance(x, str)]
    else:
        logger.warning("LLM assembler output 'selections' missing or wrong type")
        return None

    valid_focus_ids = {f.focus_id for f in focuses}
    out: List[ChunkEntry] = []
    seen: set = set()
    for f in focuses:                                # 保持 focus 顺序
        for cid in selections.get(f.focus_id, []):
            if cid in seen:
                continue
            c = candidates_by_id.get(cid)
            if c is None:
                logger.debug("LLM assembler hallucinated chunk_id: %s", cid)
                continue
            out.append(c); seen.add(cid)
            if len(out) >= final_top_k:
                break
        if len(out) >= final_top_k:
            break

    # 检查未知 focus_id
    for fid in selections:
        if fid not in valid_focus_ids:
            logger.debug("LLM assembler emitted unknown focus_id: %s", fid)

    return out


# ─────────────────────────────────────────────────────────────────────
# 4. 主装填器：组合纯规则 + LLM 路径
# ─────────────────────────────────────────────────────────────────────
async def assemble_final_chunks(
    chunks: List[ChunkEntry],
    focuses: List[Any],
    final_top_k: int,
    focus_quota: Dict[str, int],
    *,
    preset: str = "lite",
    query: str = "",
    llm_config: Optional[LlmAssemblerConfig] = None,
    llm_invoker: Optional[Callable[[str], Any]] = None,
) -> List[ChunkEntry]:
    """组合调用入口。

    Pipeline:
      1. demote_redundant_angles  (pure)
      2. 决定是否走 LLM 路径
         - 不走：直接 round_robin_fill
         - 走：构造 LLM input → invoke → parse；解析失败回退 round_robin_fill
      3. 返回选中的 chunks 列表（保持 focus 顺序）

    Args:
        chunks: merge 后的全量候选 chunks
        focuses: 全局 focuses 列表（保持顺序）
        final_top_k: 硬上限
        focus_quota: {"critical_min": int, "important_min": int}
        preset: 仅传给 LLM 用作上下文
        query: 原始 query 文本（LLM 上下文）
        llm_config: LLM 调用配置；None 时禁用 LLM
        llm_invoker: async callable(prompt_text) -> dict；None 时禁用 LLM
    """
    if not chunks or not focuses or final_top_k <= 0:
        return []

    # Step 1: 纯规则降级
    chunks = demote_redundant_angles(chunks)

    # Step 2: 决定路径
    use_llm = bool(
        llm_config
        and llm_config.enabled
        and llm_invoker is not None
        and (not llm_config.trigger_only_when_overflow or len(chunks) > final_top_k)
    )

    if use_llm:
        try:
            prompt = _make_llm_input(
                query, preset, final_top_k, focuses, focus_quota, chunks,
                llm_config.content_preview_chars,
            )
            raw = await llm_invoker(prompt)          # type: ignore[misc]
            picked = _parse_llm_output(raw, {c.id: c for c in chunks}, focuses, final_top_k)
            if picked is not None:
                if len(picked) > final_top_k:
                    picked = picked[:final_top_k]
                logger.info(
                    "assemble_final_chunks: LLM path picked=%d / candidates=%d / cap=%d",
                    len(picked), len(chunks), final_top_k,
                )
                return picked
            logger.warning("assemble_final_chunks: LLM output unparseable, fallback to rule")
        except Exception as e:
            logger.exception("assemble_final_chunks: LLM call failed: %s, fallback to rule", e)

    # Step 3: 纯规则兜底
    picked = round_robin_fill(chunks, focuses, final_top_k, focus_quota)
    logger.info(
        "assemble_final_chunks: rule path picked=%d / candidates=%d / cap=%d",
        len(picked), len(chunks), final_top_k,
    )
    return picked


# ─────────────────────────────────────────────────────────────────────
# 5. 出口 schema 回填
# ─────────────────────────────────────────────────────────────────────
def render_final_payload(
    query: str,
    chunks: List[ChunkEntry],
    focuses: List[Any],
) -> Dict[str, Any]:
    """组装 retrieve_agent 出口 schema：{query, chunks: [...]}。

    每条 chunk 9 个字段（与 spec_plan 对齐）：
      text / source_name / source_url / source_tool / heading_path /
      confidence_tier / focus_query / sub_query / chunk_id
    """
    fid_to_text = {f.focus_id: f.text for f in focuses}
    chunks_out: List[Dict[str, Any]] = []
    for c in chunks:
        # Last boundary invariant: evaluation footprints and unjudged chunks
        # can remain in operational trace, never in downstream full content.
        if c.tier not in {"critical", "important"}:
            continue
        meta = c.metadata or {}
        # focus_query 优先用 LLM 标注的 answered_focuses，否则 fall back 到 target_focuses
        focus_ids = list((meta.get("answered_focuses") or [])) or sorted(c.target_focuses or set())
        focus_query = [fid_to_text[fid] for fid in focus_ids if fid in fid_to_text]
        chunks_out.append({
            "text": c.content,
            "source_name": meta.get("source_name", ""),
            "source_url": meta.get("source_url", ""),
            "source_tool": c.source,
            "heading_path": meta.get("heading_path", ""),
            "confidence_tier": c.tier or "",
            "focus_query": focus_query,
            "sub_query": c.sub_query,
            "chunk_id": c.id,
        })
    return {"query": query, "chunks": chunks_out}
