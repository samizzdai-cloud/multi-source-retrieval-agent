"""Feishu-specific LLM Configuration & Prompts."""

from typing import List, Literal, Optional, Type
from pydantic import BaseModel, Field, create_model

from google.adk.agents.llm_agent import LlmAgent
from tools.core.llm_contracts import MODEL, Decision, TierAssignment, build_eval_only_model

# ============================================================================
# Pydantic Schemas
# ============================================================================

class SubQueryItem(BaseModel):
    query: str = Field(..., description="子查询原文")
    target_focus: str = Field(..., description="该 sub_query 回答的 focus_id")
    time_filter: Optional[str] = Field(default=None, description="feishu 专用：'last_N_days' 或 None")
    query_type: Optional[Literal["structured", "concept"]] = Field(default=None, description="feishu 专用：structured=结构化, concept=概念")

class PlanOnlyOutput(BaseModel):
    sub_queries: List[SubQueryItem] = Field(..., description="按 focus 拆解出的子查询列表")

def build_plan_eval_model(source_top_k: int) -> Type[BaseModel]:
    return create_model(
        f"PlanEvalOutputK{source_top_k}",
        decision=(Decision, Field(..., description="continue=继续 replan，stop=终止并给 top_k")),
        replan_queries=(Optional[List[SubQueryItem]], Field(default=None, description="decision=continue 时必填")),
        top_k_chunk_ids=(Optional[List[str]], Field(default=None, max_length=source_top_k, description="decision=stop 时必填")),
        tiers=(Optional[List[TierAssignment]], Field(default=None, description="decision=stop 时必填")),
    )

# ============================================================================
# Prompts
# ============================================================================

PLAN_ONLY_INSTRUCTION = """你是检索规划器。针对给定的 source，按 focuses 拆解 sub_queries，并自主选择需要的参数。

用户消息里会提供：
- focuses_block: 检索维度列表
- source_profile_block: 目标检索源的能力画像
- source_top_k: 该源最终产出 chunks 上限
- sub_query_total_cap: sub_queries 总数上限
- source_query_rules_block: 该源的 query 编写规则

## 输出要求（严格 JSON）
- 每个 focus 至少覆盖 1 条 sub_query，最多 3 条
- sub_queries 总数不超过给定的上限
- 每条 sub_query 必填：query, target_focus
- 每条 sub_query 选填：
    · time_filter: 取值如 "last_30_days" 或 null
    · query_type: 取值 "structured" 或 "concept" 

输出示例（飞书源）：
{"sub_queries": [
  {"query": "nubia 英国 销售", "target_focus": "focus_0", "time_filter": null, "query_type": "structured"},
  {"query": "nubia 策略", "target_focus": "focus_1", "time_filter": "last_30_days", "query_type": "concept"}
]}
"""

PLAN_EVAL_INSTRUCTION = """你是检索评估与规划器。判断是否继续 replan，若停止则给出 top_k。

用户消息里会提供：
- focuses_block: 检索维度列表
- chunks_block: 累积候选 chunks
- loop_info: loop_n + min_loops + max_loops
- source_top_k: top_k_chunk_ids 上限

## 决策规则
- 若已有 chunks 足够覆盖所有 focus → decision="stop"
- 否则 → decision="continue"，给出 replan_queries

## 评分维度（decision=stop 时对 top_k 每个 chunk 都要给）
1. tier: critical / important / noise
2. relevance: [0, 1] 连续分
3. answered_focuses: 该 chunk 实际回答了哪些 focus_id
"""

EVAL_ONLY_INSTRUCTION = """你是检索评估器。对给定 chunks 打分并选出 top_k。

用户消息里会提供：
- focuses_block: 检索维度列表
- chunks_block: 候选 chunks
- source_top_k: top_k_chunk_ids 上限

## 评分维度
1. tier: critical / important / noise
2. relevance: [0, 1] 连续分
3. answered_focuses: 该 chunk 实际回答了哪些 focus_id
"""

# ============================================================================
# Builders
# ============================================================================

def build_feishu_plan_only_agent() -> Optional[LlmAgent]:
    return LlmAgent(
        name="feishu_plan_only",
        model=MODEL,
        instruction=PLAN_ONLY_INSTRUCTION,
        output_schema=PlanOnlyOutput,
    )

def build_feishu_eval_only_agent(source_top_k: int) -> LlmAgent:
    return LlmAgent(
        name="feishu_eval_only",
        model=MODEL,
        instruction=EVAL_ONLY_INSTRUCTION,
        output_schema=build_eval_only_model(source_top_k),
    )

def build_feishu_plan_eval_agent(source_top_k: int) -> Optional[LlmAgent]:
    return LlmAgent(
        name="feishu_plan_eval",
        model=MODEL,
        instruction=PLAN_EVAL_INSTRUCTION,
        output_schema=build_plan_eval_model(source_top_k),
    )
