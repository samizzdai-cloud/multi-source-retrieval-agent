"""Notion-specific LLM Configuration & Prompts."""

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


输出示例（Notion源）：
{"sub_queries": [
  {"query": "设计文档", "target_focus": "focus_0"}
]}
"""

PLAN_EVAL_INSTRUCTION = """你是检索评估与规划器。判断是否继续 replan，若停止则给出 top_k。

用户消息里会提供：
- focuses_block: 检索维度列表
- chunks_block: 按 focus 分组渲染的候选 chunks，每个 chunk 标注了 focuses=[...]（表示它属于哪些 focus）和 sq=...（表示它来自哪个 sub_query）
- loop_info: loop_n + min_loops + max_loops
- source_top_k: top_k_chunk_ids 上限

## 决策规则
- 若已有 chunks 足够覆盖所有 focus → decision="stop"
- 否则 → decision="continue"，给出 replan_queries

## 评分维度（decision=stop 时对 top_k 每个 chunk 都要给）
1. **回答能力优先**：评分必须围绕 "该 chunk 能否直接回答对应的 focus/sub_query"
   - critical: chunk 内容直接包含 focus 要求的答案（时间、地点、指标、主题都匹配）
   - important: chunk 内容部分相关，可作为辅助参考
   - noise: chunk 内容与 focus 无关或时空/主题不匹配
2. **时空/实体一致性**：特别关注 chunk 的时间范围、地区、产品型号、品牌是否与 focus/sub_query 一致
3. **信息密度**：优先选择包含具体数据（数字、表格）而非泛泛描述的 chunks
4. relevance: [0, 1] 连续分
5. answered_focuses: 该 chunk 实际回答了哪些 focus_id
   - 必须严格填写该 chunk 实际能回答的 focus_id
   - 如果一个 chunk 只部分回答某个 focus（如只有 GMV 没有订单量），该 focus 不应出现在 answered_focuses 中
"""

EVAL_ONLY_INSTRUCTION = """你是检索评估器。对每个 focus，评估哪些 chunks 能回答它，然后选出综合 top_k。

用户消息里会提供：
- focuses_block: 检索维度列表（每个 focus 是一个用户问题维度）
- chunks_block: 按 focus 分组渲染的候选 chunks；每个 chunk 标注了 focuses=[...]（表示它属于哪些 focus）和 sq=...（表示它来自哪个 sub_query）
- source_top_k: top_k_chunk_ids 上限

## 核心评分原则
1. **回答能力优先**：评分必须围绕 "该 chunk 能否直接回答对应的 focus/sub_query"
   - critical: chunk 内容直接包含 focus 要求的答案（时间、地点、指标、主题都匹配）
   - important: chunk 内容部分相关，可作为辅助参考
   - noise: chunk 内容与 focus 无关或时空/主题不匹配
2. **时空/实体一致性**：特别关注 chunk 的时间范围、地区、产品型号、品牌是否与 focus/sub_query 一致
3. **信息密度**：优先选择包含具体数据（数字、表格）而非泛泛描述的 chunks

## 输出要求
- top_k_chunk_ids: 按相关度降序，最多 source_top_k 个
- tiers: 对每个选中的 chunk，给出 tier + relevance + answered_focuses
  - answered_focuses 必须严格填写该 chunk 实际能回答的 focus_id
  - 如果一个 chunk 只部分回答某个 focus（如只有 GMV 没有订单量），该 focus 不应出现在 answered_focuses 中
"""

# ============================================================================
# Builders
# ============================================================================

def build_notion_plan_only_agent() -> Optional[LlmAgent]:
    return LlmAgent(
        name="notion_plan_only",
        model=MODEL,
        instruction=PLAN_ONLY_INSTRUCTION,
        output_schema=PlanOnlyOutput,
    )

def build_notion_eval_only_agent(source_top_k: int) -> LlmAgent:
    return LlmAgent(
        name="notion_eval_only",
        model=MODEL,
        instruction=EVAL_ONLY_INSTRUCTION,
        output_schema=build_eval_only_model(source_top_k),
    )

def build_notion_plan_eval_agent(source_top_k: int) -> Optional[LlmAgent]:
    return LlmAgent(
        name="notion_plan_eval",
        model=MODEL,
        instruction=PLAN_EVAL_INSTRUCTION,
        output_schema=build_plan_eval_model(source_top_k),
    )
