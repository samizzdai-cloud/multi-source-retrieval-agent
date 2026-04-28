"""v1 新架构的 LLM 契约——统一模型常量 + 3 份解耦模板的 pydantic 输出 schema。

使用者：
- ``agents/sub_agents/plan_only.py``
- ``agents/sub_agents/plan_eval.py``
- ``agents/sub_agents/eval_only.py``
- ``agents/sub_agents/eval_layer.py``（全局可选评估）

设计说明：
- 模型常量 ``MODEL`` 放这里而非 ``llm_client.py``，是为了不破坏旧代码对
  ``LLM_MODEL`` env 变量的依赖（DRY：旧路径从 env 读，新路径从这里读）。
- ADK ``LlmAgent.output_schema`` 要求 ``type[BaseModel]``；``maxItems`` 通过
  ``Field(max_length=source_top_k)`` 在动态类工厂里注入，对应 Gemini
  ``response_schema.maxItems``。
"""

from typing import Dict, List, Literal, Optional, Type

from pydantic import BaseModel, Field, create_model


# ── 全局统一模型 ─────────────────────────────────────────────────────────────
MODEL = "gemini-3.1-flash-lite-preview"


# ── 共用字面量 ──────────────────────────────────────────────────────────────
Tier = Literal["critical", "important", "noise"]
Decision = Literal["continue", "stop"]


# 注：避免 ``ConfigDict(extra="forbid")`` / ``additionalProperties=false``，
# 因为 Gemini ``response_schema`` 不接受这些字段（会报 Invalid JSON payload）。


# ── 原子元素 ────────────────────────────────────────────────────────────────
class SubQueryItem(BaseModel):
    """plan_only / plan_eval 产出的 sub_query 元素（v2：支持源特有参数）。

    ``tags`` / ``indexes`` / ``time_filter`` / ``query_type`` 均可选：
    - bailian 的 plan_only 会填 ``tags``（L2 业务分类 + 时效 tag）+ ``indexes``（知识库名）
    - feishu 的 plan_only 会填 ``time_filter``（"last_N_days" 或 None）+ ``query_type``
      （"structured"/"concept"，驱动 Feishu doc reader 路由与召回量分化）
    - google / notion 保持默认空
    """
    query: str = Field(..., description="子查询原文")
    target_focus: str = Field(..., description="该 sub_query 回答的 focus_id")
    tags: List[str] = Field(
        default_factory=list,
        description="bailian 专用：L2 业务 tag + 时效 tag；其他源留空",
    )
    indexes: List[str] = Field(
        default_factory=list,
        description="bailian 专用：目标知识库名；其他源留空",
    )
    time_filter: Optional[str] = Field(
        default=None,
        description="feishu 专用：'last_N_days' 或 None；其他源留空",
    )
    query_type: Optional[Literal["structured", "concept"]] = Field(
        default=None,
        description="feishu 专用：structured=结构化/可核对事实载体，concept=概念/背景/策略；"
                    "其他源留空（None）",
    )


class TierAssignment(BaseModel):
    """单条 chunk 的 LLM 评估赋值。

    用 ``List[TierAssignment]`` 而不是 ``Dict[str, ...]``，
    因为 Gemini ``response_schema`` 不支持 ``additionalProperties``。

    Phase 4b 新增：
    - ``relevance``：LLM 直接给的 [0, 1] 连续分，取代 retrieve 原生 score。
    - ``answered_focuses``：LLM 判断该 chunk 实际回答了哪些 focus_id
      （可为空 / 多个；用于 eval_layer 的跨 focus 覆盖奖励）。
    """
    chunk_id: str = Field(..., description="chunk 唯一 id")
    tier: Tier = Field(..., description="critical / important / noise")
    relevance: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="LLM 给该 chunk 的 [0,1] 连续相关度分；同 tier 内二级排序用",
    )
    answered_focuses: List[str] = Field(
        default_factory=list,
        description="该 chunk 实际回答了哪些 focus_id（LLM 视角，可跨 focus）",
    )


def tiers_list_to_dict(tiers: Optional[List[dict]]) -> Dict[str, str]:
    """把 ``[{chunk_id, tier, ...}, ...]`` 归一成 ``{chunk_id: tier}``（兼容旧调用）。"""
    out: Dict[str, str] = {}
    for item in tiers or []:
        cid = item.get("chunk_id")
        tier = item.get("tier")
        if cid and tier:
            out[cid] = tier
    return out


def tiers_list_to_map(tiers: Optional[List[dict]]) -> Dict[str, Dict]:
    """把 tiers 归一成 ``{chunk_id: {tier, relevance, answered_focuses}}``。

    用于 Phase 4b eval_layer：把完整 LLM 评估结果回写到 ChunkEntry。
    容忍缺字段（老 LLM 响应只给 tier）。
    """
    out: Dict[str, Dict] = {}
    for item in tiers or []:
        cid = item.get("chunk_id")
        if not cid:
            continue
        out[cid] = {
            "tier": item.get("tier"),
            "relevance": float(item.get("relevance", 0.5) or 0.5),
            "answered_focuses": list(item.get("answered_focuses") or []),
        }
    return out


# ── plan_only 输出 ───────────────────────────────────────────────────────────
class PlanOnlyOutput(BaseModel):
    """首轮规划输出：focus → sub_queries。"""
    sub_queries: List[SubQueryItem] = Field(
        ..., description="按 focus 拆解出的子查询列表"
    )


# ── eval_only / plan_eval 动态 schema 工厂 ──────────────────────────────────
def build_eval_only_model(source_top_k: int) -> Type[BaseModel]:
    """返回一个按 ``source_top_k`` 约束 ``top_k_chunk_ids`` 长度的 pydantic 类。"""
    return create_model(  # type: ignore[call-overload]
        f"EvalOnlyOutputK{source_top_k}",
        top_k_chunk_ids=(
            List[str],
            Field(..., max_length=source_top_k,
                  description="按相关度降序的 chunk_id 列表"),
        ),
        tiers=(
            List[TierAssignment],
            Field(..., description="对 top_k_chunk_ids 中每个 id 的 tier 赋值"),
        ),
    )


def build_plan_eval_model(source_top_k: int) -> Type[BaseModel]:
    """返回 plan_eval 输出的 pydantic 类。decision 必填，其余按 decision 校验（见 validate_plan_eval_output）。"""
    return create_model(  # type: ignore[call-overload]
        f"PlanEvalOutputK{source_top_k}",
        decision=(
            Decision,
            Field(..., description="continue=继续 replan，stop=终止并给 top_k"),
        ),
        replan_queries=(
            Optional[List[SubQueryItem]],
            Field(default=None, description="decision=continue 时必填"),
        ),
        top_k_chunk_ids=(
            Optional[List[str]],
            Field(default=None, max_length=source_top_k,
                  description="decision=stop 时必填，按相关度降序"),
        ),
        tiers=(
            Optional[List[TierAssignment]],
            Field(default=None, description="decision=stop 时必填；对 top_k 每个 id 的 tier 赋值"),
        ),
    )


# ── 运行时校验（pydantic 已覆盖大部分，这里只兜底 decision 分支） ────────────
def validate_plan_eval_output(payload: dict) -> dict:
    """按 decision 校验互斥字段：
    - continue → 必须有 replan_queries
    - stop → 必须有 top_k_chunk_ids + tiers

    不符时将 decision 翻转为 stop（若有 top_k）或返回空兜底。
    """
    decision = payload.get("decision")
    if decision == "continue":
        if not payload.get("replan_queries"):
            # 无法 replan 又没给 top_k → 硬性 stop 空结果（外层会走 eval_only 兜底）
            payload["decision"] = "stop"
            payload["top_k_chunk_ids"] = payload.get("top_k_chunk_ids") or []
            payload["tiers"] = payload.get("tiers") or []
    elif decision == "stop":
        payload["top_k_chunk_ids"] = payload.get("top_k_chunk_ids") or []
        payload["tiers"] = payload.get("tiers") or []
    return payload


# ── loop 控制（与 spec v1 §2.3 对齐） ────────────────────────────────────────
def needs_eval(loop_n: int, min_loops: int, max_loops: int) -> bool:
    """是否需要进入 plan_eval（而不是直接走 eval_only 收尾）。

    单轮场景 ``min=max=1``：首轮即最后一轮，直接 eval_only → 返回 False
    多轮场景：只要 ``max_loops > 1``，中间轮都要 plan_eval 来决定 continue/stop。
    """
    if min_loops == 1 and max_loops == 1:
        return False
    return True


def enforce_loop_bounds(
    decision: str,
    loop_n: int,
    min_loops: int,
    max_loops: int,
) -> str:
    """对 LLM 给出的 decision 加硬约束。

    - ``loop_n < min_loops`` 时强制 continue（即便 LLM 说 stop）
    - ``loop_n >= max_loops`` 时强制 stop（即便 LLM 说 continue）
    - 其余尊重 LLM 判断
    """
    if loop_n < min_loops:
        return "continue"
    if loop_n >= max_loops:
        return "stop"
    return decision if decision in ("continue", "stop") else "stop"
