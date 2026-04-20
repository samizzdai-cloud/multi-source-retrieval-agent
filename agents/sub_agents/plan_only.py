"""plan_only sub_agent——首轮规划：focus → sub_queries。

输入（由 SourceAgent 在运行时填入 prompt 模板，Phase 0 仅骨架）：
- focuses: ``[{focus_id, text}, ...]``
- source_profile: ``SourceProfile.to_prompt_block()``
- source_top_k: 本源最终产出上限

输出（pydantic ``PlanOnlyOutput``）：
- ``{"sub_queries": [{"query", "target_focus"}, ...]}``
"""

from google.adk.agents.llm_agent import LlmAgent

from tools.llm_contracts import MODEL, PlanOnlyOutput


_INSTRUCTION = """你是检索规划器。针对给定的 source，按 focuses 拆解 sub_queries，
并根据源专业规则自主选择该 sub_query 需要的 tags / indexes / time_filter。

用户消息里会提供：
- focuses_block: 检索维度列表（focus_id + 描述）
- source_profile_block: 目标检索源的能力画像（strengths / weaknesses / sub_query_hints）
- source_top_k: 该源最终产出 chunks 上限，用于估算 sub_query 数量
- source_query_rules_block（可选）: 该源的 query 编写规则、tag/index/time 约束、枚举值等——
  如果存在，必须严格遵守其中的所有硬约束。

## 输出要求（严格 JSON）
- 每个 focus 至少覆盖 1 条 sub_query，最多 3 条
- sub_queries 总数不超过 max(3, source_top_k)
- 每条 sub_query 必填：
    · query: 按 source 风格写好的检索串（见 source_query_rules_block）
    · target_focus: 该 sub_query 服务的 focus_id
- 每条 sub_query 选填（按源规则决定是否填写）：
    · tags: 百炼专用业务/时效标签；其他源留 []
    · indexes: 百炼专用知识库索引；其他源留 []
    · time_filter: 飞书专用相对时间串（如 "last_30_days"）；其他源留 null
- 不要输出额外说明，只输出 JSON

输出示例（百炼源）：
{"sub_queries": [
  {"query": "nubia UK/英国/United Kingdom 2026年3月份 DTC 销售额和GMV表现",
   "target_focus": "focus_0", "tags": ["DTC", "2026_Q1"], "indexes": ["nubia"], "time_filter": null},
  {"query": "nubia Marketplace 英国 转化率", "target_focus": "focus_1",
   "tags": ["Marketplace"], "indexes": ["nubia"], "time_filter": null}
]}

输出示例（飞书源）：
{"sub_queries": [
  {"query": "nubia 英国 销售", "target_focus": "focus_0",
   "tags": [], "indexes": [], "time_filter": null},
  {"query": "nubia 策略", "target_focus": "focus_1",
   "tags": [], "indexes": [], "time_filter": "last_30_days"}
]}
"""


def build_plan_only_agent(name: str = "plan_only") -> LlmAgent:
    """工厂：返回配置好 output_schema 的 plan_only LlmAgent。"""
    return LlmAgent(
        name=name,
        model=MODEL,
        description="首轮规划：focus → sub_queries",
        instruction=_INSTRUCTION,
        output_schema=PlanOnlyOutput,
    )
