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
import re
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
    "feishu": "标题/全文关键词搜索；命中后可读取 Doc/Wiki/Sheet/Bitable，支持 time/author filter。",
    "bailian": "知识库检索；支持 symbolic index 与 tag filter。",
    "google": "Drive 全文搜索；命中后可读取 Docs/Sheets/Slides/Gmail。",
    "notion": "页面关键词检索。",
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
    bailian_fields = (
        '          "tags": ["百炼 L2/时效过滤；仅 target_source=bailian 时输出"],\n'
        '          "indexes": ["百炼 symbolic index；仅 target_source=bailian 时输出"],\n'
        if "bailian" in enabled_sources else ""
    )
    return f"""## 输出契约（严格 JSON；不要 markdown / 不要解释 / 不要 ```代码块）

{{
  "focuses": [
    {{
      "focus_id": "focus_0",                          // 必须形如 focus_0/focus_1 顺序编号
      "focus_content": "一个可独立验收的回答子任务：保留用户实体/限定，并说明要形成什么判断或输出",
      "sub_queries": [
        {{
          "target_source": "<{sources_repr} 之一>",   // 越权值会被 runner 丢弃
           "query": "为该 focus 取得一类证据的 API 检索串",
{bailian_fields}
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
    profile_context: str = "",
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

    diagnostic_coverage_rule = (
        "For a broad GTM conversion-diagnosis request that does not limit one stage, "
        "the answer-task focus and its distinct evidence queries must cover verifiable "
        "acquisition/channel, paid efficiency, and on-site funnel or page-friction evidence. "
        "These are evidence angles, not invented business entities.\n\n"
    )
    return diagnostic_coverage_rule + f"""你是多源检索系统的全局规划器。输出严格 JSON，不要解释。

核心模型：focus 是回答 user query 的一个可独立验收的子任务，描述理想答案必须形成的判断、结论或结构化输出；它不是文档主题、定义词或单条检索词。sub_query 是为同一个 focus 取得不同证据的 API 请求；一个 focus 可以有多条 query，但它们必须角度不同且不可只是同义改写。

user_query：「{query}」
current_date：{now_str}（"今年" = {today.year} 年；focus 中的"今年/本年/this year" 必须替换为实际年份）
session_context：{session_context or "无"}

## Profile query vocabulary（仅候选词，不是证据）

{profile_context or "无"}

## 可用检索源

{profiles_block}

## 实体保真（拒识自创）

{ENTITY_FIDELITY_BLOCK}

{common_block}## 各源 sub_query 专属规则（在共享规则之上叠加）

{rules_block}

## Focus 质量要求

  1. 诊断/原因/排查问题：focus 必须保留诊断目标与结果，例如“定位 CVR 低的漏斗、流量和页面摩擦证据”；禁止改写为“CVR 定义”或泛泛“优化策略”。
     query 应面向会出现在证据中的结果词/规范指标词：可保留原缩写，同时补一个必要的规范指标或漏斗表达（如“转化率”“渠道漏斗”“站点漏斗”），
     不是无边界同义词扩展，也不能加入未出现的业务实体。
  2. 预测/判断问题：focus 必须保留对象、目标时间和结果指标，并区分现有诊断证据与缺失的上游预测输入。
  3. 指定表格/渠道输出问题：一个 focus 覆盖完整输出任务；排列顺序和列字段属于答案格式，不要按每个渠道拆成 focus。
  4. user_query 的实体、缩写、指标、地区、日期、产品状态（如新品/老品）和诊断/预测词必须原样留在相关 focus 或 query；不得引入任何示例品牌或业务实体。
  5. 预测、判断、估算这类问题通常只能有 **一个** focus：该 focus 是要形成的最终预测/判断；
     销售现状、历史趋势、库存、渠道明细、预测模型是这个 focus 的不同证据面，必须作为同一 focus 下
     角度不同的 query，而不是拆成多个“获取数据”的 focus。只有两个结论能被用户独立验收时才可拆 focus。

## 数量约束（硬要求）

  · focuses 总数 ≤ {cfg.max_focuses}；简单任务通常 1 个，只有答案需要不同、可独立验收的结论时才拆分。
  · 同一 focus + source 可有多条不同证据角度的 sub_query；禁止只因别名、
    大小写、翻译或同义表达不同而重复检索。别名/表达替换交由零结果 fallback。
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


_DIAGNOSTIC_TERMS = ("排查", "原因", "异常", "下降", "下滑", "低")
_FORECAST_TERMS = ("预测", "判断", "预估")
_LITERAL_TERMS = ("新品", "老品", "UTM", "联盟", "GMV", "CVR", "ROAS", "CPA")
_REGIONS = ("新加坡", "日本", "美国", "英国", "德国", "法国", "欧洲", "中国")


def _semantic_contract_warnings(
    original_query: str, focuses: List[PlannedFocus],
) -> List[str]:
    """Return plan-level anchor warnings without deleting executable work.

    Anchors are expected across the union of focuses and queries.  Requiring
    every focus to repeat every global entity/date/diagnostic term caused valid
    complementary evidence angles to be discarded and produced empty dispatch.
    """
    combined = " ".join(
        [focus.focus_content for focus in focuses]
        + [sq.query for focus in focuses for sq in focus.sub_queries]
    ).casefold()
    warnings: List[str] = []
    for token in re.findall(r"\b[A-Z][A-Z0-9+._-]{1,}\b", original_query):
        if token.casefold() not in combined:
            warnings.append(f"missing_literal:{token}")
    for token in _LITERAL_TERMS + _REGIONS:
        if token in original_query and token.casefold() not in combined:
            warnings.append(f"missing_literal:{token}")
    if any(term in original_query for term in _DIAGNOSTIC_TERMS):
        if not any(term in combined for term in _DIAGNOSTIC_TERMS):
            warnings.append("missing_diagnostic_intent")
    if any(term in original_query for term in _FORECAST_TERMS):
        if not any(term in combined for term in _FORECAST_TERMS):
            warnings.append("missing_forecast_intent")
    compact_combined = re.sub(r"\s+", "", combined)
    for token in re.findall(r"\d{1,2}[./-]\d{1,2}|\d{4}[-/]\d{1,2}|\d+月|Q[1-4]", original_query, re.I):
        if re.sub(r"\s+", "", token.casefold()) not in compact_combined:
            warnings.append(f"missing_period:{token}")
    return list(dict.fromkeys(warnings))


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
        seen_source_queries: set[tuple[str, str]] = set()
        for sq_item in sqs_raw:
            psq = _parse_planned_sub_query(sq_item, enabled_set)
            key = (psq.target_source, re.sub(r"\s+", " ", psq.query).strip().casefold()) if psq is not None else None
            if psq is not None and key not in seen_source_queries:
                sub_queries.append(psq)
                seen_source_queries.add(key)
        out.append(
            PlannedFocus(focus_id=focus_id, focus_content=focus_content, sub_queries=sub_queries)
        )

    if not out:
        raise PlannerFailure(reason="empty_focuses", detail="all focuses dropped post-validate")

    warnings = _semantic_contract_warnings(original_query, out)
    if warnings:
        logger.warning("global_planner plan-level anchor warnings=%s", warnings)
    return GlobalPlan(
        original_query=original_query,
        focuses=out,
        validation_warnings=warnings,
    )


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def plan_global(
    query: str,
    enabled_sources: List[str],
    cfg: GlobalPlannerConfig,
    bailian_catalog: Optional[BailianIndexCatalog] = None,
    session_context: str = "",
    profile_context: str = "",
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
        profile_context=profile_context,
    )

    started = time.perf_counter()
    try:
        # DeepSeek thinking 配置：从 cfg 取，转为 litellm kwargs
        tc_kwargs = None
        if cfg.thinking_config is not None:
            tc_kwargs = cfg.thinking_config.to_litellm_kwargs()

        raw_text = call_llm(
            prompt,
            max_tokens=2048,
            timeout=cfg.timeout_s,
            _caller_tag="global_planner_v2",
            model=cfg.model,
            thinking_config=tc_kwargs,
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
