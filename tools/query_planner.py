"""Global Query Planner (v2) — 1× LLM 调用，职责只剩「语义拆解 + 源路由」。

输出结构：``focus_groups[{focuses: [text], targets: [source]}]``

**已删除**：sub_queries / tags / indexes / time_filter / required_targets / query_type
这些全部下放到各 SourceAgent 的 plan_only LLM 去做，保持职责单一。
"""

import json
import logging
from typing import List

from tools.llm_client import call_llm, parse_json_response
from .models import FocusGroup, QueryPlan
from .source_query_config import BRAND_ROUTING

logger = logging.getLogger(__name__)


# 源特性描述（仅供 planner 做路由决策，简短精准）
_SOURCE_PROFILES_BASE = {
    "feishu": "飞书文档：团队内部沉淀的运营材料、会议纪要、项目文档、表格数据。",
    "bailian": "百炼知识库：公司级结构化知识（产品、渠道、合规、市场情报等）向量检索。",
    "google": "Google Drive：跨团队协作文档（当前未启用检索）。",
    "notion": "Notion：外部合作/公共知识文档（当前未启用检索）。",
}

_FOCUS_GUIDE = (
    "每个 focus 是一个**可评估**的检索维度（能判断一个切片是否真正服务于该意图）。"
    "示例：\n"
    "  - 'nubia DTC渠道的销售数据和转化率'\n"
    "  - 'hypershell 德国市场的运营策略'\n"
    "  - '这次项目复盘的核心结论和改进方向'"
)


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


def plan_query(
    query: str,
    available_sources: List[str],
    session_context: str = "",
    max_focus_groups: int = 4,
) -> QueryPlan:
    """把用户 query 拆成 focus_groups，并为每个 group 路由源。

    输出 ``QueryPlan`` 只含：``original_query`` + ``focus_groups[{focuses, targets}]``。
    """
    profiles_block = _build_source_profiles_block(available_sources)
    brand_block = _build_brand_routing_block(available_sources)

    prompt = f"""你是多源检索的「全局规划器」。职责只有两件事：
1. **语义拆解**：把用户 query 拆成若干个可独立评估的 focus（检索维度）。
2. **源路由**：为每个 focus_group 分配最合适的检索源（targets）。

用户 query：「{query}」
上下文：{session_context or "无"}

可用检索源：
{profiles_block}

{brand_block}

{_FOCUS_GUIDE}

## 拆解规则

- 每个 focus_group = 一个独立的检索意图方向。多实体/多主题时分多个 group。
- 每个 group 内的 focuses 是该方向下更细粒度的维度拆解。
- 多实体对比：对称拆分（两侧 focuses 维度一致、数量一致）。
- 多实体独立询问（不是对比）：各实体按实际被问到的内容独立拆分，无需对称。
- **最多 {max_focus_groups} 个 focus_group，宁精勿多**。

## 源路由规则

- targets 只能从 {json.dumps(available_sources)} 中选，且不能为空。
- 按「源特性」决定：内部运营/表格/文档 → feishu；结构化知识/语义推理 → bailian。
- 用户显式指定源（"在飞书里找"、"#bailian"）时，targets 只保留该源。

## 输出（严格 JSON，不要额外说明）

{{
  "focus_groups": [
    {{
      "focuses": ["具体描述 focus 维度 1...", "具体描述 focus 维度 2..."],
      "targets": ["feishu", "bailian"]
    }}
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

        focus_groups: List[FocusGroup] = []
        for fg in result.get("focus_groups", []):
            focuses = [f for f in fg.get("focuses", []) if isinstance(f, str) and f.strip()]
            if not focuses:
                continue
            raw_targets = fg.get("targets", [])
            targets = [t for t in raw_targets if t in available_sources]
            if not targets:
                targets = list(available_sources)
            focus_groups.append(FocusGroup(focuses=focuses, targets=targets))

        if not focus_groups:
            raise ValueError("No valid focus_groups in LLM response")

        plan = QueryPlan(original_query=query, focus_groups=focus_groups)

        logger.info(
            "QueryPlan: groups=%d, focuses_total=%d",
            len(plan.focus_groups),
            sum(len(g.focuses) for g in plan.focus_groups),
        )
        for gi, fg in enumerate(plan.focus_groups, 1):
            logger.info("  [Group%d] targets=%s focuses=%s", gi, fg.targets, fg.focuses)

        return plan

    except Exception as e:
        logger.warning("plan_query LLM failed, using fallback: %s", e)
        return QueryPlan(
            original_query=query,
            focus_groups=[FocusGroup(
                focuses=[query],
                targets=list(available_sources),
            )],
        )

