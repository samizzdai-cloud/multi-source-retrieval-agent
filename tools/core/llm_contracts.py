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
MODEL = "gemini-3.1-flash-lite"


# ── 共用字面量 ──────────────────────────────────────────────────────────────
Tier = Literal["critical", "important", "noise"]
Decision = Literal["continue", "stop"]


# ── 共享 eval rubric（P2）──────────────────────────────────────────────
# 所有 SourceAgent 的 eval_only / plan_eval prompt 必须 include 此 block，
# 保证跨源 tier / angle_tag 标准统一。
EVAL_RUBRIC_BLOCK = """\
## 综合判 tier 维度（按以下 4 项加权决定 critical / important / noise）

[A] 回答完整度
  - 含决定性事实/数字/结论 → 倾向 critical
  - 部分回答或概括 → 倾向 important
  - 只是相关背景 → important 或 noise

[B] 时间符合性（仅当 query 含明显时间窗 / temporal_filter 启用）
  - 文档/邮件时间在窗内 → 维持原 tier
  - 过期但仍可作背景 → 降一级
  - 严重过期 → noise

[C] 角度对齐（chunk 是否回答了 focus 真正想问的角度）
  - on_target 直接回答 focus 核心问题 → 维持 tier
  - adjacent 相关但角度偏 → 降一级
  - off_target 偏题 → noise

[D] 角度标记 angle_tag（必须输出）
  用 1-2 个英文小写词概括该 chunk 回答的角度
  例: "pricing" / "timeline" / "competitor" / "user-feedback" / "spec-detail" / "policy"
  同 focus 内不同 chunk 的 angle_tag 越分散越好（多视角覆盖）；
  无法判断时填 "general"。

## 时空/实体一致性硬约束
特别关注 chunk 的时间范围、地区、产品型号、品牌是否与 focus 一致；
不一致直接判 noise，无论文本相似度多高。

## 信息密度偏好
优先选择包含具体数据（数字、表格、明确事实）而非泛泛描述的 chunks。
"""


# 注：避免 ``ConfigDict(extra="forbid")`` / ``additionalProperties=false``，
# 因为 Gemini ``response_schema`` 不接受这些字段（会报 Invalid JSON payload）。


# ── 原子元素 ────────────────────────────────────────────────────────────────

class TierAssignment(BaseModel):
    """单条 chunk 的 LLM 评估赋值。

    用 ``List[TierAssignment]`` 而不是 ``Dict[str, ...]``，
    因为 Gemini ``response_schema`` 不支持 ``additionalProperties``。

    Phase 4b 新增：
    - ``relevance``：LLM 直接给的 [0, 1] 连续分，取代 retrieve 原生 score。
    - ``answered_focuses``：LLM 判断该 chunk 实际回答了哪些 focus_id
      （可为空 / 多个；用于 eval_layer 的跨 focus 覆盖奖励）。

    P2 新增：
    - ``angle_tag``：LLM 标注该 chunk 回答 focus 的角度（1-2 个英文小写词，
      如 "pricing" / "timeline" / "competitor"）。同 focus 内多 chunk 同 angle_tag
      时，装填器会按 relevance 降级冗余条目，提升角度多样性。
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
    angle_tag: str = Field(
        default="",
        description="该 chunk 回答 focus 的角度，1-2 个英文小写词；用于同角度去重",
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
            "angle_tag": str(item.get("angle_tag") or "").strip().lower(),
        }
    return out


# ── eval_only 动态 schema 工厂 ──────────────────────────────────
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


# ── final_assembler LLM schema（P3）────────────────────────────────────
class FinalAssemblerOutput(BaseModel):
    """LLM eval_layer 输出：仅含 chunk_id 索引，schema 与最终 chunk schema 解耦。

    selections 用 List 而非 Dict，因 Gemini response_schema 不支持 additionalProperties。
    每项含 (focus_id, chunk_ids)。
    """
    selections: List["FinalSelectionEntry"] = Field(
        ..., description="每个 focus 的选中 chunk_id 列表（保持 focus 顺序）",
    )
    rationale: str = Field(
        default="", description="一句话说明本次选择关键考量",
    )


class FinalSelectionEntry(BaseModel):
    focus_id: str = Field(..., description="focus 标识")
    chunk_ids: List[str] = Field(
        default_factory=list, description="该 focus 选中的 chunk_id 列表",
    )


FinalAssemblerOutput.model_rebuild()


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
