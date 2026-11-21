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
from dataclasses import dataclass, replace
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
                                "query":         {"type": "string", "description": "把所属 Focus 适配为目标 source 的 Search/MCP API 请求，遵循该源的查询优化规则与预算，定位目标 chunks 所在材料。"},
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
      "focus_content": "研究任务描述；【必须项】完成该任务必需的信息与证据；【可选补充】有价值但不阻塞完成的信息（没有则省略）",
      "sub_queries": [
        {{
          "target_source": "<{sources_repr} 之一>",   // 越权值会被 runner 丢弃
           "query": "将所属 Focus 适配为目标 source 的 Search/MCP API 请求，定位目标 chunks 所在材料",
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


def _focus_limit_for_sources(
    enabled_sources: List[str],
    configured_max_focuses: int,
    preset: str = "balanced",
) -> int:
    """Return the bounded retrieval-task budget for this planner invocation.

    Global capacity is bounded by source capacity and the configured ceiling.
    A shared Focus counts once globally and once for each assigned source.
    """
    active_sources = len({str(s).strip().lower() for s in enabled_sources if str(s).strip()})
    configured_cap = max(1, int(configured_max_focuses))
    if preset in {"balanced", "deep_dive"}:
        return min(4 * max(1, active_sources), configured_cap)
    return min(4, active_sources + 1, configured_cap)


def _build_prompt(
    query: str,
    enabled_sources: List[str],
    cfg: GlobalPlannerConfig,
    bailian_catalog: Optional[BailianIndexCatalog],
    session_context: str = "",
    profile_context: str = "",
    preset: str = "balanced",
) -> str:
    today = date.today()
    now_str = today.isoformat()
    profiles_block = _build_source_profiles_block(enabled_sources, bailian_catalog)
    rules_block = _build_per_source_rules_block(enabled_sources, bailian_catalog)
    output_block = _build_output_schema_block(enabled_sources)
    focus_limit = _focus_limit_for_sources(enabled_sources, cfg.max_focuses, preset)
    query_limit = 2 if preset in {"balanced", "deep_dive"} else 3

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

    source_focus_budget = "每个 source 最多承接 4 个 Focus（共享 Focus 在每个承接源各计一次）" if preset in {"balanced", "deep_dive"} else "各源承接数不超过全局 Focus 上限"
    return f"""你是多源检索系统的全局规划器。输出严格 JSON，不要解释。

## Focus 与 Subquery 的职责
- user_query 是用户原始业务诉求，不能因平台检索方式而改变。
- Focus 是围绕原始诉求、可独立验收的研究任务：说明要查明什么信息、需要什么证据、支持什么判断，并保留对象、范围、时间与任务间的承接关系。
- 先从原始诉求确定必须完成的信息需求，只为这些需求创建 Focus。仅用于丰富答案的背景、既有做法、历史效果或参考方法，应作为相关 Focus 的可选补充，不单独创建必查任务。
- 可按研究方向、逻辑维度、业务、品牌或产品解耦为多个 Focus。拆分依据是独立的信息需求与验收维度，不是词的数量、搜索表达数量或平台数量；不要为凑满预算而拆分，也不要机械按每个指标拆分。
- 同一个 Focus 可以由多个 source 共享，其业务含义保持稳定；各源分别生成适配自身 Search API/MCP 的 Subquery。
- Subquery 是属于一个 Focus、一个 source 的可执行请求（query 及必要参数）。先理解能支撑该 Focus 的目标内容会表达什么，再结合平台的关键词/语义检索能力进行转译，而不是摘抄 Focus 或预先编造答案。
- 同一 Focus 下的多条 Subquery 可以寻找同类证据，只要检索表达或路径有实际召回价值。每条保留明确归属；实际召回的同一文档或 chunk 可以支持多个 Focus，由后续评估确认。

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
  2. 预测/判断问题：focus 必须保留对象、目标时间和结果指标，并区分现有诊断证据与缺失的上游预测输入。
  3. user_query 的实体、缩写、指标、地区、日期、产品状态（如新品/老品）和诊断/预测词必须原样留在相关 focus 或 query；不得引入任何示例品牌或业务实体。
  4. 对复合诊断或建议问题，区分当前状态核实和改进依据等可独立验收的检索任务，比如一个复杂问题的不同逻辑|方面|对象|产品等维度去独立验收。
     一个 Focus 若需要超过 2个query以上才能覆盖多个独立任务，应拆分，保留它们的对象、时间与承接关系。
     这里的前提是包含多个独立任务；仅为同一任务更换检索表达，不因此拆 Focus。
  5. 请求新建议时，核实用户所需的业务现状和约束，由 AnswerAgent 综合形成建议。已有策略、历史复盘、实验成效与参考方法默认为可选补充，只有用户明确要求查找这些内容时才作为必须项；不得将“提出建议”改写为“必须找到已有建议或历史效果”。
  6. 在每个 focus_content 中显式用【必须项】突出验收条件：来自用户明确要求，或完成其诉求不可缺少的信息；不是列举所有可能相关的指标、报表或分析方法。
     有价值但不影响完成任务的内容，用【可选补充】单独标注（无则省略）。不要把自行联想到的明细、工具或验证手段升级为必须项，也不要把用户明确要求降为可选。必须项已满足时，不应因缺少可选补充而继续检索。
     输出前逐项核对必须项与原始诉求的必要关系：缺少它是否确实无法完成用户任务？仅能让建议更丰富的历史实验、复盘、画像或逐环节诊断不是自动必需，不能因为资料可能存在就创建独立必查任务。

## 数量约束（硬要求）

  · focuses 总数 ≤ {focus_limit}（按 preset 与配置上限计算）；这是上限，按实际可独立验收的任务分配。
  · 同一 focus + source 的 sub_query 用于优化该源的 API 召回，去除完全重复请求，不要求每条承担不同业务任务。
  · 同一 focus + source 每轮最多 {query_limit} 条 sub_query，同时遵从更小的源专属预算。
  · {source_focus_budget}；全局总数和源级承接数分别计数。
  · 若两个候选 Focus 的研究目标与验收要求相同则合并；只共用对象、来源、检索词或部分资料时，不据此合并独立任务。
  · 用户并列要求不同对象各自的分析时，分别保留对象、交付任务和业务时间要求。只有用户明确要求比较或借鉴时，才在 Focus 中建立跨对象依赖。

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

# The planner often writes a Chinese evidence angle for an English request.  That
# is fine for intent planning, but Feishu keyword search also needs the original
# surface form when the target document/table is English.  Keep this extractor
# deliberately conservative: it is *not* query expansion and must never invent
# a business entity.  It only returns product-like literals, acronyms, and a
# small set of explicit proper-name phrases copied from the user's request.
_SURFACE_STOPWORDS = {
    "Should", "Could", "Would", "What", "Which", "When", "Where", "Why", "How",
    "The", "This", "That", "These", "Those", "And", "Or", "For", "From", "To",
    "With", "In", "On", "At", "By", "Of", "Is", "Are", "Be", "Has", "Have",
}
_SURFACE_PROPER_RE = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*){0,2}\b")


def _original_surface_anchors(query: str, *, limit: int = 6) -> List[str]:
    """Return bounded user-authored literals suitable for source-query repair."""
    out: List[str] = []
    for match in _SURFACE_PROPER_RE.finditer(query or ""):
        phrase = match.group(0).strip()
        words = phrase.split()
        # Keep all-caps acronyms, product identifiers containing a digit, and
        # title-cased names.  Single ordinary sentence starters are excluded.
        is_acronym = phrase.isupper() and len(phrase) >= 2
        has_digit = any(ch.isdigit() for ch in phrase)
        is_name = len(words) > 1 or (phrase not in _SURFACE_STOPWORDS and phrase[0].isupper())
        if not (is_acronym or has_digit or is_name):
            continue
        if phrase.casefold() not in {x.casefold() for x in out}:
            out.append(phrase)
        if len(out) >= limit:
            break
    return out


def _preserve_source_surface_anchors(original_query: str, focuses: List[PlannedFocus]) -> List[PlannedFocus]:
    """Attach one bounded lexical fallback probe for cross-language source plans.

    A translated semantic query and a three-term literal probe serve different
    purposes.  The latter prevents an AND-like document search from losing an
    English table because its product/task wording was translated.  It only
    copies user-authored surfaces, is bounded to one probe per source/focus,
    and is not appended as a primary executable query. Source execution may
    invoke it only after primary routes return zero candidates.
    """
    anchors = _original_surface_anchors(original_query)
    if not anchors:
        return focuses
    repaired: List[PlannedFocus] = []
    for focus in focuses:
        by_source: Dict[str, List[PlannedSubQuery]] = {}
        for sq in focus.sub_queries:
            by_source.setdefault(sq.target_source, []).append(sq)
        repaired_queries = list(focus.sub_queries)
        for queries in by_source.values():
            combined = " ".join(sq.query for sq in queries).casefold()
            missing = [anchor for anchor in anchors if anchor.casefold() not in combined]
            has_latin_input = bool(re.search(r"[A-Za-z]", original_query))
            has_cjk_plan = bool(re.search(r"[\u4e00-\u9fff]", " ".join(sq.query for sq in queries)))
            if not (has_latin_input and has_cjk_plan and len(anchors) >= 2):
                continue
            # The final three literals are usually the requested comparison
            # set (for example, countries) rather than generic sentence
            # starters or the product name already covered by the main query.
            probe_terms = (missing or anchors)[-3:]
            probe = " ".join(probe_terms)
            if not probe or any(re.sub(r"\s+", " ", sq.query).casefold() == probe.casefold() for sq in queries):
                continue
            target = next((sq for sq in queries if sq.query_type == "structured"), queries[0])
            replacement = replace(
                target,
                fallback_variants=list(dict.fromkeys([*target.fallback_variants, probe])),
                fallback_variant_origins={
                    **target.fallback_variant_origins,
                    probe: "original_surface",
                },
            )
            repaired_queries[repaired_queries.index(target)] = replacement
        repaired.append(
            replace(focus, sub_queries=repaired_queries)
        )
    return repaired


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
    max_queries_per_source: int = 2,
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
        source_counts: Dict[str, int] = {}
        for sq_item in sqs_raw:
            psq = _parse_planned_sub_query(sq_item, enabled_set)
            key = (psq.target_source, re.sub(r"\s+", " ", psq.query).strip().casefold()) if psq is not None else None
            if psq is not None and key not in seen_source_queries and source_counts.get(psq.target_source, 0) < max_queries_per_source:
                sub_queries.append(psq)
                seen_source_queries.add(key)
                source_counts[psq.target_source] = source_counts.get(psq.target_source, 0) + 1
        out.append(
            PlannedFocus(focus_id=focus_id, focus_content=focus_content, sub_queries=sub_queries)
        )

    if not out:
        raise PlannerFailure(reason="empty_focuses", detail="all focuses dropped post-validate")

    out = _preserve_source_surface_anchors(original_query, out)
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
    preset: str = "balanced",
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
        preset=preset,
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
            use_provider_output_limit=True,
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

    focus_limit = _focus_limit_for_sources(enabled_sources, cfg.max_focuses, preset)
    plan = _parse_global_plan(
        raw_obj,
        original_query=query,
        enabled_sources=enabled_sources,
        max_focuses=focus_limit,
        max_queries_per_source=2 if preset in {"balanced", "deep_dive"} else 3,
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
