"""统一数据模型 — 多源检索 Agent 的核心类型定义"""

import os
from dataclasses import dataclass, field, replace
from typing import Dict, List, Literal, Optional


@dataclass
class SourceConfig:
    """检索源凭证配置。优先使用字段值，为空时 fallback 到同名环境变量。

    设计意图：
    - 测试阶段：全部留空，自动从 .env 读取（默认行为）
    - 生产部署：上游 Agent 传入凭证，覆盖 env vars
    - 某个凭证为空 → 对应检索源不可用（不调用）
    """
    feishu_uat: str = ""
    bailian_index_map: str = ""           # "nubia:id1,hypershell:id2"
    bailian_workspace_id: str = ""
    alibaba_cloud_ak: str = ""
    alibaba_cloud_sk: str = ""
    dashscope_api_key: str = ""           # text-embedding-v4
    load_from_env: bool = True            # 开关：True 时空字段 fallback 到 env var

    def get(self, field_name: str, env_var: str) -> str:
        """获取配置值：字段非空 → 字段值；load_from_env=True → env var；否则空字符串。"""
        val = getattr(self, field_name, "")
        if val:
            return val
        if self.load_from_env:
            return os.getenv(env_var, "")
        return ""


@dataclass
class UnifiedChunk:
    """跨源统一的检索结果切片。每个 retrieve tool 必须返回此格式。"""
    text: str                          # chunk 原文切片
    source_name: str                   # 文件名/文档名
    source_url: str = ""               # 文档链接（无则空）
    source_tool: str = ""              # "feishu" | "bailian" | "google" | "notion"
    heading_path: str = ""             # 章节路径
    score: float = 0.0                 # 归一化 0.0-1.0（tool 内部负责归一化）
    confidence_tier: str = ""          # reranker 填充: "critical" | "important" | "noise"
    metadata: Dict = field(default_factory=dict)


@dataclass
class FocusGroup:
    """一组检索意图 + 源路由分配（v2 精简）。

    Global Planner 输出的最小单元：
    - focuses: 可评估的检索目标列表，每个 focus 描述一个维度/方面。
    - targets: 该 FocusGroup 应派发到的源列表（planner 已完成路由决策）。

    示例: query="nubia 3月销售" →
        FocusGroup(focuses=["nubia DTC渠道销售数据"], targets=["feishu","bailian"])
    """
    focuses: List[str] = field(default_factory=list)
    targets: List[str] = field(default_factory=list)


@dataclass
class QueryPlan:
    """Global Planner 的输出（v2 精简：只管语义拆解 + 源路由）。

    不再包含 time_filter / query_type / sub_queries / tags / indexes / required_targets
    ——这些由各 SourceAgent 自己的 plan_only LLM 产出。
    """
    original_query: str
    focus_groups: List[FocusGroup] = field(default_factory=list)


@dataclass
class RetrievalResult:
    """RetrievalAgent 的最终输出。"""
    query: str
    chunks: List[UnifiedChunk] = field(default_factory=list)
    source_stats: Dict[str, int] = field(default_factory=dict)
    total_sources_queried: int = 0
    total_chunks_before_filter: int = 0
    total_time_ms: int = 0
    plan: Optional[QueryPlan] = None
    focus_coverage: Optional[Dict[str, int]] = None  # focus → 命中 chunk 数


# ════════════════════════════════════════════════════════════════════════════
# v1 新架构契约（spec_plan.md v1）— 与旧类型并存，新 SourceAgent 使用这些类型
# ════════════════════════════════════════════════════════════════════════════

from typing import Literal, Set, Any

Tier = Literal["critical", "important", "noise"]
SourceTag = Literal["feishu", "bailian", "google", "notion"]


@dataclass
class ChunkEntry:
    """v1 SourceAgent 内部 / 跨源合并统一切片。

    与旧 ``UnifiedChunk`` 的差异：
    - ``id``: 稳定主键 ``f"{source}:{doc_id}:{block_id}"``，用于跨 loop / 跨源去重
    - ``target_focuses``: 该 chunk 回答的 focus_id 集合（可多命中）
    - ``tier``: 由 eval_only / plan_eval 打标，LLM 原生产出
    - ``sub_query``: 命中该 chunk 的 sub_query 原文（单条，多次命中时保留首次）
    """
    id: str
    source: str                              # SourceTag 值，保留 str 以便扩展
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    score: Optional[float] = None
    sub_query: str = ""
    target_focuses: Set[str] = field(default_factory=set)
    tier: Optional[str] = None               # Tier 值，未打标为 None

    def merge(self, other: "ChunkEntry") -> "ChunkEntry":
        """同 id 冲突时：合并 target_focuses / 取更高 score / 取更高 tier / 保留首个 sub_query。

        不可变式实现——返回新对象而非原地修改，避免并发写 chunk_index 时的竞争。
        tier 优先级：critical > important > noise > None。

        Phase 4b Step D（防评分 boost 策略）：
        - score 取 max（单调，多 loop 命中同一 chunk 不会累积放大）
        - tier 取更高级（多 loop 命中只升不降）
        - metadata["answered_focuses"] 做 **并集**（旧实现是 self 覆盖 other，会丢 focus 归属）
        """
        merged_focuses = set(self.target_focuses) | set(other.target_focuses)
        best_score = max(
            [s for s in (self.score, other.score) if s is not None] or [None]
        )
        # tier 取更高级（rank 小的胜出）
        _rank = {"critical": 0, "important": 1, "noise": 2, None: 3}
        best_tier = self.tier if _rank.get(self.tier, 3) <= _rank.get(other.tier, 3) else other.tier

        # metadata 合并：self 优先；但 answered_focuses 例外做并集
        merged_meta: Dict[str, Any] = {**(other.metadata or {}), **(self.metadata or {})}
        a1 = set((self.metadata or {}).get("answered_focuses") or [])
        a2 = set((other.metadata or {}).get("answered_focuses") or [])
        if a1 or a2:
            merged_meta["answered_focuses"] = sorted(a1 | a2)

        return ChunkEntry(
            id=self.id,
            source=self.source,
            content=self.content or other.content,
            metadata=merged_meta,
            score=best_score,
            sub_query=self.sub_query or other.sub_query,
            target_focuses=merged_focuses,
            tier=best_tier,
        )

    def with_tier(self, tier: Optional[str]) -> "ChunkEntry":
        """返回一个带 tier 标记的副本。"""
        if tier == self.tier:
            return self
        return ChunkEntry(
            id=self.id,
            source=self.source,
            content=self.content,
            metadata=dict(self.metadata),
            score=self.score,
            sub_query=self.sub_query,
            target_focuses=set(self.target_focuses),
            tier=tier,
        )

    def to_dict(self) -> Dict[str, Any]:
        """JSON 可序列化（set → list）。"""
        return {
            "id": self.id,
            "source": self.source,
            "content": self.content,
            "metadata": self.metadata,
            "score": self.score,
            "sub_query": self.sub_query,
            "target_focuses": sorted(self.target_focuses),
            "tier": self.tier,
        }


@dataclass
class SourceAgentConfig:
    """单个 SourceAgent 的运行参数。

    ``min_loops == max_loops == 1`` 时走单轮路径：plan_only → execute → eval_only。
    ``max_loops > 1`` 启用 plan_eval loop，``enforce_loop_bounds`` 保证 min/max 硬约束。
    """
    source_top_k: int = 5                  # 本源最终产出 chunks 上限
    min_loops: int = 1
    max_loops: int = 1
    source_timeout_s: float = 25.0         # SourceAgent 整体软超时
    sub_query_top_n: int = 8               # 每个 sub_query 原始召回上限


# ── SourceAgent 预设（Phase 4b）────────────────────────────────────
# 预设语义：
# - STRICT: 单轮、召回少、快速；适合 planner 路由明确的场景
# - LOOSE : 多轮 replan、召回多；适合 query 模糊需要 SourceAgent 自纠的场景
# 数值为 Phase 4b Step B 实验前的占位，实验后会回填最终值
STRICT_SOURCE_CFG = SourceAgentConfig(
    source_top_k=5,
    min_loops=1, max_loops=1,
    sub_query_top_n=8,
    source_timeout_s=25.0,
)

LOOSE_SOURCE_CFG = SourceAgentConfig(
    source_top_k=8,
    min_loops=1, max_loops=2,
    sub_query_top_n=12,
    source_timeout_s=40.0,
)


# ── eval_layer 模式（Phase 4b）────────────────────────────────────
EvalLayerMode = Literal["rule", "llm_tool"]


@dataclass
class RetrieveConfig:
    """retrieve_agent 顶层配置。"""
    enabled_sources: List[str] = field(default_factory=lambda: ["feishu", "bailian"])
    source_configs: Dict[str, SourceAgentConfig] = field(default_factory=dict)

    # ── SourceAgent 预设耦合（Phase 4b）──
    # 当 enabled_preset != None 时，effective_config_for 以预设为基底叠加 source_configs 覆写
    # 可取 "strict" / "loose" / None（后者 = 完全由 source_configs 决定，同旧行为）
    source_preset: Optional[Literal["strict", "loose"]] = None

    # ── eval_layer（Phase 4b）──
    eval_layer_enabled: bool = False
    eval_layer_mode: EvalLayerMode = "rule"
    eval_layer_top_k: int = 12             # 兜底上限；实际值由 min(n_focuses*3, CAP) 计算
    eval_layer_llm_timeout_s: float = 15.0 # LlmEvalLayer 单次 LLM 超时
    eval_layer_llm_max_input: int = 22     # LlmEvalLayer 直灌 LLM 的最大 chunk 数，超过则 rule 预筛

    model: str = "gemini-3.1-flash-lite-preview"   # 全局统一 LLM

    def config_for(self, source: str) -> SourceAgentConfig:
        """取某源配置，缺省返回默认 ``SourceAgentConfig()``（不叠加 preset，兼容旧调用）。"""
        return self.source_configs.get(source, SourceAgentConfig())

    def effective_config_for(self, source: str) -> SourceAgentConfig:
        """取某源的最终生效配置。

        优先级：``source_configs[source]`` 非默认字段 > ``source_preset`` > ``SourceAgentConfig()``。

        实现策略：用 preset 作为基底 dataclass，再用 source_configs[source] 里**与
        ``SourceAgentConfig()`` 默认值不同**的字段覆写。未传 source_configs 时返回纯 preset。
        """
        # 1) 基底 = preset 或 默认
        if self.source_preset == "strict":
            base = replace(STRICT_SOURCE_CFG)
        elif self.source_preset == "loose":
            base = replace(LOOSE_SOURCE_CFG)
        else:
            base = SourceAgentConfig()

        # 2) 覆写 = source_configs 里显式改过的字段
        override = self.source_configs.get(source)
        if override is None:
            return base

        defaults = SourceAgentConfig()
        diff: Dict[str, object] = {}
        for f in (
            "source_top_k", "min_loops", "max_loops",
            "source_timeout_s", "sub_query_top_n",
        ):
            if getattr(override, f) != getattr(defaults, f):
                diff[f] = getattr(override, f)
        return replace(base, **diff) if diff else base
