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
    bailian_allowed_indexes: str = ""     # "nubia,hypershell"，空时代表不限制，允许所有注册的知识库
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
class AgentConfig:
    """统一外部配置接口——调用方只需传这一个对象。

    将 RetrieveConfig（行为档位）+ SourceConfig（凭证）合并为单一入口，
    方便 API / SDK 调用方按需传入，不需要了解内部两层配置结构。

    用法示例：
        cfg = AgentConfig(
            preset="balanced",
            sources=["feishu", "bailian"],
            feishu_uat="t-xxx",
            bailian_allowed_indexes="nubia",
        )
        agent = build_retrieve_agent_from_config(cfg)
        result = await run_query(agent, "nubia 日本市场的KOL合作名单")
    """
    # ── 行为档位 ──────────────────────────────────────────────
    preset: str = "balanced"              # "lite" | "balanced" | "deep_dive"
    sources: List[str] = field(default_factory=lambda: ["feishu", "bailian"])

    # ── 飞书凭证 ──────────────────────────────────────────────
    feishu_uat: str = ""                  # 空时走 FEISHU_UAT 环境变量

    # ── 百炼凭证 ──────────────────────────────────────────────
    bailian_allowed_indexes: str = ""     # 允许查询的知识库白名单，如 "nubia,hypershell"，空时走全部

    def to_retrieve_config(self) -> "RetrieveConfig":
        """转换为 RetrieveConfig（行为配置）。"""
        return RetrieveConfig(
            preset=self.preset,           # type: ignore[arg-type]
            enabled_sources=list(self.sources),
        )

    def to_source_config(self) -> "SourceConfig":
        """转换为 SourceConfig（凭证配置）。"""
        return SourceConfig(
            feishu_uat=self.feishu_uat,
            bailian_allowed_indexes=self.bailian_allowed_indexes,
            load_from_env=True,           # 空字段始终 fallback 到环境变量
        )


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
class PlanFocus:
    """Global Planner 输出的单个 focus（扁平架构，v2.1）。

    v2.1 FG 下线后，每个 focus 独立携带自己的源路由 ``targets``。

    下游 ``retrieve_agent._prepare_focuses`` 会把 ``PlanFocus`` 映射到
    ``agents.source_agent_base.Focus``（补上 ``focus_id``）后再派发。
    """
    text: str
    targets: List[str] = field(default_factory=list)


@dataclass
class QueryPlan:
    """Global Planner 的输出（v2.1：FG 下线，flat focuses）。

    只管语义拆解 + 源路由（每个 focus 独立带 targets）。
    不再包含 time_filter / query_type / sub_queries / tags / indexes / required_targets
    ——这些由各 SourceAgent 自己的 plan_only LLM 产出。
    """
    original_query: str
    focuses: List[PlanFocus] = field(default_factory=list)


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
        for key in ("focus_ids", "sub_query_ids", "feishu_urls", "selected_query_types"):
            v1 = set((self.metadata or {}).get(key) or [])
            v2 = set((other.metadata or {}).get(key) or [])
            if v1 or v2:
                merged_meta[key] = sorted(v1 | v2)

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
    google_download_timeout_s: float = 12.0 # 非原生文件流式下载强制打断时间
    google_api_timeout_s: float = 12.0      # 原生文件单次 API 请求底层 Socket 超时
    google_enable_gmail: bool = False       # 是否开启 Gmail 检索
    google_enable_gmail_attachments: bool = False # 是否开启 Gmail 附件读取
    google_enable_sheet_filter: bool = False # 是否开启 Google Sheets LLM/Rerank 过滤
    google_enable_excel_filter: bool = False # 是否开启 Excel/CSV LLM/Rerank 过滤
    google_enable_fulltext_search: bool = True # 是否开启 Drive 全文检索（若为 False，则仅查 name）
    google_order_by_mode: Optional[str] = None # Drive API 排序，如 "modifiedTime desc", "viewedByMeTime desc"


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

# ── RetrieveConfig 一体化档位（Phase 4c）──────────────────────────
# 档位语义：
#   lite      : STRICT 基底 + eval_layer 关 + doc 全走 MCP（无 table）
#               极简快速路径，doc[:8] MCP-only，跳过 table 流水线
#   balanced  : STRICT 基底 + eval_layer 关 + doc block_api/mcp 混合 + table 标准
#               当前默认生产模式，质量与速度平衡（原 lite 逻辑）
#   deep_dive : LOOSE  基底 + llm_tool eval_layer + doc block_api/mcp 混合 + table 宽松
#               模糊 query + 多源噪声大
RetrievePreset = Literal["lite", "balanced", "deep_dive"]


@dataclass
class RetrieveConfig:
    """retrieve_agent 顶层配置。

    推荐用 ``preset`` 一键切档（见 RetrievePreset 注释）；需要微调时用
    ``source_configs[source]`` 覆写单源字段，或显式设置 ``source_preset`` /
    ``eval_layer_enabled`` / ``eval_layer_mode`` 覆盖 preset 推导值。
    """
    enabled_sources: List[str] = field(default_factory=lambda: ["feishu", "bailian"])
    source_configs: Dict[str, SourceAgentConfig] = field(default_factory=dict)

    # ── 一体化档位（Phase 4c）──
    preset: RetrievePreset = "lite"

    # ── preset 显式覆盖通道（向后兼容 + 细粒度手动覆盖）──
    # 这些字段为 None/默认值时走 preset 推导；非默认时覆盖 preset。
    # exp_phase4b*.py 显式设置 source_configs + eval_layer_enabled=True/False 的写法继续生效。
    source_preset: Optional[Literal["strict", "loose"]] = None
    eval_layer_enabled: Optional[bool] = None
    eval_layer_mode: Optional[EvalLayerMode] = None

    # ── eval_layer 细粒度旋钮（Phase 4b）──
    eval_layer_top_k: int = 12             # 兜底上限；实际值由 min(n_focuses*3, CAP) 计算
    eval_layer_llm_timeout_s: float = 15.0 # LlmEvalLayer 单次 LLM 超时
    eval_layer_llm_max_input: int = 22     # LlmEvalLayer 直灌 LLM 的最大 chunk 数，超过则 rule 预筛

    model: str = "gemini-3.1-flash-lite-preview"   # 全局统一 LLM

    # ── preset 推导内部表 ───────────────────────────────────────
    _PRESET_DEFAULTS = {
        "lite":      {"source_preset": "strict", "eval_layer_enabled": False, "eval_layer_mode": "rule",
                      "source_overrides": {
                          "google": {"google_download_timeout_s": 8.0, "google_api_timeout_s": 8.0},
                      }},
        "balanced":  {"source_preset": "strict", "eval_layer_enabled": False, "eval_layer_mode": "rule",
                      "source_overrides": {
                          "bailian": {"max_loops": 2, "source_timeout_s": 30.0},
                          "google": {"google_download_timeout_s": 12.0, "google_api_timeout_s": 12.0},
                      }},
        "deep_dive": {"source_preset": "loose",  "eval_layer_enabled": True,  "eval_layer_mode": "llm_tool",
                      "source_overrides": {
                          "feishu":  {"max_loops": 2, "source_timeout_s": 45.0},
                          "bailian": {"max_loops": 3, "source_timeout_s": 50.0},
                          "google": {"google_download_timeout_s": 12.0, "google_api_timeout_s": 12.0},
                      }},
    }

    @property
    def effective_source_preset(self) -> Optional[Literal["strict", "loose"]]:
        """实际生效的 source_preset：显式设置优先，否则从 preset 推导。"""
        if self.source_preset is not None:
            return self.source_preset
        return self._PRESET_DEFAULTS.get(self.preset, {}).get("source_preset")  # type: ignore[return-value]

    @property
    def eval_layer_active(self) -> bool:
        """eval_layer 是否启用：显式设置优先，否则从 preset 推导。"""
        if self.eval_layer_enabled is not None:
            return self.eval_layer_enabled
        return bool(self._PRESET_DEFAULTS.get(self.preset, {}).get("eval_layer_enabled", False))

    @property
    def eval_layer_effective_mode(self) -> EvalLayerMode:
        """eval_layer 模式：显式设置优先，否则从 preset 推导。"""
        if self.eval_layer_mode is not None:
            return self.eval_layer_mode
        return self._PRESET_DEFAULTS.get(self.preset, {}).get("eval_layer_mode", "rule")  # type: ignore[return-value]

    def config_for(self, source: str) -> SourceAgentConfig:
        """取某源配置，缺省返回默认 ``SourceAgentConfig()``（不叠加 preset，兼容旧调用）。"""
        return self.source_configs.get(source, SourceAgentConfig())

    def effective_config_for(self, source: str) -> SourceAgentConfig:
        """取某源的最终生效配置。

        优先级：``source_configs[source]`` 显式字段 > preset 内 source_overrides >
        ``effective_source_preset`` 基底 > ``SourceAgentConfig()``。
        """
        # 1) 基底 = effective preset 或 默认
        sp = self.effective_source_preset
        if sp == "strict":
            base = replace(STRICT_SOURCE_CFG)
        elif sp == "loose":
            base = replace(LOOSE_SOURCE_CFG)
        else:
            base = SourceAgentConfig()

        # 2) preset 内 per-source 覆写（如 balanced.bailian.max_loops=2）
        preset_overrides = (self._PRESET_DEFAULTS.get(self.preset) or {}).get("source_overrides", {})
        if source in preset_overrides:
            diff = {k: v for k, v in preset_overrides[source].items()}
            base = replace(base, **diff)

        # 3) 用户显式 source_configs 覆写（最高优先级）
        override = self.source_configs.get(source)
        if override is None:
            return base

        defaults = SourceAgentConfig()
        diff: Dict[str, object] = {}
        for f in (
            "source_top_k", "min_loops", "max_loops",
            "source_timeout_s", "sub_query_top_n",
            "google_download_timeout_s", "google_api_timeout_s",
            "google_enable_gmail", "google_enable_gmail_attachments",
            "google_enable_sheet_filter", "google_enable_excel_filter",
            "google_enable_fulltext_search", "google_order_by_mode",
        ):
            if getattr(override, f) != getattr(defaults, f):
                diff[f] = getattr(override, f)
        return replace(base, **diff) if diff else base
