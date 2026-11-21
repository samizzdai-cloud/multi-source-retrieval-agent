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
from datetime import date
from typing import List, Optional

from tools.core.llm_client import call_llm, parse_json_response
from tools.core.models import PlanFocus, QueryPlan

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
    "google": "Google Workspace：产品上市规划&报告、上市复盘数据、营销媒体素材、产品系列详情。",
    "notion": "Notion：外部合作/公共知识文档（当前未启用检索）。",
}

try:
    from tools.core.source_query_config import ENTITY_FIDELITY_BLOCK as _ENTITY_FIDELITY_BLOCK
except Exception:
    _ENTITY_FIDELITY_BLOCK = ""


_FOCUS_GUIDE_TEMPLATE = """每个 focus 必须满足：
  1. **可评估**：给任一切片能直接判断是否命中（不能是 'nubia'/'销售数据' 这类泛词）
  2. **自包含**：实体 + 维度 + 可选限定，15-40 字
  3. **不重叠**：focuses 间语义边界清晰，禁止改写式重复
  4. **时间词具体化**：query 中出现"今年/本年/this year"等相对时间词时，必须替换为实际年份（= {current_year}）；
     "今年Q1" → "{current_year}Q1"，"今年" → "{current_year}年"。禁止在 focus 中保留"今年"等模糊词。

正例：'nubia 英国 {current_year}Q1 DTC 渠道销售额与 GMV'
反例：'销售数据' + '销售表现'（语义重叠）"""


def _build_focus_guide() -> str:
    return _FOCUS_GUIDE_TEMPLATE.format(current_year=date.today().year)


def _build_source_profiles_block(
    available_sources: List[str],
    bailian_allowed_indexes: Optional[List[str]] = None,
) -> str:
    lines = []
    for s in available_sources:
        desc = _SOURCE_PROFILES_BASE.get(s, '(未知源)')
        if s == "bailian" and bailian_allowed_indexes is not None:
            desc += f"（当前可用知识库项目：{', '.join(bailian_allowed_indexes)}）"
        lines.append(f"  - {s}: {desc}")
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


def _bailian_allowed_for_focus(
    text: str,
    bailian_allowed_indexes: Optional[List[str]],
) -> bool:
    """硬规则：focus 文本中包含 bailian 任一 allowed index 名称才允许路由到 bailian。

    不依赖 LLM 遵守 prompt，在代码层强制执行。
    bailian_allowed_indexes 为 None（未配置）时放行，交由 bailian 自己返回空结果。
    """
    if bailian_allowed_indexes is None:
        return True
    text_lower = text.lower()
    return any(idx.lower() in text_lower for idx in bailian_allowed_indexes)


def _validate_plan(
    raw_focuses: List[dict],
    available_sources: List[str],
    bailian_allowed_indexes: Optional[List[str]] = None,
) -> List[PlanFocus]:
    """LLM 输出 → List[PlanFocus] 后置校验 & 兜底。

    约束（按优先级）：
    1. 字段完整性：text 非空，targets 非空
    2. targets ⊂ available_sources；取交集后为空则退回全量
    3. bailian 硬过滤：focus 文本不含 allowed index 名称时强制移除 bailian
    4. 总数 ≤ MAX_FOCUSES_TOTAL，超则截断并 warning
    5. 规范化文本精确去重（字面重复才丢）
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

        # 硬过滤：bailian 仅在 focus 文本命中 allowed index 名称时保留
        if "bailian" in targets and not _bailian_allowed_for_focus(text, bailian_allowed_indexes):
            targets = [t for t in targets if t != "bailian"]
            logger.info("plan_query: stripped bailian from focus %r (entity not in allowed indexes)", text)
            if not targets:
                targets = [t for t in available_sources if t != "bailian"] or list(available_sources)

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
    bailian_allowed_indexes: Optional[List[str]] = None,
    session_context: str = "",
) -> QueryPlan:
    """把用户 query 拆成 flat focuses（每个 focus 独立带 targets）。

    v2.1：FG 概念下线，直接输出 ``focuses[{text, targets}]``。
    输出 ``QueryPlan`` 只含：``original_query`` + ``focuses``。
    """
    profiles_block = _build_source_profiles_block(
        available_sources,
        bailian_allowed_indexes=bailian_allowed_indexes,
    )

    current_date_str = date.today().isoformat()
    prompt = f"""你是多源检索的「全局规划器」。职责只有两件事：
1. **语义拆解**：把用户 query 拆成若干个 flat focuses（可独立评估的检索维度）。
2. **源路由**：为每个 focus **独立**决定最合适的检索源（targets）。

用户 query：「{query}」
当前日期：{current_date_str}（"今年" = {date.today().year} 年，请在 focus 中用实际年份替换相对时间词）
上下文：{session_context or "无"}

可用检索源（包含了当前环境动态解析出的可访问清单）：
{profiles_block}

## Focus 质量要求

{_build_focus_guide()}

{_ENTITY_FIDELITY_BLOCK}

## 数量约束（硬要求）

- focuses 总数 ≤ {MAX_FOCUSES_TOTAL}（简单单实体单意图 1-2 个；多实体/多维度/对比 3-{MAX_FOCUSES_TOTAL} 个）
- 若两个候选 focus 可被同一条 sub_query 覆盖 → 合并
- 多实体对比尽量对称拆分；非对比的独立问法按各实体实际问到的内容拆分

## 源路由规则（每个 focus 独立判断）

- ``targets`` 从 {json.dumps(available_sources)} 中选，不能为空
- 判断依据：
  · 内部运营事实 / 结构化表格 / SOP / 会议纪要 / 协作材料 → `feishu`
  · query 中的**核心实体/品牌/项目名**必须在 `bailian` 可用知识库列表中有对应项目，才可路由到 `bailian`；
  · 产品上市规划&报告、上市复盘数据、营销媒体素材 / 产品系列详情 → `google`
  · 多个源能**独立**贡献非冗余信息时 → 同时路由（如 `["feishu", "bailian"，“google”]`）
- 用户显式指定源（如 "在飞书里找"、"#bailian"、"搜一下Drive"）时，targets 仅保留指定源（最高优先级）。

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
            use_provider_output_limit=True,
            timeout=15.0,
            _caller_tag="query_planner",
            model=_planner_model,
        )
        result = parse_json_response(response_text)

        raw_focuses: List[dict] = []
        if isinstance(result.get("focuses"), list):
            raw_focuses = list(result["focuses"])

        focuses = _validate_plan(raw_focuses, available_sources, bailian_allowed_indexes)

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

