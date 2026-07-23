"""eval_layer——跨源合并后的全局重排与截断（Phase 4b Step C/E）。

职责：
  - 输入：merge 后的 ``List[ChunkEntry]``（已经由各 SourceAgent 内部的 eval_only
    或 plan_eval 打过 tier / relevance / answered_focuses）
  - 输出：按全局公式重排 + min-keep 截断 + focus 补齐的 ``List[ChunkEntry]``

两种实现：
  RuleEvalLayer（lite/balanced，mode="rule"）：
    公式：final = 0.45*tier + 0.40*relevance + 0.15*coverage_bonus
    零 LLM 成本，亚毫秒级完成。

  LlmEvalLayer（deep_dive，mode="llm_tool"，Phase 4b Step E）：
    跨源全局二次 LLM 排序——各源 eval_only 打 tier 时只看单源上下文，
    LlmEvalLayer 站在所有源之上，比较同 focus 下多源 chunk 的相对质量，
    可升降 tier、调整 focus 归属，并输出全局最优 top_k。
    失败时 fallback 到 RuleEvalLayer。
    retrieve_agent 通过 asyncio.to_thread 调用，不阻塞事件循环。
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, List, Optional, Protocol

from tools.core.models import ChunkEntry


logger = logging.getLogger(__name__)


# ── 参数 dataclass（便于实验扫描） ─────────────────────────────────
@dataclass
class RuleEvalParams:
    """RuleEvalLayer 参数；未指定时用论证过的默认值。"""
    # tier 权重
    tier_w_critical: float = 1.0
    tier_w_important: float = 0.6
    tier_w_noise: float = 0.1
    tier_w_none: float = 0.3

    # 公式权重（之和可以不为 1，但建议保持）
    # E5 实验结论（Phase 4b Step C）：rel_heavy 组 crit_ρ 0.733 / focus_cov 1.00 最优，
    # 相比 (0.55/0.30/0.15) 提升 +5pp critical 密度 + +3pp 覆盖率。relevance 由 LLM
    # 直接给出的 [0,1] 连续分质量高，值得加重权重。
    w_tier: float = 0.45
    w_relevance: float = 0.40
    w_coverage: float = 0.15

    # coverage bonus 阶梯（n=1→0, n=2→0.25, n=3→0.5, n≥5→1.0）
    coverage_step: float = 0.25

    # 截断区间（比例）
    min_keep_ratio: float = 0.25
    max_keep_ratio: float = 0.80

    # 目标 k（取 min(target_k, n_focus * n_per_focus)）
    n_per_focus: int = 3

    # focus 补齐开关（E6 扫描用）
    focus_backfill: bool = True


# ── Protocol ─────────────────────────────────────────────────────
class EvalLayer(Protocol):
    """跨源全局重排层接口。"""

    def rank(
        self,
        chunks: List[ChunkEntry],
        focuses: List[object],  # Focus-like: 具 focus_id 属性
        top_k: int,
    ) -> List[ChunkEntry]:
        ...


# ── 公式组件 ─────────────────────────────────────────────────────
def _tier_weight(tier: Optional[str], p: RuleEvalParams) -> float:
    if tier == "critical":
        return p.tier_w_critical
    if tier == "important":
        return p.tier_w_important
    if tier == "noise":
        return p.tier_w_noise
    return p.tier_w_none


def _coverage_bonus(n_answered: int, p: RuleEvalParams) -> float:
    return min(1.0, max(0, n_answered - 1) * p.coverage_step)


def _compute_final_score(c: ChunkEntry, p: RuleEvalParams) -> float:
    """主公式：tier + relevance + coverage。"""
    tw = _tier_weight(c.tier, p)
    rel = float(c.score or 0.0)  # relevance 已写回 score
    n_ans = len((c.metadata or {}).get("answered_focuses") or [])
    cov = _coverage_bonus(n_ans, p)
    return p.w_tier * tw + p.w_relevance * rel + p.w_coverage * cov


# ── RuleEvalLayer ────────────────────────────────────────────────
_TIER_RANK = {"critical": 0, "important": 1, "noise": 2, None: 3}


@dataclass
class RuleEvalLayer:
    """规则版全局重排层。"""
    params: RuleEvalParams = field(default_factory=RuleEvalParams)

    def rank(
        self,
        chunks: List[ChunkEntry],
        focuses: List[object],  # 每个有 .focus_id
        top_k: int,
    ) -> List[ChunkEntry]:
        p = self.params
        n_total = len(chunks)
        if n_total == 0:
            return []

        # 1) 打分 + 排序（主键 final，次键 tier_rank，三键 score）
        scored: List[tuple] = []
        for c in chunks:
            final = _compute_final_score(c, p)
            scored.append((final, _TIER_RANK.get(c.tier, 3), -(c.score or 0.0), c))
        scored.sort(key=lambda x: (-x[0], x[1], x[2]))

        # 2) min-keep 截断计算
        target_k = min(top_k, max(1, len(focuses) * p.n_per_focus))
        cap_lo = max(1, math.ceil(n_total * p.min_keep_ratio))
        cap_hi = max(cap_lo, math.floor(n_total * p.max_keep_ratio))
        effective_k = max(cap_lo, min(cap_hi, target_k))

        primary = [t[3] for t in scored[:effective_k]]
        leftover = [t[3] for t in scored[effective_k:]]

        # 3) focus 补齐（可选）
        if p.focus_backfill and focuses:
            primary = self._backfill_focuses(
                primary, leftover, focuses, cap_hi=cap_hi, params=p,
            )

        logger.info(
            "RuleEvalLayer: n_total=%d target_k=%d cap=[%d,%d] final_k=%d",
            n_total, target_k, cap_lo, cap_hi, len(primary),
        )
        return primary

    @staticmethod
    def _focus_hit(c: ChunkEntry, focus_id: str) -> bool:
        """该 chunk 是否命中该 focus（tier 达标 && answered 包含）。"""
        if c.tier not in ("critical", "important"):
            return False
        answered = (c.metadata or {}).get("answered_focuses") or []
        return focus_id in answered or focus_id in (c.target_focuses or set())

    def _backfill_focuses(
        self,
        primary: List[ChunkEntry],
        leftover: List[ChunkEntry],
        focuses: List[object],
        cap_hi: int,
        params: RuleEvalParams,
    ) -> List[ChunkEntry]:
        """检查每个 focus 是否在 primary 里有达标命中，缺则从 leftover 挑最高分补一条。"""
        primary_ids = {c.id for c in primary}
        out = list(primary)
        for f in focuses:
            fid = getattr(f, "focus_id", None)
            if not fid:
                continue
            # 已有命中？
            if any(self._focus_hit(c, fid) for c in out):
                continue
            # 从 leftover 找候选：优先 answered 含 fid，其次 target_focuses 含 fid
            candidate = None
            for c in leftover:
                if c.id in primary_ids:
                    continue
                answered = (c.metadata or {}).get("answered_focuses") or []
                if fid in answered:
                    candidate = c
                    break
            if candidate is None:
                for c in leftover:
                    if c.id in primary_ids:
                        continue
                    if fid in (c.target_focuses or set()):
                        candidate = c
                        break
            if candidate is None:
                logger.debug("RuleEvalLayer: no backfill candidate for focus=%s", fid)
                continue
            if len(out) >= cap_hi:
                # 超上限就替换最末一条（保护 focus 覆盖比保护个别 chunk 重要）
                out[-1] = candidate
            else:
                out.append(candidate)
            primary_ids.add(candidate.id)
            logger.debug("RuleEvalLayer: backfilled %s for focus=%s", candidate.id[:40], fid)
        return out


# ── 工厂 ─────────────────────────────────────────────────────────
def build_rule_eval_layer(params: Optional[RuleEvalParams] = None) -> EvalLayer:
    return RuleEvalLayer(params=params or RuleEvalParams())


# ── LlmEvalLayer（Phase 4b Step E）───────────────────────────────

_TABLE_MARKERS = ("| --- |", "| --- ", "Bitable:", "Sheet:")


def _render_chunk_for_eval(c: ChunkEntry, base_max: int = 300, *, chunk_ref: str | None = None) -> str:
    """与 source_agent_base._chunks_block 保持一致的单 chunk 渲染。

    - 表格 chunk 使用 500 字宽窗口（同 _chunks_block）
    - 输出 chunk_id（非 id alias）供 schema 对齐 TierAssignment.chunk_id
    """
    # Cross-source evaluation follows the same contract as source eval: inspect
    # the evaluation view, while final assembly hydrates ``content`` by ID.
    content = c.preview_content or c.content or ""
    is_table = any(m in content for m in _TABLE_MARKERS)
    limit = 500 if is_table else base_max
    snippet = content.replace("\n", " ").strip()
    if len(snippet) > limit:
        snippet = snippet[:limit] + "..."

    meta = c.metadata or {}
    source_label = meta.get("source_name") or ""
    heading = meta.get("heading_path") or ""
    modified = meta.get("modified_time") or meta.get("modifiedTime") or meta.get("drive_modified") or ""
    answered = sorted(set(meta.get("answered_focuses") or []))
    angle_tag = meta.get("angle_tag") or ""
    sq = (c.sub_query or "")[:40]

    parts = [
        f"chunk_ref={chunk_ref or c.id}",
        f"source={c.source}",
        f"tier={c.tier or 'none'}",
        f"relevance={c.score or 0:.3f}",
        f"focuses={sorted(c.target_focuses or set())}",
    ]
    if answered:
        parts.append(f"answered={answered}")
    if angle_tag:
        parts.append(f"angle={angle_tag}")
    if source_label:
        parts.append(f"src={source_label[:40]}")
    if modified:
        parts.append(f"modified={str(modified)[:10]}")
    if heading:
        parts.append(f"heading={heading[:50]}")
    if sq:
        parts.append(f"sq={sq}")

    return "- " + " | ".join(parts) + f"\n  {snippet}"


def _build_llm_eval_layer_prompt(
    chunks: List[ChunkEntry],
    focuses: List[object],
    top_k: int,
    chunk_refs: Dict[str, str] | None = None,
) -> str:
    """构建 LlmEvalLayer 的 LLM prompt。

    对齐 eval_only / plan_eval 的 prompt 规范：
    - 使用相同 EVAL_RUBRIC_BLOCK（跨源 tier 标准统一）
    - chunk 渲染格式与 _chunks_block 保持一致（source_name / heading / modified）
    - 输出 schema 对齐 TierAssignment：chunk_id + tier + relevance + answered_focuses + angle_tag
    """
    from tools.core.llm_contracts import EVAL_RUBRIC_BLOCK

    focus_lines = [f"- {getattr(f, 'focus_id', '?')}: {getattr(f, 'text', '')}" for f in focuses]
    focuses_block = "\n".join(focus_lines)

    # 分组渲染：按 focus 顺序展示，每条 chunk 归属第一个命中 focus
    placed: set = set()
    chunk_lines: List[str] = []
    for f in focuses:
        fid = getattr(f, "focus_id", "?")
        members = [c for c in chunks if fid in (c.target_focuses or set()) and c.id not in placed]
        if members:
            chunk_lines.append(f"\n## {fid}: {getattr(f, 'text', '')}")
            for c in members:
                chunk_lines.append(_render_chunk_for_eval(c, chunk_ref=(chunk_refs or {}).get(c.id)))
                placed.add(c.id)
    leftover = [c for c in chunks if c.id not in placed]
    if leftover:
        chunk_lines.append("\n## Unassigned")
        for c in leftover:
            chunk_lines.append(_render_chunk_for_eval(c, chunk_ref=(chunk_refs or {}).get(c.id)))
    chunks_block = "\n".join(chunk_lines)

    n_focuses = len(focuses)
    valid_fids = [getattr(f, "focus_id", "?") for f in focuses]

    # multi-focus 硬约束文本：每 focus 至少 1 条
    if n_focuses > 1:
        per_focus_min_line = (
            f"- 每个 focus（共 {n_focuses} 个）在 selected_ids 中**至少选 1 条**"
            f"（哪怕最强候选只是 noise，也要选进来让下游看到'已评估'的脚印）"
        )
        # answered_focuses 示例展示跨 focus 赋值，用 json.dumps 保证双引号格式
        example_answered = json.dumps(valid_fids[:2], ensure_ascii=False)
    else:
        per_focus_min_line = ""
        example_answered = json.dumps([valid_fids[0]] if valid_fids else ["focus_0"], ensure_ascii=False)

    constraints = f"""\
- selected_ids 长度 ≤ {top_k}，对应 tiers 须覆盖全部 selected_ids
{per_focus_min_line + chr(10) if per_focus_min_line else ""}\
- chunk_id 必须是上方 chunks_block 中打印的 chunk_ref（C1, C2, ...），不允许输出 canonical ID
- answered_focuses 中的 focus_id 须来自 focuses_block：{valid_fids}
- 一条 chunk 同时覆盖多个 focus 时，answered_focuses 列出所有命中的 focus_id
- relevance 为 [0,1] 连续分；angle_tag 为 1-2 个英文小写词"""

    return f"""\
你是跨源全局检索评估器（eval_layer）。

各来源（bailian / feishu / google）的 eval_only 已在**单源视角**下对每条 chunk 打了初始 tier。
你的任务是在**全局视角**下二次评估：同一 focus 下多来源证据的相对质量，升降 tier，
选出全局最优的 {top_k} 条 chunk，并输出完整的评估赋值。

{EVAL_RUBRIC_BLOCK}

current_date: {date.today().isoformat()}

focuses_block:
{focuses_block}

chunks_block（{len(chunks)} 条，按 focus 分组）：
{chunks_block}

global_top_k: {top_k}

## 输出 schema（严格 JSON，不输出任何其他文字）
{{
  "selected_ids": ["<chunk_ref1>", "<chunk_ref2>", ...],
  "tiers": [
    {{
      "chunk_id": "<chunk_ref>",
      "tier": "critical",
      "relevance": 0.90,
      "answered_focuses": {example_answered},
      "angle_tag": "timeline"
    }}
  ]
}}

约束：
{constraints}
"""


_VALID_TIERS = {"critical", "important", "noise"}


def _normalize_eval_payload(parsed: object, *, top_k: int) -> Dict[str, List[Dict] | List[str]]:
    """Normalize the JSON-mode response without silently discarding LLM work.

    DeepSeek JSON mode normally returns the requested object.  In practice it
    can emit a top-level list of tier assignments even when the object schema
    was supplied.  That is still useful evidence judgment, not a reason to
    degrade to the unrelated rule selector.  Recover selected IDs from the
    tier list in tier/relevance order and leave hallucinated IDs to the normal
    callback below.
    """
    if isinstance(parsed, dict):
        return {
            "selected_ids": list(parsed.get("selected_ids") or []),
            "tiers": list(parsed.get("tiers") or []),
        }
    if isinstance(parsed, list):
        # Some OpenAI-compatible providers wrap the requested object in a
        # one-element array, or append a harmless non-tier object to a tier
        # list.  Recover the useful, schema-shaped part without treating an
        # arbitrary prose list as evidence judgment.
        wrappers = [
            item for item in parsed
            if isinstance(item, dict) and ("selected_ids" in item or "tiers" in item)
        ]
        if len(wrappers) == 1:
            wrapper = wrappers[0]
            tiers = wrapper.get("tiers") or []
            if isinstance(tiers, list):
                return {
                    "selected_ids": list(wrapper.get("selected_ids") or []),
                    "tiers": [dict(item) for item in tiers if isinstance(item, dict)],
                }
        rows = [
            dict(item) for item in parsed
            if isinstance(item, dict) and isinstance(item.get("chunk_id"), str)
        ]
        if not rows:
            item_types = ",".join(sorted({type(item).__name__ for item in parsed}))
            raise TypeError(f"eval JSON list has no usable tier rows; item_types={item_types}")
        tier_rank = {"critical": 0, "important": 1, "noise": 2}
        rows.sort(key=lambda row: (
            tier_rank.get(str(row.get("tier")), 3),
            -float(row.get("relevance") or 0.0),
        ))
        return {
            "selected_ids": [str(row["chunk_id"]) for row in rows if row.get("chunk_id")][:top_k],
            "tiers": rows,
        }
    raise TypeError(f"eval JSON must be object or tier list, got {type(parsed).__name__}")

# OpenAI-compatible JSON schema: aligns with TierAssignment + selected_ids.
_EVAL_LAYER_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "selected_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "tiers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chunk_id":        {"type": "string"},
                    "tier":            {"type": "string", "enum": ["critical", "important", "noise"]},
                    "relevance":       {"type": "number"},
                    "answered_focuses":{"type": "array", "items": {"type": "string"}},
                    "angle_tag":       {"type": "string"},
                },
                "required": ["chunk_id", "tier", "relevance", "answered_focuses", "angle_tag"],
            },
        },
    },
    "required": ["selected_ids", "tiers"],
}


@dataclass
class LlmEvalLayer:
    """LLM 驱动的跨源全局重排层（Phase 4b Step E）。

    各源 eval_only 是单源视角打分；此处站在全量合并后的 chunk 面前，
    比较同 focus 下多源证据相对质量，可升降 tier，输出全局最优 top_k。

    同步实现（blocking）：retrieve_agent 通过 ``asyncio.to_thread`` 调用。
    任何异常（LLM 超时 / JSON 解析 / 空选 / 幻觉 chunk_id）→ fallback RuleEvalLayer。
    """
    # Keep deep-dive global evaluation on the same configured DeepSeek path as
    # source eval_only/plan_eval.  It must not implicitly require Vertex ADC.
    model: str = "deepseek/deepseek-v4-flash"
    timeout_s: float = 30.0
    # This layer improves final ordering only. After one transient retry,
    # deterministic RuleEvalLayer preserves already judged evidence instead of
    # spending another long provider round-trip on a non-critical optimization.
    max_retries: int = 2
    max_input_chunks: int = 40      # 超出时先过规则层截断
    rule_params: RuleEvalParams = field(default_factory=RuleEvalParams)
    # Request-local, body-free telemetry. RetrieveAgent appends it to the
    # operational trace after rank() returns.
    trace_events: List[Dict] = field(default_factory=list, init=False, repr=False)

    def rank(
        self,
        chunks: List[ChunkEntry],
        focuses: List[object],
        top_k: int,
    ) -> List[ChunkEntry]:
        self.trace_events.clear()
        if not chunks:
            return []

        rule_layer = RuleEvalLayer(params=self.rule_params)

        # 超长截断：规则层先粗筛，让 LLM 看精品子集
        if len(chunks) > self.max_input_chunks:
            candidates = rule_layer.rank(chunks, focuses, self.max_input_chunks)
            logger.info(
                "LlmEvalLayer: truncated %d→%d for LLM input",
                len(chunks), len(candidates),
            )
        else:
            candidates = list(chunks)

        self.trace_events.append({
            "event": "eval_layer_started", "stage": "final_eval",
            "mode": "llm_tool", "model": self.model,
            "input_chunk_count": len(chunks), "candidate_chunk_count": len(candidates),
            "top_k": top_k, "timeout_s": self.timeout_s,
        })
        try:
            selected = self._llm_rank(candidates, focuses, top_k)
        except Exception as e:
            self.trace_events.append({
                "event": "eval_layer_fallback", "stage": "final_eval",
                "reason": "llm_call_failed", "error_type": type(e).__name__,
                "fallback": "rule", "candidate_chunk_count": len(candidates),
            })
            logger.warning("LlmEvalLayer: LLM failed (%s) → fallback RuleEvalLayer", e)
            return rule_layer.rank(chunks, focuses, top_k)

        if not selected:
            self.trace_events.append({
                "event": "eval_layer_fallback", "stage": "final_eval",
                "reason": "empty_selection", "fallback": "rule",
                "candidate_chunk_count": len(candidates),
            })
            logger.warning("LlmEvalLayer: LLM returned 0 chunks → fallback RuleEvalLayer")
            return rule_layer.rank(chunks, focuses, top_k)

        logger.info(
            "LlmEvalLayer: selected %d/%d (cap=%d) chunks",
            len(selected), len(candidates), top_k,
        )
        self.trace_events.append({
            "event": "eval_layer_selected", "stage": "final_eval",
            "mode": "llm_tool", "candidate_chunk_count": len(candidates),
            "selected_chunk_count": len(selected), "top_k": top_k,
        })
        return selected

    def _llm_rank(
        self,
        candidates: List[ChunkEntry],
        focuses: List[object],
        top_k: int,
    ) -> List[ChunkEntry]:
        from tools.core.llm_client import call_llm, parse_json_response

        valid_fids = {getattr(f, "focus_id", "") for f in focuses}
        ref_to_id = {f"C{index}": chunk.id for index, chunk in enumerate(candidates, 1)}
        id_to_ref = {chunk_id: ref for ref, chunk_id in ref_to_id.items()}
        prompt = _build_llm_eval_layer_prompt(candidates, focuses, top_k, chunk_refs=id_to_ref)
        raw = call_llm(
            prompt,
            max_tokens=2048,
            timeout=self.timeout_s,
            _caller_tag="eval_layer.llm_tool",
            model=self.model,
            max_retries=self.max_retries,
            trace_callback=lambda item: self.trace_events.append({
                **dict(item), "event": "model_call_attempt", "stage": "final_eval",
            }),
            response_schema=_EVAL_LAYER_RESPONSE_SCHEMA,  # 结构强制（含 response_mime_type）
        )
        parsed = _normalize_eval_payload(parse_json_response(raw), top_k=top_k)

        selected_ids: List[str] = [
            ref_to_id[ref] for ref in (parsed.get("selected_ids") or [])
            if isinstance(ref, str) and ref in ref_to_id
        ]
        tiers_list: List[Dict] = []
        for assignment in (parsed.get("tiers") or []):
            if not isinstance(assignment, dict):
                continue
            ref = assignment.get("chunk_id")
            canonical_id = ref_to_id.get(ref) if isinstance(ref, str) else None
            if canonical_id:
                tiers_list.append({**assignment, "chunk_id": canonical_id})

        # tiers_map: chunk_id → full assignment dict（对齐 TierAssignment schema）
        tiers_map: Dict[str, Dict] = {}
        for t in tiers_list:
            cid = t.get("chunk_id")
            if not cid:
                continue
            tiers_map[cid] = {
                "tier": t.get("tier") if t.get("tier") in _VALID_TIERS else None,
                "relevance": float(t.get("relevance") or 0.5),
                "answered_focuses": [
                    fid for fid in (t.get("answered_focuses") or [])
                    if fid in valid_fids           # 白名单：过滤幻觉 focus_id
                ],
                "angle_tag": str(t.get("angle_tag") or "").strip().lower(),
            }

        # 组装结果，写回 tier / score / metadata（与 _assemble_top_chunks 对齐）
        id_to_chunk: Dict[str, ChunkEntry] = {c.id: c for c in candidates}
        result: List[ChunkEntry] = []
        for cid in selected_ids:
            c = id_to_chunk.get(cid)
            if c is None:
                logger.debug("LlmEvalLayer: hallucinated chunk_id %r, skip", cid)
                continue
            info = tiers_map.get(cid) or {}
            new_tier = info.get("tier")
            rel = info.get("relevance", c.score or 0.5)
            answered = info.get("answered_focuses") or []
            angle = info.get("angle_tag") or ""

            # tier 更新（与 _assemble_top_chunks 一致：用 with_tier 创建副本）
            if new_tier and new_tier != c.tier:
                c = c.with_tier(new_tier)

            # relevance 写回 score（eval_layer 公式 w_relevance=0.40 的输入）
            # metadata 写回 answered_focuses / angle_tag（final_assembler 依赖）
            updated_meta = dict(c.metadata or {})
            if rel != c.score:
                updated_meta["_eval_layer_relevance"] = rel   # 保留原 score，另存 EL 分
            if answered:
                updated_meta["answered_focuses"] = sorted(
                    set(updated_meta.get("answered_focuses") or []) | set(answered)
                )
            if angle:
                updated_meta["angle_tag"] = angle

            c = ChunkEntry(
                id=c.id,
                source=c.source,
                content=c.content,
                preview_content=c.preview_content,
                metadata=updated_meta,
                score=rel,          # 替换为 LLM 给出的连续 relevance 分
                sub_query=c.sub_query,
                target_focuses=set(c.target_focuses),
                tier=c.tier,
            )
            if c.tier not in {"critical", "important"}:
                logger.info("LlmEvalLayer: excluded selected non-evidence chunk %s tier=%s", c.id, c.tier)
                continue
            result.append(c)
            if len(result) >= top_k:
                break

        return result


def build_eval_layer(
    mode: str,
    params: Optional[RuleEvalParams] = None,
    model: Optional[str] = None,
) -> EvalLayer:
    """按 mode 返回 eval_layer 实例。

    - ``mode="rule"``: RuleEvalLayer（lite/balanced）
    - ``mode="llm_tool"``: LlmEvalLayer（deep_dive）；失败时 fallback 到规则层
    - ``model``: 覆盖 LlmEvalLayer 默认模型（None → 用 dataclass 默认值）
    """
    if mode == "llm_tool":
        kwargs: dict = dict(rule_params=params or RuleEvalParams())
        if model:
            kwargs["model"] = model
        return LlmEvalLayer(**kwargs)
    return build_rule_eval_layer(params)
