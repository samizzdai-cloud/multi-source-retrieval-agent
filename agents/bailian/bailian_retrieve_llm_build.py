"""Bailian-specific LLM Configuration & Prompts."""

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
    query: str = Field(..., description="子查询原文")
    target_focus: str = Field(..., description="该 sub_query 回答的 focus_id")
    tags: List[str] = Field(default_factory=list, description="L2 业务 tag + 时效 tag")
    indexes: List[str] = Field(default_factory=list, description="目标知识库名")
    author_filter: Optional[str] = Field(default=None, description="作者或归属过滤，如果明确要求'我写的'等填'me'")

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
    · tags: 业务或时效标签列表
    · indexes: 知识库索引列表

输出示例（百炼源）：
{{"sub_queries": [
  {{"query": "nubia UK/英国/United Kingdom 2026年3月份 DTC 销售额和GMV表现",
   "target_focus": "focus_0", "tags": ["DTC", "2026_Q1"], "indexes": ["nubia"]}},
  {{"query": "nubia Marketplace 英国 转化率", "target_focus": "focus_1",
   "tags": ["Marketplace"], "indexes": ["nubia"]}}
]}}
"""

PLAN_EVAL_INSTRUCTION = f"""你是检索评估与规划器。判断是否继续 replan，若停止则给出 top_k。

用户消息里会提供：
- focuses_block: 检索维度列表
- chunks_block: 按 focus 分组渲染的候选 chunks，每个 chunk 标注了 focuses=[...] 和 sq=...
- loop_info: loop_n + min_loops + max_loops
- source_top_k: top_k_chunk_ids 上限

{ENTITY_FIDELITY_BLOCK}

## 决策规则
- 若已有 chunks 足够覆盖所有 focus → decision="stop"
- 否则 → decision="continue"，给出 replan_queries（同样遵守上述实体保真原则）

{EVAL_RUBRIC_BLOCK}

## decision=stop 时输出
- top_k_chunk_ids 按相关度降序
- answered_focuses 严格填写该 chunk 实际回答的 focus_id（部分回答不算）
"""

EVAL_ONLY_INSTRUCTION = f"""你是检索评估器。对每个 focus，评估哪些 chunks 能回答它，然后选出综合 top_k。

用户消息里会提供：
- focuses_block: 检索维度列表
- chunks_block: 按 focus 分组渲染的候选 chunks；每个 chunk 标注了 focuses=[...] 和 sq=...
- source_top_k: top_k_chunk_ids 上限

{EVAL_RUBRIC_BLOCK}

## 输出要求
- top_k_chunk_ids 按相关度降序
- answered_focuses 严格填写该 chunk 实际回答的 focus_id（部分回答不算）
"""

# ============================================================================
# Builders
# ============================================================================

def build_bailian_plan_only_agent() -> Optional[LlmAgent]:
    return LlmAgent(
        name="bailian_plan_only",
        model=_llm_contracts.MODEL,
        instruction=PLAN_ONLY_INSTRUCTION,
        output_schema=PlanOnlyOutput,
        generate_content_config=AGENT_CFG_PLAN_ONLY,
    )

def build_bailian_eval_only_agent(source_top_k: int) -> LlmAgent:
    # Schema cap 留出 multi-focus 动态预算空间（base + EXTRA），运行时由
    # SourceAgent._effective_source_top_k 决定实际 effective N（≤ schema cap）。
    return LlmAgent(
        name="bailian_eval_only",
        model=_llm_contracts.MODEL,
        instruction=EVAL_ONLY_INSTRUCTION,
        output_schema=build_eval_only_model(source_top_k + MULTI_FOCUS_TOP_K_EXTRA),
        generate_content_config=AGENT_CFG_EVAL_ONLY,
    )

def build_bailian_plan_eval_agent(source_top_k: int) -> Optional[LlmAgent]:
    return LlmAgent(
        name="bailian_plan_eval",
        model=_llm_contracts.MODEL,
        instruction=PLAN_EVAL_INSTRUCTION,
        output_schema=build_plan_eval_model(source_top_k + MULTI_FOCUS_TOP_K_EXTRA, SubQueryItem),
        generate_content_config=AGENT_CFG_PLAN_EVAL,
    )
