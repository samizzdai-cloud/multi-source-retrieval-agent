"""Feishu-specific LLM Configuration & Prompts."""

from typing import List, Literal, Optional, Type
from pydantic import BaseModel, Field, create_model

from google.adk.agents.llm_agent import LlmAgent
from tools.core import llm_contracts as _llm_contracts
from tools.core.llm_contracts import (
    build_eval_only_model, build_plan_eval_model,
    EVAL_RUBRIC_BLOCK, MULTI_FOCUS_TOP_K_EXTRA,
    AGENT_CFG_EVAL_ONLY, AGENT_CFG_PLAN_ONLY, AGENT_CFG_PLAN_EVAL,
)
from tools.core.source_query_config import ENTITY_FIDELITY_BLOCK

# ============================================================================
# Pydantic Schemas
# ============================================================================

class SubQueryItem(BaseModel):
    query: str = Field(..., description="围绕所属 Focus，将目标内容的可能表述转译为飞书关键词检索串，遵循源规则，保持业务对象与范围")
    target_focus: str = Field(..., description="该 sub_query 回答的 focus_id")
    time_filter: Optional[str] = Field(default=None, description="ISO 日期格式（如 '>2025-01-01'）或相对时间（如 'last_30_days'）")
    query_type: Optional[Literal["structured", "concept", "personal_recent"]] = Field(default=None, description="查询模式分类")
    author_filter: Optional[str] = Field(default=None, description="作者过滤，如'me'")

class PlanOnlyOutput(BaseModel):
    sub_queries: List[SubQueryItem] = Field(..., description="按 focus 拆解出的子查询列表")


# ============================================================================
# Prompts
# ============================================================================

PLAN_ONLY_INSTRUCTION = f"""你是检索规划器。保持给定 Focus 的业务含义稳定，根据目标内容的可能表述与 source 的检索能力生成可执行 sub_queries，并选择需要的参数。不是重拆 Focus 或摘抄关键词。

用户消息里会提供：
- focuses_block: 检索维度列表
- source_profile_block: 目标检索源的能力画像
- source_top_k: 该源最终产出 chunks 上限
- sub_query_total_cap: sub_queries 总数上限（**硬纪律**）
- source_query_rules_block: 该源的 query 编写规则
- queries_per_focus_limit: 每个 Focus 在本源的查询上限（如已配置）

{ENTITY_FIDELITY_BLOCK}

## 输出要求（严格 JSON）
- 每个 focus 至少覆盖 1 条 sub_query，最多 queries_per_focus_limit 条（未提供时遵从源规则和总预算）
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
- replan_queries_per_focus_limit: 每个 Focus 在本源下一轮的查询上限
- source_query_rules_block: 与初查共用的飞书查询规则，续查同样遵循

{ENTITY_FIDELITY_BLOCK}

## 决策规则
- 先核实证据的实际业务对象。检索 query、target_focus 和 Profile 名称只是路由上下文，不能证明正文属于该对象。其他品牌或产品线的数据，即使是唯一可用的实绩，也不能满足原对象的现状 Focus；组织归属或行业相似性不构成同一数据口径。
- 若用户没有要求跨品牌比较或借鉴，其他品牌实绩判为 noise，原 Focus 保持 gap/insufficient 并为它规划续查。禁止在 reason 承认“非目标品牌”后仍判 covered。
- 先为每个 focus 输出 focus_decisions：status、gap_type、reason。
- 每个现有 focus_id 恰好给一条判定。为同一 Focus 的各项要求分别检查证据；保留已支持部分要求的证据，在 reason 写清缺哪一项。
- attempted_queries_block 列出实际尝试过的查询、时间过滤和候选结果。结合证据判断每个 Focus；零候选且无其他可用证据时判为 insufficient。
- status 只允许 `covered | gap | insufficient`；部分覆盖必须写 `status="gap"`，禁止输出 `partially_covered` 或 `evidence_gap`。
- gap_type 只允许 `none | upstream_evidence_needed | query_refinement`；`covered` 必须配 `none`，其余状态不得配 `none`。
- gap_type 描述缺什么：缺用户要求的原始指标、报表等用 upstream_evidence_needed；召回偏题或需要调整词面用 query_refinement。可选补充信息的缺失只在 reason 说明。
- Focus 标有【必须项】时，按必须项联合验收；【可选补充】缺失不构成 gap，也不为它生成续查。必须项已满足就判 covered，不追加模型自行联想的指标或验证材料作为停止条件。无标记时按用户实际要求验收。
- 对每个未满足的 Focus，只要还有轮次与时间预算、且有合理的飞书检索方向，至少给一条未尝试过的 replan_queries。上游数据缺口也适用，例如搜飞书内的业务复盘或报表；需要表格时用 structured。
- 低召回、零候选或只有策划案只能说明本轮未找到实绩。若仍可用品牌/型号和所缺指标定向搜报表或复盘，给出续查；不得仅凭候选少就推断源内没有资料。同一批的多个子查询属于同一轮，以 loop_info 为准。
- 对照查询组的候选、已评估片段和上轮 Focus 判定，为未满足项优化飞书检索请求。保持 Focus 的对象、范围、时间和研究目标稳定，从业务理解、适用 Profile 和已读材料推断目标内容可能的表述，调整关键词或检索参数。不同查询可以定位同类证据；候选文档数不代表实绩证据数。
- 只见某型号的策划或排期时，当前核心型号仍待实绩确认；续查保留品牌层面的正文锚点，避免全部查询都锁定该策划中的型号。
- 每条续查填写 target_focus、query、query_type，按本源查询规则定位未满足项，不为适配平台而改写 Focus。
- 每个未满足 Focus 遵从 replan_queries_per_focus_limit；先写出可执行 query 列表，再核对 decision。
- 有续查 query 时 decision="continue"；全部满足、没有合理的飞书检索方向或预算已用完时 decision="stop"。未满足且停止时，在该 Focus 的 reason 写明缺什么和停止原因。
- `replan_queries` 每次必填。continue 必须非空；reason 中写“仍可续查”必须落实成这个数组里的 query。不要只说明继续意图却遗漏查询字段。
- “最新”按证据里的业务数据周期判断。文档修改时间只提供线索。
- 无论 continue 或 stop 都给出证据 judgments；已有合格证据应尽量为每个 Focus 保留一条代表证据。

{EVAL_RUBRIC_BLOCK}

## 输出契约
- decision=continue 时，给出可执行的 `replan_queries`，同时保留现有证据 judgments。通用结构为：
  `{{"decision":"continue","replan_queries":[{{"query":"原对象 正文锚点","target_focus":"focus_0","query_type":"structured","time_filter":null}}],"top_k_chunk_ids":[],"tiers":[{{"chunk_id":"C1","tier":"important","relevance":0.7,"answered_focuses":["focus_0"],"angle_tag":"current-state"}}],"focus_decisions":[{{"focus_id":"focus_0","status":"gap","gap_type":"upstream_evidence_needed","reason":"缺口及下一条查询要补的证据"}}]}}`。
- decision=stop 时，`replan_queries=[]`，并给出下面的最终选证据字段。
- top_k_chunk_ids 和 tiers[].chunk_id 只能使用 chunks_block 中的 chunk_ref（如 C1），不得输出 canonical chunk ID；按相关度降序。
- answered_focuses 标记该 chunk 直接支持的 Focus，即使只支持其中一个必需项；仅有背景或路由关联时留空。整个 Focus 是否满足由证据组合判断。
- 严格输出 JSON 对象，且 `tiers` 必须包含每个 active preview chunk 的 judgment：
  `{{"decision":"stop","replan_queries":[],"top_k_chunk_ids":["C1"],"tiers":[{{"chunk_id":"C1","tier":"important","relevance":0.8,"answered_focuses":["focus_0"],"angle_tag":"funnel"}}],"focus_decisions":[{{"focus_id":"focus_0","status":"covered","gap_type":"none","reason":"..."}}]}}`。
"""

EVAL_ONLY_INSTRUCTION = f"""你是检索评估器。对每个 focus，评估哪些 chunks 能回答它，然后选出综合 top_k。

用户消息里会提供：
- focuses_block: 检索维度列表
- chunks_block: 按 focus 分组渲染的候选 chunks；每个 chunk 标注了 chunk_ref=C1/C2/...、focuses=[...] 和 sq=...
- source_top_k: top_k_chunk_ids 上限

先核实正文的实际品牌、产品线和数据口径。检索 query、target_focus 和 Profile 名称只是路由上下文。
用户没有要求跨品牌比较或借鉴时，其他品牌实绩判为 noise；资料稀少或组织归属不能让它替代目标对象的数据。

{EVAL_RUBRIC_BLOCK}

## 输出要求
- top_k_chunk_ids 和 tiers[].chunk_id 只能使用 chunks_block 中的 chunk_ref（如 C1），不得输出 canonical chunk ID；按相关度降序。
- answered_focuses 标记该 chunk 直接支持的 Focus，即使只支持其中一个必需项；仅有背景或路由关联时留空。每个有合格证据的 Focus 优先保留一条代表证据。
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
        output_schema=build_plan_eval_model(source_top_k + MULTI_FOCUS_TOP_K_EXTRA, SubQueryItem, require_replan_field=True),
        generate_content_config=AGENT_CFG_PLAN_EVAL,
    )
