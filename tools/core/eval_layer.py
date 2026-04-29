"""eval_layer——跨源合并后的全局重排与截断（Phase 4b Step C）。

职责：
  - 输入：merge 后的 ``List[ChunkEntry]``（已经由各 SourceAgent 内部的 eval_only
    或 plan_eval 打过 tier / relevance / answered_focuses）
  - 输出：按全局公式重排 + min-keep 截断 + focus 补齐的 ``List[ChunkEntry]``

设计原则（spec §4b / 用户拍板）：
  - **放弃** retrieve 原生 score（已在 _assemble_top_chunks 被 LLM relevance 覆盖）
  - **复用** LLM 判断：tier（粗分类）+ relevance（[0,1] 连续分）+ answered_focuses
  - **不再** 在 eval_layer 调 LLM（RuleEvalLayer 零成本；LlmEvalLayer 留 Step E）

公式：
  final = 0.55 * TIER_W[tier] + 0.30 * relevance + 0.15 * coverage_bonus
  TIER_W = {critical: 1.0, important: 0.6, noise: 0.1, None: 0.3}
  coverage_bonus = min(1.0, max(0, len(answered_focuses) - 1) * 0.25)

截断（min-keep）：
  target_k = min(eval_layer_top_k, n_focus * 3)
  cap_lo = ceil(n_total * 0.25)
  cap_hi = floor(n_total * 0.80)
  effective_k = max(cap_lo, min(cap_hi, target_k))

focus 补齐：
  取完 top_effective_k 后检查每个 focus 是否至少有 1 条命中（tier ∈ {critical, important}）。
  未命中的 focus → 从剩余池按 final 降序补 1 条（优先 answered_focuses 含该 focus 的）。
  补齐产物仍受 cap_hi 限制。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
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


def build_eval_layer(mode: str, params: Optional[RuleEvalParams] = None) -> EvalLayer:
    """按 mode 返回 eval_layer 实例。

    - mode="rule": RuleEvalLayer
    - mode="llm_tool": 未实现（Phase 4b Step E），当前回退 RuleEvalLayer
    """
    if mode == "llm_tool":
        logger.warning("eval_layer mode=llm_tool not implemented, fallback to rule")
    return build_rule_eval_layer(params)
