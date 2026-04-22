"""Global Query Planner (v2.1) — 1× LLM 调用，职责只剩「语义拆解 + 源路由」。

输出结构（v2.1，FG 下线）：``focuses[{text, targets}]``

**已删除**：sub_queries / tags / indexes / time_filter / required_targets / query_type /
focus_groups（FG 概念下线，直接 flat focuses）。
这些全部下放到各 SourceAgent 的 plan_only LLM 去做，保持职责单一。

## Q1 策略（v2.1）

- **数量约束**：``focuses ≤ MAX_FOCUSES_TOTAL (=6)``，简单 query 1-2，复杂 query 3-6
- **独立路由**：每个 focus 自带 ``targets``，由 LLM 根据 focus 语义决定
- **文本质量**：prompt 显式要求"可评估/不重叠"；代码侧做「规范化文本精确去重」兜底
- **没有 priority / cluster_id**：交给 Planner LLM 整体把握
"""

import json
import logging
from typing import List

from tools.llm_client import call_llm, parse_json_response
from .models import PlanFocus, QueryPlan
from .source_query_config import BRAND_ROUTING

logger = logging.getLogger(__name__)


# ── Q1 策略参数（可调） ─────────────────────────────────────────────
MAX_FOCUSES_TOTAL = 6
"""Focus 总数硬上限。prompt 告知 + 代码后置截断双重兜底。

算力放大链（假设 k=2/focus，bailian 2 index + 偶发 last_long）：
  6 × 1.5 targets × 2 sub_q × 2.6 bailian calls/sq ≈ 47 次，25s 超时下可控。
往上（8）接近超时边界，往下（4）又会让对称多实体拆分受限。
"""


# 源特性描述（仅供 planner 做路由决策，简短精准）
_SOURCE_PROFILES_BASE = {
    "feishu": "飞书文档：团队内部沉淀的运营材料、会议纪要、项目文档、表格数据。",
    "bailian": "百炼知识库：公司级结构化知识（产品、渠道、合规、市场情报等）向量检索。",
    "google": "Google Drive：跨团队协作文档（当前未启用检索）。",
    "notion": "Notion：外部合作/公共知识文档（当前未启用检索）。",
}

_FOCUS_GUIDE = """每个 focus 必须满足：
  1. **可评估**：给任一文档切片，能直接判断是否回答了该 focus（不是泛词如"销售数据"）
  2. **自包含**：带足上下文（实体 + 维度 + 可选限定），15-40 字为宜
  3. **不重叠**：focuses 之间语义边界清晰，避免同一事情改写两次

正例：
  - 'nubia 英国 2026Q1 DTC 渠道销售额与 GMV'
  - 'hypershell 德国市场的本地化运营策略和合规要点'
  - '项目 X 复盘会议的核心结论和下一步改进行动'

反例：
  - 'nubia'（太泛，无法评估）
  - '销售数据'（缺实体，每条切片都会命中）
  - '销售数据' + '销售表现'（语义重叠，等价重写）"""


def _build_source_profiles_block(available_sources: List[str]) -> str:
    return "\n".join(
        f"  - {s}: {_SOURCE_PROFILES_BASE.get(s, '(未知源)')}"
        for s in available_sources
    )


def _build_brand_routing_block(available_sources: List[str]) -> str:
    """feishu + bailian 同时可用时，提供品牌路由建议（仅建议，LLM 可自由决定）。"""
    if "feishu" not in available_sources or "bailian" not in available_sources:
        return ""
    lines = ["品牌路由建议（基于历史召回质量；LLM 最终自由裁量）："]
    for brand, routes in BRAND_ROUTING.items():
        keep = [r for r in routes if r in available_sources]
        lines.append(f"  - {brand}: {keep}")
    return "\n".join(lines)


# ── 文本质量兜底：规范化精确去重 ─────────────────────────────────
def _normalize_focus_text(text: str) -> str:
    """去除首尾空白 + 内部空白折叠 + 小写，用作精确去重的键。"""
    return " ".join(text.split()).lower()


def _dedupe_focuses_exact(focuses: List[PlanFocus]) -> List[PlanFocus]:
    """规范化文本完全一致即视为重复，保留前者。

    取代 Jaccard：LLM 在 prompt 硬约束下产生"字面近似"focus 的概率极低，
    真正的语义去重交由 Planner LLM 自身完成（prompt 里已要求"不重叠"）。
    这里只拦住最朴素的字面重复，保持 Occam's Razor。
    """
    kept: List[PlanFocus] = []
    seen: set = set()
    for f in focuses:
        key = _normalize_focus_text(f.text)
        if not key:
            continue
        if key in seen:
            logger.info("plan_query: drop exact-duplicate focus: %r", f.text)
            continue
        seen.add(key)
        kept.append(f)
    return kept


def _validate_plan(
    raw_focuses: List[dict],
    available_sources: List[str],
) -> List[PlanFocus]:
    """LLM 输出 → List[PlanFocus] 后置校验 & 兜底。

    约束（按优先级）：
    1. 字段完整性：text 非空，targets 非空
    2. targets ⊂ available_sources；取交集后为空则退回全量
    3. 总数 ≤ MAX_FOCUSES_TOTAL，超则截断并 warning
    4. 规范化文本精确去重（字面重复才丢）
    """
    out: List[PlanFocus] = []
    for item in raw_focuses:
        if not isinstance(item, dict):
            continue
        text = (item.get("text") or "").strip()
        if not text:
            continue
        raw_targets = item.get("targets") or []
        targets = [t for t in raw_targets if t in available_sources]
        if not targets:
            logger.info(
                "plan_query: focus %r has no valid targets in %s, broadcast to all",
                text, available_sources,
            )
            targets = list(available_sources)
        out.append(PlanFocus(text=text, targets=targets))

    out = _dedupe_focuses_exact(out)

    if len(out) > MAX_FOCUSES_TOTAL:
        logger.warning(
            "plan_query: LLM produced %d focuses, truncating to %d",
            len(out), MAX_FOCUSES_TOTAL,
        )
        out = out[:MAX_FOCUSES_TOTAL]

    return out


def plan_query(
    query: str,
    available_sources: List[str],
    session_context: str = "",
) -> QueryPlan:
    """把用户 query 拆成 flat focuses（每个 focus 独立带 targets）。

    v2.1：FG 概念下线，直接输出 ``focuses[{text, targets}]``。
    输出 ``QueryPlan`` 只含：``original_query`` + ``focuses``。
    """
    profiles_block = _build_source_profiles_block(available_sources)
    brand_block = _build_brand_routing_block(available_sources)

    prompt = f"""你是多源检索的「全局规划器」。职责只有两件事：
1. **语义拆解**：把用户 query 拆成若干个 flat focuses（可独立评估的检索维度）。
2. **源路由**：为每个 focus **独立**决定最合适的检索源（targets）。

用户 query：「{query}」
上下文：{session_context or "无"}

可用检索源：
{profiles_block}

{brand_block}

## Focus 质量要求

{_FOCUS_GUIDE}

## 数量约束（硬要求）

- focuses 总数 ≤ {MAX_FOCUSES_TOTAL}
- 简单 query（单实体单意图）：1-2 个 focus
- 复杂 query（多实体 / 多维度 / 对比类）：3-{MAX_FOCUSES_TOTAL} 个
- 判据：若两个候选 focus 可被同一条 sub_query 覆盖 → 合并为一个
- 多实体对比：尽量对称拆分（两侧维度一致、数量一致）
- 多实体独立问法（非对比）：各实体按实际被问到的内容独立拆分，无需强对称

## 源路由规则（每个 focus 独立判断）

- ``targets`` 从 {json.dumps(available_sources)} 中选，不能为空
- 判断依据：
  · 内部运营事实 / 表格数据 / SOP / 会议纪要 / 项目材料 → feishu
  · 跨文档概念 / 语义推理 / 品牌长文档 / 市场情报 → bailian
  · 两个源都能**独立**贡献有价值信息（非互为冗余）→ 同时路由
- 用户显式指定源（"在飞书里找"、"#bailian"）时，targets 只保留该源

## 输出（严格 JSON，不要额外说明）

{{
  "focuses": [
    {{"text": "具体可评估的 focus 描述...", "targets": ["feishu", "bailian"]}},
    {{"text": "另一个 focus 描述...", "targets": ["bailian"]}}
  ]
}}"""

    try:
        import os as _os
        _planner_model = _os.getenv("GEMINI_FALLBACK_MODEL") or None
        response_text = call_llm(
            prompt,
            max_tokens=1024,
            timeout=15.0,
            _caller_tag="query_planner",
            model=_planner_model,
        )
        result = parse_json_response(response_text)

        # 兼容兜底：若 LLM 仍旧按老 schema 输出 focus_groups，扁平化后再走校验
        raw_focuses: List[dict] = []
        if isinstance(result.get("focuses"), list):
            raw_focuses = list(result["focuses"])
        elif isinstance(result.get("focus_groups"), list):
            logger.info("plan_query: LLM returned legacy focus_groups, flattening")
            for fg in result["focus_groups"]:
                if not isinstance(fg, dict):
                    continue
                fg_targets = fg.get("targets") or []
                for text in fg.get("focuses") or []:
                    if isinstance(text, str) and text.strip():
                        raw_focuses.append({"text": text, "targets": fg_targets})

        focuses = _validate_plan(raw_focuses, available_sources)

        if not focuses:
            raise ValueError("No valid focuses in LLM response")

        plan = QueryPlan(original_query=query, focuses=focuses)

        logger.info("QueryPlan: focuses_total=%d", len(plan.focuses))
        for i, f in enumerate(plan.focuses):
            logger.info("  [focus_%d] targets=%s text=%s", i, f.targets, f.text)

        return plan

    except Exception as e:
        logger.warning("plan_query LLM failed, using fallback: %s", e)
        return QueryPlan(
            original_query=query,
            focuses=[PlanFocus(text=query, targets=list(available_sources))],
        )

