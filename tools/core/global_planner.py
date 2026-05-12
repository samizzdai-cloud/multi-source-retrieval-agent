"""Global Planner v2.2 — 一次 LLM 调用直出 per-source sub_queries。

与 v2.1 (``tools.core.query_planner``) 的差异：
  · v2.1 只产 ``focuses[{text, targets}]``，每源各自再跑 plan_only LLM
  · v2.2 一次调用产 ``focuses[{focus_id, focus_content, sub_queries[{target_source, query, tags, indexes, time_filter, query_type, author_filter}]}]``
    各源 plan_only LLM 不再默认参与（仅在 runner 标记 ``replan_needed`` 时局部兜底）

设计要点：
  · 动态 instructions 装配：仅注入 ``enabled_sources`` 的源规则；未启用源一字不见
  · 模型走 ``RetrieveConfig.global_planner.model``，不读 env
  · 失败检测见 ``PlannerFailure``——单点故障由 retrieve_agent 决定是否回落 v1
  · 输出瘦身：planner 不产生 sub_query_id / target_focus / google_enable_fulltext /
    google_order_by（这些由 planner_runner 翻译时分配 / 推断）
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional

from tools.core.llm_client import call_llm, parse_json_response

# 结构化输出 schema：强制 JSON 输出，消除 markdown 包装 + 缩短生成 token 数
_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "focuses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "focus_id":      {"type": "string"},
                    "focus_content": {"type": "string"},
                    "sub_queries": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "target_source": {"type": "string"},
                                "query":         {"type": "string"},
                                "tags":          {"type": "array", "items": {"type": "string"}},
                                "indexes":       {"type": "array", "items": {"type": "string"}},
                                "time_filter":   {"type": "string"},
                                "query_type":    {"type": "string"},
                                "author_filter": {"type": "string"},
                            },
                            "required": ["target_source", "query"],
                        },
                    },
                },
                "required": ["focus_id", "focus_content", "sub_queries"],
            },
        },
    },
    "required": ["focuses"],
}
from tools.core.models import (
    BailianIndexCatalog,
    GlobalPlan,
    GlobalPlannerConfig,
    PlannedFocus,
    PlannedSubQuery,
)
from tools.core.source_query_config import (
    BAILIAN_L2_TAGS,
    COMMON_SUB_QUERY_HINTS,
    COUNTRY_ABBREV_RULE,
    ENTITY_FIDELITY_BLOCK,
    QUERY_TYPE_DESCRIPTION,
    get_source_query_rules,
)

logger = logging.getLogger(__name__)


# 源 profile —— 仅供 planner 路由决策
_SOURCE_PROFILES_BASE = {
    "feishu": "飞书文档：团队内部沉淀的运营材料、会议纪要、项目文档、表格数据。",
    "bailian": "百炼知识库：公司级结构化知识（产品、渠道、合规、市场情报等）向量检索。",
    "google": "Google Workspace：产品上市规划&报告、上市复盘数据、营销媒体素材、产品系列详情。",
    "notion": "Notion：外部合作/公共知识文档（当前未启用检索）。",
}


# ─────────────────────────────────────────────────────────────────────
# 失败类型 —— 供 retrieve_agent 决定是否回落 v1
# ─────────────────────────────────────────────────────────────────────
@dataclass
class PlannerFailure(Exception):
    """global_planner 整盘故障；retrieve_agent 接住后按 ``fallback_to_v1`` 决策。"""

    reason: str  # "json_parse" | "empty_focuses" | "schema" | "timeout" | "api_error"
    detail: str = ""

    def __str__(self) -> str:
        return f"PlannerFailure(reason={self.reason}, detail={self.detail[:120]})"


# ─────────────────────────────────────────────────────────────────────
# Instructions 动态装配
# ─────────────────────────────────────────────────────────────────────
def _build_source_profiles_block(
    enabled_sources: List[str],
    bailian_catalog: Optional[BailianIndexCatalog],
) -> str:
    lines: List[str] = []
    for s in enabled_sources:
        desc = _SOURCE_PROFILES_BASE.get(s, "(未知源)")
        if s == "bailian" and bailian_catalog and bailian_catalog.allowed:
            desc += f"（当前可用 symbolic indexes：{', '.join(bailian_catalog.allowed)}）"
        lines.append(f"  - {s}: {desc}")
    return "\n".join(lines)


def _build_per_source_rules_block(
    enabled_sources: List[str],
    bailian_catalog: Optional[BailianIndexCatalog],
) -> str:
    """仅装配启用源的规则；未启用源一字不写。

    共享规则（COMMON_SUB_QUERY_HINTS / QUERY_TYPE_DESCRIPTION）由 _build_prompt 顶部
    单独注入，这里调 ``include_common=False`` 拿纯 per-source 特有部分以免重复。
    """
    parts: List[str] = []
    for src in enabled_sources:
        body = get_source_query_rules(src, include_common=False) or ""
        parts.append(f"### [{src}] sub_query 编写规则\n{body}".rstrip())
        if src == "bailian":
            allowed = (bailian_catalog.allowed if bailian_catalog else []) or []
            parts.append(
                "百炼可用 L2 业务分类枚举（tags 必须从此选 ≤1 个）：" + ", ".join(BAILIAN_L2_TAGS)
            )
            parts.append(
                "百炼允许的 symbolic indexes（必须从中选；planner 永远使用 symbolic 名）："
                + (json.dumps(allowed, ensure_ascii=False) if allowed else "[]（未限制）")
            )
            parts.append(COUNTRY_ABBREV_RULE)
    return "\n\n".join(parts)


def _build_output_schema_block(enabled_sources: List[str]) -> str:
    """输出契约（严格 JSON 结构）。"""
    sources_repr = json.dumps(enabled_sources, ensure_ascii=False)
    return f"""## 输出契约（严格 JSON；不要 markdown / 不要解释 / 不要 ```代码块）

{{
  "focuses": [
    {{
      "focus_id": "focus_0",                          // 必须形如 focus_0/focus_1 顺序编号
      "focus_content": "可独立评估的 focus 文本，15-40 字，含实体+维度+可选限定",
      "sub_queries": [
        {{
          "target_source": "<{sources_repr} 之一>",   // 越权值会被 runner 丢弃
          "query": "投递到该源的检索串（遵守该源专属规则）",
          "tags": ["仅 bailian 用 L2+时效；其它源 []"],
          "indexes": ["仅 bailian 用 symbolic 名；其它源 []"],
          "time_filter": "feishu/google 可用；bailian 恒 null",
          "query_type": "feishu/google：structured|concept|personal_recent；bailian/notion null",
          "author_filter": "feishu/google：'me' 或 null"
        }}
      ]
    }}
  ]
}}

禁止输出 sub_query_id / target_focus / google_enable_fulltext / google_order_by
（运行时字段，由 runner 自动分配/推断）。"""


def _build_prompt(
    query: str,
    enabled_sources: List[str],
    cfg: GlobalPlannerConfig,
    bailian_catalog: Optional[BailianIndexCatalog],
    session_context: str = "",
) -> str:
    today = date.today()
    now_str = today.isoformat()
    profiles_block = _build_source_profiles_block(enabled_sources, bailian_catalog)
    rules_block = _build_per_source_rules_block(enabled_sources, bailian_catalog)
    output_block = _build_output_schema_block(enabled_sources)

    # 共享段：feishu/google 都用到的时间推断 / 身份归属 / query_type 三段统一注入一次。
    # 仅当 enabled_sources 含 feishu 或 google 时才注入，避免给 bailian-only 调用读冗余。
    needs_common = any(s in ("feishu", "google") for s in enabled_sources)
    common_block = ""
    if needs_common:
        common_block = (
            "## 共享 sub_query 规则（feishu / google 通用）\n\n"
            + COMMON_SUB_QUERY_HINTS.format(now_str=now_str)
            + "\n\n"
            + QUERY_TYPE_DESCRIPTION
            + "\n"
        )

    return f"""你是多源检索系统的「全局规划器」(v2.2)。一次性完成：
  1. **语义拆解**：把 user query 拆成 ≤{cfg.max_focuses} 个可独立评估的 focus
  2. **源路由 + 子查询定制**：为每个 focus 直接产出投递到指定源的 sub_queries
     （含全部源专业字段，下游不再为你做二次规划）

user_query：「{query}」
current_date：{now_str}（"今年" = {today.year} 年；focus 中的"今年/本年/this year" 必须替换为实际年份）
session_context：{session_context or "无"}

## 可用检索源

{profiles_block}

## 实体保真（拒识自创）

{ENTITY_FIDELITY_BLOCK}

{common_block}## 各源 sub_query 专属规则（在共享规则之上叠加）

{rules_block}

## Focus 质量要求

  1. **可评估**：给任一切片能直接判断是否命中（不能是 'nubia'/'销售数据' 这类泛词）
  2. **自包含**：实体 + 维度 + 可选限定，15-40 字
  3. **不重叠**：focuses 间语义边界清晰

## 数量约束（硬要求）

  · focuses 总数 ≤ {cfg.max_focuses}（简单 1-2 / 复杂 3-{cfg.max_focuses}）
  · 各源 sub_queries 数量遵从其专属规则的预算（google 有明确预算；feishu/bailian
    无显式上限但保持少而精）
  · 若两个候选 focus 可被同一条 sub_query 覆盖 → 合并

{output_block}
"""


# ─────────────────────────────────────────────────────────────────────
# 解析 / 校验
# ─────────────────────────────────────────────────────────────────────
def _parse_planned_sub_query(item: Any, enabled_set: set) -> Optional[PlannedSubQuery]:
    if not isinstance(item, dict):
        return None
    target_source = str(item.get("target_source") or "").strip().lower()
    if target_source not in enabled_set:
        return None
    query = str(item.get("query") or "").strip()
    if not query:
        return None

    def _str_list(v: Any) -> List[str]:
        if not isinstance(v, list):
            return []
        return [str(x).strip() for x in v if isinstance(x, (str, int)) and str(x).strip()]

    return PlannedSubQuery(
        target_source=target_source,
        query=query,
        tags=_str_list(item.get("tags")),
        indexes=_str_list(item.get("indexes")),
        time_filter=(str(item.get("time_filter")).strip() if item.get("time_filter") else None),
        query_type=(str(item.get("query_type")).strip().lower() if item.get("query_type") else None),
        author_filter=(str(item.get("author_filter")).strip().lower() if item.get("author_filter") else None),
    )


def _parse_global_plan(
    raw: Any,
    original_query: str,
    enabled_sources: List[str],
    max_focuses: int,
) -> GlobalPlan:
    if not isinstance(raw, dict):
        raise PlannerFailure(reason="schema", detail=f"top-level not dict: {type(raw).__name__}")
    focuses_raw = raw.get("focuses")
    if not isinstance(focuses_raw, list) or not focuses_raw:
        raise PlannerFailure(reason="empty_focuses", detail="missing/empty 'focuses'")

    enabled_set = set(enabled_sources)
    out: List[PlannedFocus] = []
    for idx, item in enumerate(focuses_raw[:max_focuses]):
        if not isinstance(item, dict):
            continue
        focus_id = str(item.get("focus_id") or f"focus_{idx}").strip() or f"focus_{idx}"
        focus_content = str(item.get("focus_content") or "").strip()
        if not focus_content:
            continue
        sqs_raw = item.get("sub_queries") or []
        sub_queries: List[PlannedSubQuery] = []
        for sq_item in sqs_raw:
            psq = _parse_planned_sub_query(sq_item, enabled_set)
            if psq is not None:
                sub_queries.append(psq)
        out.append(
            PlannedFocus(focus_id=focus_id, focus_content=focus_content, sub_queries=sub_queries)
        )

    if not out:
        raise PlannerFailure(reason="empty_focuses", detail="all focuses dropped post-validate")

    return GlobalPlan(original_query=original_query, focuses=out)


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def plan_global(
    query: str,
    enabled_sources: List[str],
    cfg: GlobalPlannerConfig,
    bailian_catalog: Optional[BailianIndexCatalog] = None,
    session_context: str = "",
) -> GlobalPlan:
    """一次 LLM 调用产 ``GlobalPlan``。失败抛 ``PlannerFailure``。

    Args:
        query: 用户原始查询
        enabled_sources: 启用的源（动态规则装配 + planner 输出白名单都基于此）
        cfg: ``RetrieveConfig.global_planner`` 实例（含 model / timeout / max_focuses）
        bailian_catalog: bailian symbolic index 集合；为 None 时 prompt 内显示"未限制"

    Raises:
        PlannerFailure: 整盘故障；retrieve_agent 接住按 fallback_to_v1 决定下一步
    """
    if not enabled_sources:
        raise PlannerFailure(reason="schema", detail="enabled_sources is empty")

    prompt = _build_prompt(
        query=query,
        enabled_sources=enabled_sources,
        cfg=cfg,
        bailian_catalog=bailian_catalog,
        session_context=session_context,
    )

    started = time.perf_counter()
    try:
        raw_text = call_llm(
            prompt,
            max_tokens=2048,
            timeout=cfg.timeout_s,
            _caller_tag="global_planner_v2",
            model=cfg.model,
            response_schema=_RESPONSE_SCHEMA,
        )
    except Exception as e:
        elapsed = (time.perf_counter() - started) * 1000.0
        msg = str(e)
        # 区分超时 / API 错误（仅用于日志，retrieve_agent 不区分）
        kind = "timeout" if "timeout" in msg.lower() or "deadline" in msg.lower() else "api_error"
        logger.warning(
            "TIMING global_planner model=%s status=failed reason=%s ms=%.1f detail=%s",
            cfg.model, kind, elapsed, msg[:160],
        )
        raise PlannerFailure(reason=kind, detail=msg) from e

    try:
        raw_obj = parse_json_response(raw_text)
    except Exception as e:
        elapsed = (time.perf_counter() - started) * 1000.0
        logger.warning(
            "TIMING global_planner model=%s status=failed reason=json_parse ms=%.1f raw=%r",
            cfg.model, elapsed, raw_text[:200],
        )
        raise PlannerFailure(reason="json_parse", detail=str(e)) from e

    plan = _parse_global_plan(
        raw_obj,
        original_query=query,
        enabled_sources=enabled_sources,
        max_focuses=cfg.max_focuses,
    )

    elapsed = (time.perf_counter() - started) * 1000.0
    total_sq = sum(len(f.sub_queries) for f in plan.focuses)
    logger.info(
        "TIMING global_planner model=%s status=ok ms=%.1f focuses=%d sub_queries=%d",
        cfg.model, elapsed, len(plan.focuses), total_sq,
    )
    for i, f in enumerate(plan.focuses):
        per_src = {}
        for sq in f.sub_queries:
            per_src[sq.target_source] = per_src.get(sq.target_source, 0) + 1
        logger.info(
            "  [%s] content=%r sq_by_source=%s",
            f.focus_id, f.focus_content[:60], per_src,
        )

    return plan
