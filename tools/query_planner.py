"""Query Planner — 多源感知的查询拆解与路由

1× LLM 调用（~1-2s），将用户 query 拆解为 FocusGroup 结构：
  每个 FocusGroup 包含一组可评估的 focuses + 对应的 sub_queries。
"""

import datetime
import json
import logging
from typing import List

from tools.llm_client import call_llm, parse_json_response
from .models import SubQueryTask, FocusGroup, QueryPlan
from .source_query_config import (
    BAILIAN_L2_TAGS,
    build_multi_entity_strategy_text,
    build_source_query_rules_text,
    build_required_targets_rule_text,
    build_routing_rules_text,
    build_tags_rules_text,
    validate_and_fix_plan,
)

logger = logging.getLogger(__name__)

_BAILIAN_L2_TAGS = BAILIAN_L2_TAGS  # 从 config 导入

# 时效 tag 格式: last_long 或 YYYY_QN (e.g. 2025_Q4)
_TIME_TAG_HINT = '"last_long" 或 "YYYY_QN"（如 "2025_Q4"、"2026_Q1"）'

# focus 示例（自由语义描述，不限定枚举，如同 thinking_focus 模式）
_FOCUS_GUIDE = (
    "具体描述这个子查询希望检索结果帮助回答 query 的哪个方面、"
    "需要什么类型的内容。要求可评估（能判断一个切片是否真正服务于该意图）。\n"
    "示例: '找到广告投放 ROAS 的历史数据和环比变化指标' | "
    "'调查这个项目复盘的结论和方向建议' | "
    "'获取物流商合作协议和长期政策条款'"
)

_SOURCE_PROFILES_BASE = {
    "feishu": "飞书文档搜索（关键词 AND 匹配），每个 query 必须只含 2-3 个短关键词，格式「核心实体 + 方向词」",
    "bailian": "百炼向量知识库（语义检索），支持较长的自然语言 query，可包含完整问句",
}


def _build_source_profiles() -> dict:
    """构建源特性描述，动态注入百炼知识库名称。"""
    from .bailian_retrieve import get_configured_indexes
    profiles = dict(_SOURCE_PROFILES_BASE)
    indexes = get_configured_indexes()
    if indexes and "bailian" in profiles:
        names = "/".join(indexes.keys())
        profiles["bailian"] = (
            f"百炼向量知识库（语义检索，包含业务实体知识库: {names}），"
            "支持较长的自然语言 query，可包含完整问句"
        )
    return profiles


def plan_query(
    query: str,
    available_sources: List[str],
    max_sub_queries: int = 4,
    session_context: str = "",
) -> QueryPlan:
    """将用户 query 拆解为多源感知的子查询计划。

    Args:
        query: 原始用户查询
        available_sources: 当前可用的检索源列表，如 ["feishu", "bailian"]
        max_sub_queries: 最大子查询数

    Returns:
        QueryPlan，LLM 失败时返回 fallback plan（原始 query 发给所有源）
    """
    from .bailian_retrieve import get_configured_indexes
    source_profiles = _build_source_profiles()
    profiles_text = "\n".join(
        f"  - {name}: {desc}"
        for name, desc in source_profiles.items()
        if name in available_sources
    )
    configured_indexes = list(get_configured_indexes().keys()) if "bailian" in available_sources else []
    l2_tags_text = ", ".join(_BAILIAN_L2_TAGS)

    _today = datetime.date.today()
    _time_context = (
        f"当前日期：{_today.strftime('%Y年%m月%d日')}  "
        f"今年={_today.year}  去年={_today.year - 1}  "
        f"本季度={'Q' + str((_today.month - 1) // 3 + 1)}  "
        f"本月={_today.month}月"
    )

    prompt = f"""你是多源检索 query 规划助手，同时也是一个精细的"派活工"。

{_time_context}
用户 query："{query}"
上下文：{session_context or "无"}

可用检索源及其特性：
{profiles_text}

百炼可用业务实体知识库：{json.dumps(configured_indexes)}
百炼知识库标签（l2 业务分类）：{l2_tags_text}
时效标签格式：{_TIME_TAG_HINT}

任务：
1. 判断 query_type（严格分类，影响检索路由策略）：
   - "data"：需要具体数字、指标、KPI、GMV、销售额、流量、库存、表格数据、对比数值、
             任务/项目进展状态（进度表、任务状态追踪）、清单/名单（KOL名单、合作清单）
     例：nubia UK 3月销售额 / 各站点流量表现 / 库存情况和目标达成率 /
         项目任务进展和状态 / KOL合作名单 / 各渠道费用汇总
   - "concept"：需要背景分析、策略规划、方案设计、竞品对比、痛点分析（无明确数字需求）
     例：hypershell的竞品格局和差异化定位 / 产品架构设计和技术方案 / 项目的痛点
   - "mixed"：query 中同时包含数据维度 + 分析/总结维度
     典型触发词："月报"/"周报"/"工作总结"+"运营" / "合作进度"/"执行进度"（含名单+状态）
     例：nubia Amazon渠道运营策略和12月工作总结（需要策略文档 + 数据月报）
         REDMAGIC 11 Pro 首发KOL合作进度（需要名单数据+进展分析）
   ⚠️ 关键判断：含有"月报/周报/工作总结"且同时涉及渠道运营 → mixed
              含有"进展/状态/任务"且指向表格/清单 → data
              只要背景分析策略不要数字 → concept
2. 检测时间过滤器（用于飞书）：
   - 相对时间词（今年/本月/本周/最新/近期）→ "last_N_days"（今年≈365，本月≈30，本周≈7）
   - 绝对时间（2024年/去年等）→ null，将时间词保留在子 query 中
   - 时间感知规则（结合上方当前日期推算）：
     · "今年" = 当前年份（{_today.year}），"去年" = {_today.year - 1}年，"前年" = {_today.year - 2}年
     · "本月" = {_today.month}月，"上月" = {(_today.month - 2) % 12 + 1}月
     · "去年同期/同月" → 比较 {_today.year}年 vs {_today.year - 1}年，需生成两组时效 tag（如 2026_Q1 vs 2025_Q1）
     · "今年和去年对比" → 生成两个 focus_group 或在同一 group 内用两个 bailian sub_query 分别携带不同时效 tag
     · 月份词（如"3月份"）在无年份时 → 默认当前年份（{_today.year}年），再根据"去年同期"推算对比年份
3. 将 query 拆分为 focus_groups（每个 group = 一个独立的检索意图方向）：
   每个 focus_group 包含：
   - focuses: 一组可评估的检索目标（细粒度拆解），每个 focus 描述一个具体维度/方面
     {_FOCUS_GUIDE}
     示例: query="对比 nubia 和 hypershell 各渠道表现" →
       group A focuses: ["nubia DTC渠道销售数据和转化率", "nubia Marketplace渠道的销售表现", "nubia Affiliate渠道流量和收入"]
       group B focuses: ["hypershell DTC渠道销售数据和转化率", "hypershell Marketplace渠道的销售表现", ...]
   - sub_queries: 为满足该 group 的 focuses 而发出的多源检索任务，每个含 query/targets/tags/indexes

⚠️ 各源 query 编写规则（严格遵守，同一信息需求对不同源生成不同风格 query）：
{build_source_query_rules_text(available_sources)}

⚠️ 数量与路由规则（关键）：
{build_routing_rules_text(available_sources, max_sub_queries)}

⚠️ 通用规则：
- 保留英文原词（品牌名、术语等），不翻译
{build_tags_rules_text()}
- targets 只能包含: {json.dumps(available_sources)}
- indexes 只能包含: {json.dumps(configured_indexes)}
- required_targets 说明：若用户在 query 中显式指定了某个源，对该意图对应的 focus_group 填写 required_targets。
  支持两类格式：
    · 自然语言：「在飞书里找」「飞书文档」「用 notion 查」
    · 标签格式（优先级更高）：「#feishu」「#bailian」「#notion」等 hash-tag 风格
  显式指定词汇 → 对应源（只含当前可用源）：
{build_required_targets_rule_text(available_sources)}
  未显式指定则为 []。required_targets 只能包含该 FG sub_queries.targets 中实际存在的源。

{build_multi_entity_strategy_text()}

Return JSON only:
{{
  "query_type": "data" | "concept" | "mixed",
  "time_filter": "last_N_days" | null,
  "focus_groups": [
    {{
      "focuses": ["focus维度1: 具体描述...", "focus维度2: 具体描述..."],
      "required_targets": [],
      "sub_queries": [
        {{
          "query": "feishu 关键词",
          "targets": ["feishu"],
          "tags": [],
          "indexes": []
        }},
        {{
          "query": "百炼语义化检索句子",
          "targets": ["bailian"],
          "tags": ["Ads", "2025_Q4"],
          "indexes": ["nubia"]
        }}
      ]
    }}
  ]
}}"""

    try:
        # [P2.7] query_planner 用 fallback 模型（更准确），lite 用于非关键路径
        import os as _os
        _planner_model = _os.getenv("GEMINI_FALLBACK_MODEL") or None
        response_text = call_llm(
            prompt, max_tokens=2048, timeout=15.0, _caller_tag="query_planner",
            model=_planner_model,
        )
        result = parse_json_response(response_text)

        focus_groups = []
        for fg in result.get("focus_groups", []):
            focuses = [f for f in fg.get("focuses", []) if isinstance(f, str) and f.strip()]
            sub_queries = []
            for sq in fg.get("sub_queries", []):
                q = sq.get("query", "").strip()
                if not q:
                    continue
                targets = [t for t in sq.get("targets", []) if t in available_sources]
                if not targets:
                    targets = list(available_sources)
                raw_tags = sq.get("tags", [])
                valid_tags = [t for t in raw_tags if isinstance(t, str) and t]
                raw_indexes = sq.get("indexes", [])
                valid_indexes = [i for i in raw_indexes if i in configured_indexes]
                sub_queries.append(SubQueryTask(
                    query=q,
                    targets=targets,
                    tags=valid_tags,
                    indexes=valid_indexes,
                ))
            if sub_queries:
                # 解析 required_targets：只保留该 FG 的 sub_queries.targets 中实际存在的源
                fg_all_targets = {t for sq in sub_queries for t in sq.targets}
                raw_required = fg.get("required_targets", [])
                valid_required = [t for t in raw_required if t in fg_all_targets]
                focus_groups.append(FocusGroup(
                    focuses=focuses,
                    sub_queries=sub_queries,
                    required_targets=valid_required,
                ))

        if not focus_groups:
            raise ValueError("No valid focus_groups in LLM response")

        plan = QueryPlan(
            original_query=query,
            focus_groups=focus_groups,
            time_filter=result.get("time_filter"),
            query_type=result.get("query_type", "mixed"),
        )

        # 规则校验 + 自动修复（tag fuzzy fix、targets/indexes 校验、对称性检测）
        plan = validate_and_fix_plan(plan, available_sources, configured_indexes)

        # 完整 plan 日志
        total_sqs = sum(len(g.sub_queries) for g in plan.focus_groups)
        logger.info(
            "QueryPlan: type=%s  time_filter=%s  groups=%d  total_sub_queries=%d",
            plan.query_type, plan.time_filter, len(plan.focus_groups), total_sqs,
        )
        for gi, fg in enumerate(plan.focus_groups, 1):
            logger.info("  [Group%d] focuses=%s", gi, fg.focuses)
            for si, sq in enumerate(fg.sub_queries, 1):
                logger.info(
                    "    [SQ%d.%d] targets=%s  indexes=%s  tags=%s  query=%s",
                    gi, si, sq.targets, sq.indexes, sq.tags, sq.query[:100],
                )

        return plan

    except Exception as e:
        logger.warning("plan_query LLM failed, using fallback: %s", e)
        return QueryPlan(
            original_query=query,
            focus_groups=[FocusGroup(
                focuses=[query],
                sub_queries=[SubQueryTask(
                    query=query,
                    targets=list(available_sources),
                    tags=[],
                    indexes=[],
                )],
            )],
            time_filter=None,
            query_type="mixed",
        )