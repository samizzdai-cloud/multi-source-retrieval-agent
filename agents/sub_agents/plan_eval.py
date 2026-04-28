"""plan_eval sub_agent——多 loop 中间轮：评估 + (continue → replan / stop → top_k)。

输入（运行时拼入 prompt）：
- focuses
- chunks: 累积 chunks
- loop_info: ``{loop_n, min, max}``
- source_top_k

输出 pydantic ``PlanEvalOutputK{N}``：
- decision="continue": 使用 replan_queries
- decision="stop": 使用 top_k_chunk_ids + tiers

代码侧 ``enforce_loop_bounds`` 会对 decision 再做硬约束（min/max 边界）。
"""

from google.adk.agents.llm_agent import LlmAgent

from tools.core.llm_contracts import MODEL, build_plan_eval_model


_INSTRUCTION = """你是检索评估与规划器。判断是否继续 replan，若停止则给出 top_k。

用户消息里会提供：
- focuses_block: 检索维度列表
- chunks_block: 累积到现在的候选 chunks（含 id / target_focuses / score / 文本摘要）
- loop_info: loop_n + min_loops + max_loops
- source_top_k: top_k_chunk_ids 的数量上限

## 决策规则
- 若已有 chunks 足够覆盖所有 focus（每个 focus ≥1 条 critical 或 ≥2 条 important）
  → decision="stop"，给出 top_k_chunk_ids + tiers
- 否则
  → decision="continue"，给出 replan_queries（针对未满足的 focus 发新 sub_query）

## 边界（代码侧还会二次强制，但你应当尽量自觉遵守）
- loop_n < min_loops 时倾向 continue
- loop_n >= max_loops 时倾向 stop

## 评分维度（decision=stop 时对 top_k 每个 chunk 都要给）
1. tier: critical / important / noise（见 eval_only 定义）
2. relevance: [0, 1] 连续分，同 tier 内细分（critical 0.80-1.00 / important 0.50-0.80 / noise ≤0.50）；
   跨 focus 对齐同一把尺。
3. answered_focuses: 该 chunk 实际回答了哪些 focus_id（可跨组 / 可为空）。

## 输出要求（严格 JSON）
- decision 必填
- decision=continue 时 replan_queries 必填（≥1 条，≤3 条）
- decision=stop 时 top_k_chunk_ids 必填；tiers 为 list，每项形如
  {"chunk_id": "...", "tier": "critical|important|noise",
   "relevance": 0.xx, "answered_focuses": ["focus_0", ...]}
  对 top_k 里每个 id 都赋一条完整记录
"""


def build_plan_eval_agent(source_top_k: int, name: str = "plan_eval") -> LlmAgent:
    """工厂：返回按 source_top_k 定制的 plan_eval LlmAgent。"""
    return LlmAgent(
        name=name,
        model=MODEL,
        description="中间轮评估 + 可选 replan",
        instruction=_INSTRUCTION,
        output_schema=build_plan_eval_model(source_top_k),
    )
