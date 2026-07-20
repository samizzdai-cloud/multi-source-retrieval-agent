"""Feishu-specific LLM Configuration & Prompts."""

from typing import List, Literal, Optional, Type
from pydantic import BaseModel, Field, create_model

from google.adk.agents.llm_agent import LlmAgent
from tools.core import llm_contracts as _llm_contracts
from tools.core.llm_contracts import (
    Decision, TierAssignment, build_eval_only_model, build_plan_eval_model,
    EVAL_RUBRIC_BLOCK, MULTI_FOCUS_TOP_K_EXTRA,
    AGENT_CFG_EVAL_ONLY, AGENT_CFG_PLAN_ONLY, AGENT_CFG_PLAN_EVAL,
)
from tools.core.source_query_config import ENTITY_FIDELITY_BLOCK

# ============================================================================
# Pydantic Schemas
# ============================================================================

class SubQueryItem(BaseModel):
    query: str = Field(..., description="子查询原文，如果查询极其空泛可留空或补充通用词汇")
    target_focus: str = Field(..., description="该 sub_query 回答的 focus_id")
    time_filter: Optional[str] = Field(default=None, description="ISO 日期格式（如 '>2025-01-01'）或相对时间（如 'last_30_days'）")
    query_type: Optional[Literal["structured", "concept", "personal_recent"]] = Field(default=None, description="查询模式分类")
    author_filter: Optional[str] = Field(default=None, description="作者过滤，如'me'")

class PlanOnlyOutput(BaseModel):
    sub_queries: List[SubQueryItem] = Field(..., description="按 focus 拆解出的子查询列表")


# ============================================================================
# Prompts
# ============================================================================

PLAN_ONLY_INSTRUCTION = f"""你是检索规划器。针对给定的 source，按 focuses 拆解 sub_queries，并自主选择需要的参数。

用户消息里会提供：
- focuses_block: 检索维度列表
- source_profile_block: 目标检索源的能力画像
- source_top_k: 该源最终产出 chunks 上限
- sub_query_total_cap: sub_queries 总数上限（**硬纪律**）
- source_query_rules_block: 该源的 query 编写规则

{ENTITY_FIDELITY_BLOCK}

## 输出要求（严格 JSON）
- 每个 focus 至少覆盖 1 条 sub_query，最多 3 条
- sub_queries 总数不超过给定的上限
- 每条 sub_query 必填：query, target_focus
- 每条 sub_query 选填：
    · time_filter: 取值如 ">2025-01-01" 或 "last_30_days" 或 null
    · query_type: 取值 "structured", "concept" 或 "personal_recent"
    · author_filter: 取值 "me" 或 null

输出示例（飞书源）：
{{"sub_queries": [
  {{"query": "用户给定实体 月度 销售", "target_focus": "focus_0", "time_filter": null, "query_type": "structured"}},
  {{"query": "复盘 总结", "target_focus": "focus_1", "time_filter": "last_30_days", "query_type": "personal_recent", "author_filter": "me"}}
]}}
"""

PLAN_EVAL_INSTRUCTION = f"""你是检索评估与规划器。判断是否继续 replan，若停止则给出 top_k。

用户消息里会提供：
- focuses_block: 检索维度列表
- chunks_block: 按 focus 分组渲染的候选 chunks，每个 chunk 标注了 chunk_ref=C1/C2/...、focuses=[...] 和 sq=...
- loop_info: loop_n + min_loops + max_loops
- source_top_k: top_k_chunk_ids 上限

{ENTITY_FIDELITY_BLOCK}

## 决策规则
- 先为每个 focus 输出 focus_decisions：status、gap_type、reason。
- status 只允许 `covered | gap | insufficient`；部分覆盖必须写 `status="gap"`，禁止输出 `partially_covered` 或 `evidence_gap`。
- gap_type 只允许 `none | upstream_evidence_needed | query_refinement`；`covered` 必须配 `none`，其余状态不得配 `none`。
- 当前文档只有诊断或结论、但回答需要上游日期/账户/结构化原始资料 → gap_type="upstream_evidence_needed"；replan_queries 使用可执行 query_type（需要表格时为 structured）。
- 仅在词面/API 召回不足且仍有合理同一意图检索方式 → gap_type="query_refinement"。
- 相关文档已确认业务事件，但缺少销量、转化率、广告报表、热力图、库存等原始测量资料，是 upstream gap，不是 query refinement。
- 只有相关候选为零/偏题，或同一证据仍可通过更准确关键词找到，才是 query refinement。
- 若已有 chunks 足够覆盖所有 focus → decision="stop"。
- 否则 → decision="continue"，给出与每个 gap_type 一致的 replan_queries（同样遵守上述实体保真原则）。

{EVAL_RUBRIC_BLOCK}

## decision=stop 时输出
- top_k_chunk_ids 和 tiers[].chunk_id 只能使用 chunks_block 中的 chunk_ref（如 C1），不得输出 canonical chunk ID；按相关度降序。
- answered_focuses 严格填写该 chunk 实际回答的 focus_id（部分回答不算）
- 严格输出 JSON 对象，且 `tiers` 必须包含每个 active preview chunk 的 judgment：
  `{{"decision":"stop","top_k_chunk_ids":["C1"],"tiers":[{{"chunk_id":"C1","tier":"important","relevance":0.8,"answered_focuses":["focus_0"],"angle_tag":"funnel"}}],"focus_decisions":[{{"focus_id":"focus_0","status":"covered","gap_type":"none","reason":"..."}}]}}`。
"""

EVAL_ONLY_INSTRUCTION = f"""你是检索评估器。对每个 focus，评估哪些 chunks 能回答它，然后选出综合 top_k。

用户消息里会提供：
- focuses_block: 检索维度列表
- chunks_block: 按 focus 分组渲染的候选 chunks；每个 chunk 标注了 chunk_ref=C1/C2/...、focuses=[...] 和 sq=...
- source_top_k: top_k_chunk_ids 上限

{EVAL_RUBRIC_BLOCK}

## 输出要求
- top_k_chunk_ids 和 tiers[].chunk_id 只能使用 chunks_block 中的 chunk_ref（如 C1），不得输出 canonical chunk ID；按相关度降序。
- answered_focuses 严格填写该 chunk 实际回答的 focus_id（部分回答不算）
- 严格输出 JSON 对象，`tiers` 不得省略：每个被输出的 chunk 至少有
  `{{"chunk_id":"C1","tier":"critical|important|noise","relevance":0.0,"answered_focuses":["focus_0"],"angle_tag":"..."}}`。
"""

# ============================================================================
# Builders
# ============================================================================

def build_feishu_plan_only_agent() -> Optional[LlmAgent]:
    return LlmAgent(
        name="feishu_plan_only",
        model=_llm_contracts.build_llm_model(),
        instruction=PLAN_ONLY_INSTRUCTION,
        output_schema=PlanOnlyOutput,
        generate_content_config=AGENT_CFG_PLAN_ONLY,
    )

def build_feishu_eval_only_agent(source_top_k: int) -> LlmAgent:
    # Schema cap 留 multi-focus 动态预算（base + EXTRA）；运行时由 effective_top_k 截断。
    return LlmAgent(
        name="feishu_eval_only",
        model=_llm_contracts.build_llm_model(),
        instruction=EVAL_ONLY_INSTRUCTION,
        output_schema=build_eval_only_model(source_top_k + MULTI_FOCUS_TOP_K_EXTRA),
        generate_content_config=AGENT_CFG_EVAL_ONLY,
    )

def build_feishu_plan_eval_agent(source_top_k: int) -> Optional[LlmAgent]:
    return LlmAgent(
        name="feishu_plan_eval",
        model=_llm_contracts.build_llm_model(),
        instruction=PLAN_EVAL_INSTRUCTION,
        output_schema=build_plan_eval_model(source_top_k + MULTI_FOCUS_TOP_K_EXTRA, SubQueryItem),
        generate_content_config=AGENT_CFG_PLAN_EVAL,
    )
