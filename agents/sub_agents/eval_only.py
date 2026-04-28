"""eval_only sub_agent——末轮 / 单 loop 收尾：打 tier + 选 top_k_chunk_ids。

输入（运行时拼入 prompt）：
- focuses
- chunks: 本源累积的 chunk 列表（id + content 摘要 + target_focuses）
- source_top_k: top_k_chunk_ids 上限（已通过 pydantic ``max_length`` 硬约束）

输出 pydantic ``EvalOnlyOutputK{N}``：
- ``{"top_k_chunk_ids": [...], "tiers": {chunk_id: "critical|important|noise"}}``

Tier 口径提示给 LLM（非严格）：
- critical: 直接回答 focus 的核心事实 / 关键数据（约 >0.8 相关度）
- important: 提供上下文 / 支撑证据（约 0.5-0.8）
- noise: 弱相关 / 重复 / 偏离主题（<0.5）
"""

from google.adk.agents.llm_agent import LlmAgent

from tools.core.llm_contracts import MODEL, build_eval_only_model


_INSTRUCTION = """你是检索评估器。对给定 chunks 打分并选出 top_k。

用户消息里会提供：
- focuses_block: 检索维度列表（每个 focus 有 focus_id 和说明）
- chunks_block: 候选 chunks，**按 focus 分组呈现**（每组标题是 focus_id）；
  每个 chunk 含 id / target_focuses（召回层命中的 focus）/ score / 文本摘要
- source_top_k: top_k_chunk_ids 的数量上限

## 评分维度（对每个入选的 chunk 都要给）
1. **tier**（粗分类）
   - critical: 直接回答某个 focus 的核心事实 / 关键数据
   - important: 提供背景 / 支撑证据
   - noise: 弱相关 / 重复 / 偏题（入选 top_k 也可能是 noise，说明总体质量不高）
2. **relevance**（[0, 1] 连续分）：同 tier 内细分用
   - critical 通常 0.80-1.00
   - important 通常 0.50-0.80
   - noise 通常 0.0-0.50
   - 同一轮评估里，跨 focus 的 relevance 要**对齐同一把尺**（不要因 focus 切换而漂移）
3. **answered_focuses**（list[focus_id]）：该 chunk 实际回答了哪些 focus
   - 可以跨组（比如分到 focus_1 组的 chunk 也可能回答了 focus_2）
   - 可以为空（一个都不答 → 通常就是 noise）
   - 取值必须是 focuses_block 里出现过的 focus_id

## 输出要求（严格 JSON）
- top_k_chunk_ids: 至多 source_top_k 个 chunk_id，按 (tier, relevance) 综合降序
- tiers: list，每项形如
  {"chunk_id": "...", "tier": "critical|important|noise",
   "relevance": 0.xx, "answered_focuses": ["focus_0", ...]}
  对 top_k_chunk_ids 中每个 id 都要给一条完整赋值
- 不要输出 schema 以外的解释性文本
"""


def build_eval_only_agent(source_top_k: int, name: str = "eval_only") -> LlmAgent:
    """工厂：返回按 source_top_k 定制的 eval_only LlmAgent。"""
    return LlmAgent(
        name=name,
        model=MODEL,
        description="末轮评估：打 tier + 选 top_k",
        instruction=_INSTRUCTION,
        output_schema=build_eval_only_model(source_top_k),
    )
