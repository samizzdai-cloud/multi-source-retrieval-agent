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

import logging
from typing import Any, Dict, List, Literal, Mapping, Optional, Type

from google.genai import types as _genai_types

logger = logging.getLogger(__name__)
from pydantic import BaseModel, Field, create_model


# ── 全局统一模型 ─────────────────────────────────────────────────────────────
# 这一行被 source_agent 的 plan_only / eval_only / eval_layer LlmAgent 共用。
# 由 retrieve_agent.build_retrieve_agent 在装配前调 set_model(cfg.eval_model_override)
# 注入运行时覆盖。
MODEL = "deepseek/deepseek-v4-flash"
_DEFAULT_MODEL = MODEL  # 不可变的原始默认；reset 时使用

# ── Thinking 配置 ─────────────────────────────────────────────────────────
# 由 retrieve_agent 在装配前调 set_thinking_config(...) 注入。
# DeepSeek 模型通过 LiteLlm(thinking={...}) 传递；Gemini 模型自动忽略。
_THINKING_CONFIG: Optional[dict] = None


def set_model(model: str = "") -> None:
    """运行时注入 eval-stage LLM 模型（source_agent eval_only / plan_only /
    plan_eval + final_assembler）。空串视为 reset 回 _DEFAULT_MODEL。

    必须在 SourceAgent / LlmAgent 实例化**之前**调用——LlmAgent 在
    __init__ 时把 model 字段烘焙进实例，构造后不再受 MODEL 全局变化影响。

    调用方：retrieve_agent.build_retrieve_agent
    驱动字段：RetrieveConfig.eval_model_override（← AgentConfig.eval_model）
    """
    global MODEL
    MODEL = model or _DEFAULT_MODEL


def set_thinking_config(config: Optional[dict] = None) -> None:
    """注入 DeepSeek thinking 配置。None 表示清除。"""
    global _THINKING_CONFIG
    _THINKING_CONFIG = config


def build_llm_model() -> Any:
    """构建当前配置下的模型实例（LiteLlm 或字符串）。

    若 MODEL 以 'deepseek/' 开头，返回配置了 thinking 和 provider 的 LiteLlm 实例。
    必须显式传 custom_llm_provider="deepseek"——ADK 的 LLMRegistry 解析后会把
    模型名前缀去掉，导致 litellm 无法识别 provider。
    """
    if MODEL.startswith("deepseek/"):
        try:
            from google.adk.models.lite_llm import LiteLlm
            actual_model = MODEL.split("/", 1)[1] if "/" in MODEL else MODEL
            extra: dict = {"custom_llm_provider": "deepseek"}
            if _THINKING_CONFIG:
                extra.update(_THINKING_CONFIG)
            return LiteLlm(model=actual_model, **extra)
        except ImportError:
            pass
    return MODEL


# ── DeepSeek response_format 适配 ───────────────────────────────────────
# ADK 的 LiteLlm._to_litellm_response_format 对非 Gemini 模型输出
# {"type": "json_schema", "json_schema": {...}}，但 DeepSeek 只支持
# {"type": "json_object"}。此处 monkey-patch 该函数，对 DeepSeek 模型降级。

_patched_response_format = False


def _ensure_response_format_patch() -> None:
    """Monkey-patch ADK LiteLlm 的 response_format 生成逻辑。

    DeepSeek API 只支持 ``response_format={"type": "json_object"}``，
    不支持 OpenAI 的 ``json_schema`` 格式。ADK 对非 Gemini 模型默认输出
    json_schema 会导致 400 错误。

    此 patch 在 build_retrieve_agent 阶段调用一次（幂等）。
    """
    global _patched_response_format
    if _patched_response_format:
        return

    try:
        from google.adk.models import lite_llm as _llm_module
        _original = _llm_module._to_litellm_response_format

        def _patched_to_litellm_response_format(
            response_schema: Any, model: str,
        ) -> Optional[dict]:
            result = _original(response_schema, model)
            if result is None:
                return None
            # DeepSeek 模型: 降级 json_schema → json_object
            if "deepseek" in str(model).lower():
                if result.get("type") == "json_schema":
                    return {"type": "json_object"}
            return result

        _llm_module._to_litellm_response_format = _patched_to_litellm_response_format
        _patched_response_format = True
        logger.debug("llm_contracts: patched _to_litellm_response_format for DeepSeek")
    except ImportError:
        pass

# ── LlmAgent generate_content_config ────────────────────────────────────────
# JSON mode does not make output unbounded.  A full judgment list can be much
# larger than top-K when a source has accumulated many previews; leave enough
# room for every active chunk's compact TierAssignment instead of recovering a
# truncated top_k-only response.
AGENT_CFG_EVAL_ONLY = _genai_types.GenerateContentConfig(max_output_tokens=16384)
AGENT_CFG_PLAN_ONLY = _genai_types.GenerateContentConfig(max_output_tokens=4096)
AGENT_CFG_PLAN_EVAL = _genai_types.GenerateContentConfig(max_output_tokens=16384)
# Partial eval must classify every supplied Focus/chunk pair.  Do not impose a
# product-level output-token cap here: a cap turns a complete classification
# into an indistinguishable truncated/empty response.  Provider-side hard
# limits still apply and are handled as an explicit contract failure.
AGENT_CFG_PARTIAL_EVAL = _genai_types.GenerateContentConfig()


# ── multi-focus 动态预算常量 ─────────────────────────────────────────────────
# eval_only / plan_eval 的 ``top_k_chunk_ids`` schema cap = base + 此常量。
# 运行时 ``effective_top_k = base + (n_focuses - 1) * 2``，schema 永远宽于
# effective（让 LLM 自由产出，最后由代码层硬截到 effective）。
# 选 10：覆盖 base=5 + 最多 6 focuses（=5+(6-1)*2=15）的极端情况。
MULTI_FOCUS_TOP_K_EXTRA = 10


def effective_source_top_k(base_top_k: int, n_focuses: int) -> int:
    """运行时算 effective top_k：base + (n_focuses - 1) * 2，单 focus 维持 base。

    样例（base=5）：1→5, 2→7, 3→9, 4→11, 5→13, 6→15。
    multi-focus 路由到同一源时，给 eval_only 留足够预算让每个 focus 至少能保 1 条。
    """
    if n_focuses <= 1:
        return base_top_k
    return base_top_k + (n_focuses - 1) * 2


# ── 共用字面量 ──────────────────────────────────────────────────────────────
Tier = Literal["critical", "important", "noise"]
PartialTier = Literal["critical", "important"]
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

## 多 focus 全面评估（multi-focus eval coverage）

当 focuses_block 含 **2 个或更多 focuses** 时（本源被 router 派活 ≥2 个 focus）：

- **必须对每个 focus 的候选都做评估**，不允许整段忽略某个 focus 的 chunks。
- 评估完成后，``top_k_chunk_ids`` 应该体现"我已经看过所有 focus"的态度：
  · 哪怕某 focus 候选整体偏弱、你判定它们都只是 noise / 边缘相关，**也应把该
    focus 的最强候选 1-2 条放进 top_k 并如实评为 noise**，让下游能看到"已查过、确无强证据"，
    而不是把该 focus 从输出里完全删掉。
  · 强 focus 自然会拿到更多 critical/important 名额，这是合理的；但弱 focus 也要
    在 top_k 里有"已评估"的脚印。
- **tier 必须客观真实**：
  · 不要为了让某 focus "看起来有覆盖" 就把 noise 强行升级到 important。
  · critical / important / noise 仅由【A】回答完整度 +【B】时间符合性 +【C】角度对齐
    决定，与 focus 之间的分配预算无关。
  · 若一条 chunk 对应该 focus 实际只是 background，老老实实评 noise。
- ``answered_focuses`` 如实填：一条 chunk 同时回答多 focus 时全部列出；只命中其
  ``target_focus`` 主 focus 也只列那一个；连主 focus 都答不上则该字段为 ``[]``。
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


class FocusEvidenceLedger(BaseModel):
    """Compact, batch-local evidence accounting for later global plan_eval."""
    focus_id: str
    evidence_chunk_ids: List[str] = Field(default_factory=list)
    covered_aspects: List[str] = Field(default_factory=list)
    remaining_gap: str = ""


class PartialFocusTier(BaseModel):
    """One retained chunk's provisional tier for the enclosing Focus only.

    Partial evaluation is an admission filter: it emits only evidence that
    survives to merged plan_eval. Omitted submitted refs are unselected for
    that Focus, not a semantic noise verdict; an all-omitted Focus group must carry
    ``reason=no_qualifying_chunks``.
    """
    chunk_id: str
    tier: PartialTier


class PartialFocusGroup(BaseModel):
    """Focus-local retained evidence for one partial-eval batch.

    ``reason`` is required when no submitted chunk survives this Focus filter.
    It is deliberately attached to the Focus group rather than represented by
    a separate "uncovered focuses" output surface.
    """
    focus_id: str
    tiers: List[PartialFocusTier] = Field(default_factory=list)
    reason: str = ""


class FocusReplanDecision(BaseModel):
    """Per-focus plan-eval conclusion retained in the compact trace."""
    focus_id: str
    status: Literal["covered", "gap", "insufficient"]
    gap_type: Literal["none", "upstream_evidence_needed", "query_refinement"] = "none"
    reason: str = ""


def tiers_list_to_dict(tiers: Optional[List[dict]]) -> Dict[str, str]:
    """把 ``[{chunk_id, tier, ...}, ...]`` 归一成 ``{chunk_id: tier}``（兼容旧调用）。"""
    out: Dict[str, str] = {}
    for item in tiers or []:
        if not isinstance(item, Mapping):
            continue
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
        if not isinstance(item, Mapping):
            continue
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


def build_partial_eval_model() -> Type[BaseModel]:
    """Complete, Focus-grouped partial tiering; no shortlist or final top-K.

    Partial evaluation must not cap retained evidence. Each Focus group emits
    provisional ``critical`` / ``important`` refs; omitted submitted refs are
    unselected for that Focus. Only merged ``plan_eval`` owns final tier, coverage,
    gap, replan, and top-K.
    """
    return create_model(  # type: ignore[call-overload]
        "PartialFocusTierOutput",
        focus_groups=(
            List[PartialFocusGroup],
            Field(..., description="complete per-Focus provisional tier groups"),
        ),
    )


def build_plan_eval_model(source_top_k: int, sub_query_item_cls: Type[BaseModel]) -> Type[BaseModel]:
    """plan_eval 输出 schema 工厂（共享版，4 个源共用）。

    ``sub_query_item_cls`` 是各源自己定义的 SubQueryItem，描述 ``replan_queries``
    每条的字段集合（bailian/feishu/google/notion 各自有不同的可调字段）。
    """
    return create_model(  # type: ignore[call-overload]
        f"PlanEvalOutputK{source_top_k}",
        decision=(Decision, Field(..., description="continue=继续 replan，stop=终止并给 top_k")),
        replan_queries=(
            Optional[List[sub_query_item_cls]],  # type: ignore[valid-type]
            Field(default=None, description="decision=continue 时必填"),
        ),
        top_k_chunk_ids=(
            Optional[List[str]],
            Field(default=None, max_length=source_top_k, description="decision=stop 时必填"),
        ),
        tiers=(
            Optional[List[TierAssignment]],
            Field(default=None, description="all active preview chunks must be judged for both continue and stop"),
        ),
        focus_decisions=(
            List[FocusReplanDecision],
            Field(default_factory=list, description="per-focus coverage/gap decision for trace and replan routing"),
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
    normalized_decisions = []
    contract_normalizations = []
    upstream_phrases = (
        "上游数据", "上游证据", "原始数据", "结构化原始", "定量证据",
        "further upstream data", "upstream evidence", "raw data",
        "no chunks provide", "no evidence provides",
    )
    missing_markers = (
        "缺乏", "缺失", "不足", "无法评估", "没有", "未提供",
        "missing", "insufficient", "lack", "without",
    )
    evidence_markers = (
        "定量", "销量", "转化率", "广告", "热力图", "漏斗", "库存", "渠道",
        "quantitative", "sales", "conversion", "ads", "heatmap", "funnel",
        "inventory", "channel", "performance data", "impact evidence",
    )
    for raw in payload.get("focus_decisions") or []:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        status = str(item.get("status") or "").strip().lower()
        raw_status = status
        if status in {"partially_covered", "partial", "evidence_gap"}:
            item["status"] = "gap"
        elif status not in {"covered", "gap", "insufficient"}:
            item["status"] = "insufficient"
        gap_type = str(item.get("gap_type") or "none").strip().lower()
        raw_gap_type = gap_type
        reason = str(item.get("reason") or "").casefold()
        explicit_upstream_gap = (
            any(marker.casefold() in reason for marker in upstream_phrases)
            or (
                any(marker.casefold() in reason for marker in missing_markers)
                and any(marker.casefold() in reason for marker in evidence_markers)
            )
        )
        if item["status"] == "covered" and explicit_upstream_gap:
            item["status"] = "gap"
        if gap_type not in {"none", "upstream_evidence_needed", "query_refinement"}:
            gap_type = (
                "upstream_evidence_needed"
                if explicit_upstream_gap
                else "query_refinement"
            )
        if item["status"] != "covered" and gap_type == "none" and explicit_upstream_gap:
            gap_type = "upstream_evidence_needed"
        item["gap_type"] = gap_type
        if raw_status != item["status"] or raw_gap_type != gap_type:
            contract_normalizations.append({
                "focus_id": str(item.get("focus_id") or ""),
                "raw_status": raw_status,
                "normalized_status": item["status"],
                "raw_gap_type": raw_gap_type,
                "normalized_gap_type": gap_type,
                "reason": "provider_enum_or_semantic_contradiction",
            })
        normalized_decisions.append(item)
    payload["focus_decisions"] = normalized_decisions
    payload["contract_normalizations"] = contract_normalizations
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
