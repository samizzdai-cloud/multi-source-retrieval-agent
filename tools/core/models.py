"""统一数据模型 — 多源检索 Agent 的核心类型定义"""

import os
from dataclasses import dataclass, field, replace
from typing import ClassVar, Dict, List, Literal, Optional, Set


@dataclass
class SourceConfig:
    """检索源凭证配置。优先使用字段值，为空时 fallback 到同名环境变量。

    设计意图：
    - 测试阶段：全部留空，自动从 .env 读取（默认行为）
    - 生产部署：上游 Agent 传入凭证，覆盖 env vars
    - 某个凭证为空 → 对应检索源不可用（不调用）
    """
    feishu_uat: str = ""
    feishu_open_id: str = ""              # 飞书用户 open_id（用于 author_filter="me" / personal_recent）
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
    feishu_open_id: str = ""              # 用户 open_id；空时运行时通过 /authen/v1/user_info 兜底请求

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
            feishu_open_id=self.feishu_open_id,
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
    google_enable_gmail: bool = True        # 默认开启 Gmail 检索
    google_enable_gmail_attachments: bool = False # balanced/deep_dive 在 preset 中覆盖开启
    google_enable_sheet_search: bool = True  # 是否开启 Google Sheets 搜索（实验可关闭）
    google_enable_sheet_filter: bool = True  # 是否开启 Google Sheets qwen rerank 过滤
    google_enable_excel_filter: bool = True  # 是否开启 Excel/CSV qwen rerank 过滤
    google_enable_fulltext_search: bool = True # 是否开启 Drive 全文检索（若为 False，则仅查 name）
    google_enable_native_slide_size_gate: bool = False
    google_enable_type_pools: bool = False
    google_enable_gmail_isolation: bool = False
    google_order_by_mode: Optional[str] = "viewedByMeTime desc" # Drive API 排序，默认"我最近查看"优先
    google_per_file_timeout_s: float = 20.0
    google_native_doc_size_gate_bytes: int = 45_000_000
    google_native_doc_outline_threshold_bytes: int = 10_000_000
    google_native_slide_size_gate_bytes: int = 55_000_000
    google_pptx_size_gate_bytes: int = 35_000_000           # pptx 单独的 size gate（35MB；43MB 实测 9s 仍 0 chunks，回收预算）
    google_word_size_gate_bytes: int = 30_000_000           # word/docx 的 size gate
    google_pptx_download_timeout_s: float = 6.0             # pptx 独立 timeout（默认 6s，比 native API 更激进）
    google_word_download_timeout_s: float = 6.0             # word/docx 独立 timeout
    google_enable_file_content_cache: bool = True           # 跨 query 文件级 LRU 缓存
    google_file_content_cache_ttl_s: float = 300.0          # 缓存 TTL（5 分钟）
    google_file_content_cache_max_files: int = 64           # 缓存最大条目数
    google_enable_cross_sq_pre_filter: bool = False         # chunk_pool 后不再默认二次 Qwen；预算在 G-2 前完成
    google_cross_sq_pre_filter_threshold: int = 10          # chunk_pool > 此阈值才触发（默认 source_top_k * 2）
    # eval_only LLM 输入硬上限（独立于 source_top_k）。当 cross_sq_pre_filter 计算
    # target_n = max(source_top_k*2, source_top_k+2) 后，再与本字段 min 取小值。
    # None / 0 表示不额外限制（沿用旧 target_n 行为）。
    # 用途：保留 source_top_k=5 输出的同时，把 eval input 从 10 压到 6-8 减 LLM RTT。
    google_eval_input_max_chunks: Optional[int] = None
    google_native_only: bool = False                        # lite 模式开启：仅原生 Google 文件（跳过 pptx/docx/excel/txt/csv）
    google_sheet_chunk_cap: int = 50
    google_sheet_prefilter_trigger_count: int = 6
    google_sheet_prefilter_qwen_trigger_count: int = 8
    google_sheet_prefilter_close_delta: float = 0.12
    # Per-sub_query Sheet 文件 cap（取代旧的全局 metadata_max_files_*）。
    # 每个 sq 按自己的 query_type 取值，union 后即 Phase B prefetch 输入集合。
    google_sheet_files_per_sq_structured: int = 3
    google_sheet_files_per_sq_concept: int = 2
    # 家族级 nonnative 搜索 gate：仅当 sq.query_type ∈ 白名单 时才在 Phase A 召回该家族的非原生文件
    # default 涵盖 structured / concept / personal_recent；可由 preset 覆盖
    google_nonnative_search_query_types: Dict[str, Set[str]] = field(default_factory=lambda: {
        "sheet": {"structured", "personal_recent"},
        "slide": {"structured", "concept", "personal_recent"},
        "doc":   {"structured", "concept", "personal_recent"},
    })
    google_dedup_native_vs_nonnative: bool = True
    google_doc_chunk_cap: int = 60
    google_slide_chunk_cap: int = 40
    google_file_emergency_rerank_threshold: int = 200
    google_file_emergency_rerank_top_n: int = 4
    google_type_bucket_rerank_threshold: int = 25
    google_type_bucket_rerank_top_n: int = 7       # doc / slide / sheet 统一上限
    google_type_bucket_rerank_top_n_gmail: int = 4 # gmail 单独上限（信噪比低，收紧）
    google_type_pool_sheet_workers: int = 4
    google_type_pool_doc_workers: int = 3
    google_type_pool_slide_workers: int = 3
    google_type_pool_gmail_workers: int = 2
    google_type_pool_nonnative_workers: int = 2  # 合并 pptx/word/excel/csv/txt 下载并发
    google_gmail_attachment_max_files: int = 3
    # per-type 搜索召回数量（按 preset 可定制）
    google_doc_search_per_sq: int = 12    # 每个 sub_query Doc 搜索召回数
    google_slide_search_per_sq: int = 12  # 每个 sub_query Slide 搜索召回数
    google_sheet_search_per_sq: int = 5   # 每个 sub_query Sheet 搜索召回数
    # per-type 候选数量（rerank 后保留，必须 ≤ 搜索召回数）
    google_doc_candidates_per_sq: int = 12    # 每个 sub_query Doc 必读候选数
    google_slide_candidates_per_sq: int = 12  # 每个 sub_query Slide 必读候选数
    google_sheet_candidates_per_sq: int = 5   # 每个 sub_query Sheet 必读候选数（收紧）
    # Gmail 候选数量
    google_max_gmail_threads_per_subquery: int = 10  # Gmail 搜索默认返回 thread 数
    google_gmail_rerank_top_n: int = 3               # snippet rerank 后保留 thread 数

    # ── 时间符合性（P1）─────────────────────────────────────────────
    # eval LLM 在判 tier 时会读这两个字段：若 chunk 文档/邮件时间在窗外 → 降一级；
    # 严重过期 → noise。eval prompt 内化为 rubric，不输出独立 chunk 字段。
    temporal_filter_enabled: bool = False
    temporal_filter_window_days: Optional[int] = None    # None = 不过滤
    temporal_filter_field: str = "modified"              # "modified" / "created" / "sent_at"


# ── eval_layer 模式（Phase 4b）────────────────────────────────────
EvalLayerMode = Literal["rule", "llm_tool"]

# ── RetrieveConfig 一体化档位（Phase 4c）──────────────────────────
# 档位语义：
#   lite      : 单轮快路径 + eval_layer 关 + doc 全走 MCP（无 table）
#               极简快速路径，doc[:8] MCP-only，跳过 table 流水线
#   balanced  : 单轮快路径 + eval_layer 关 + doc block_api/mcp 混合 + table 标准
#               当前默认生产模式，质量与速度平衡
#   deep_dive : 多轮宽召回 + llm_tool eval_layer + doc block_api/mcp 混合 + table 宽松
#               适合模糊 query + 多源噪声大场景
RetrievePreset = Literal["lite", "balanced", "deep_dive"]


@dataclass
class RetrieveConfig:
    """retrieve_agent 顶层配置。

    外部仅暴露三个旋钮：``preset`` / ``enabled_sources`` / ``source_configs``。
    其余 source_base / eval_layer_* 行为完全由 preset 推导（见 _PRESET_DEFAULTS）；
    eval_layer 细粒度参数与 model 为内部常量，不再支持外部覆盖——如需调整请改
    preset 表或常量本身，避免外部调用方手动旋调子参导致档位语义漂移。
    """
    enabled_sources: List[str] = field(default_factory=lambda: ["feishu", "bailian"])
    source_configs: Dict[str, SourceAgentConfig] = field(default_factory=dict)

    # ── 一体化档位（Phase 4c）──
    preset: RetrievePreset = "lite"

    # ── 内部常量（不作为构造参数；调整请改源码或 preset 表）──
    EVAL_LAYER_TOP_K: ClassVar[int] = 12
    EVAL_LAYER_LLM_TIMEOUT_S: ClassVar[float] = 15.0
    EVAL_LAYER_LLM_MAX_INPUT: ClassVar[int] = 22
    MODEL: ClassVar[str] = "gemini-3.1-flash-lite-preview"

    # 兼容旧调用：保留小写别名属性，但只读
    @property
    def eval_layer_top_k(self) -> int: return self.EVAL_LAYER_TOP_K
    @property
    def eval_layer_llm_timeout_s(self) -> float: return self.EVAL_LAYER_LLM_TIMEOUT_S
    @property
    def eval_layer_llm_max_input(self) -> int: return self.EVAL_LAYER_LLM_MAX_INPUT
    @property
    def model(self) -> str: return self.MODEL

    # ── preset 推导内部表 ───────────────────────────────────────
    _PRESET_DEFAULTS = {
        "lite":      {"source_base": {}, "eval_layer_enabled": False, "eval_layer_mode": "rule",
                      "focus_quota": {"critical_min": 2, "important_min": 0},
                      "final_top_k_per_focus": 4,
                      "source_overrides": {
                          "google": {
                              # 激进 timeout：lite 主打速度
                              "google_download_timeout_s": 6.0, "google_api_timeout_s": 6.0,
                              "google_per_file_timeout_s": 7.0,
                              "google_pptx_download_timeout_s": 6.0,
                              "google_word_download_timeout_s": 6.0,
                              # 仅原生 Google 文件（跳过 pptx/docx/excel/txt/csv 二进制下载）
                              "google_native_only": True,
                              # lite 固定保持极速特征：不走 Sheet 搜索/metadata/read
                              "google_enable_sheet_search": False,
                              # gmail 仅正文，不下附件
                              "google_enable_gmail_attachments": False,
                              # 候选/检索数量收紧
                              "google_doc_search_per_sq": 8, "google_slide_search_per_sq": 8,
                              # doc rerank 候选 +1（4→5），适度放开 doc/docx/txt 召回。
                              "google_doc_candidates_per_sq": 5, "google_slide_candidates_per_sq": 3,
                              "google_sheet_candidates_per_sq": 3,
                              "google_max_gmail_threads_per_subquery": 2,
                              "google_gmail_rerank_top_n": 2,
                              # 复用 balanced 的所有优化开关
                              "google_enable_native_slide_size_gate": True,
                              "google_enable_type_pools": True,
                              "google_enable_gmail_isolation": True,
                              "google_enable_cross_sq_pre_filter": False,
                              # lite：sheet + PPTX 全关；保留 doc nonnative（docx/txt 较快）。
                              # 配合 doc_candidates_per_sq +1（4→5）补足召回深度。
                              "google_nonnative_search_query_types": {
                                  "sheet": set(),
                                  "slide": set(),                                          # PPTX off
                                  "doc":   {"structured", "concept", "personal_recent"},
                              },
                          },
                      }},
        "balanced":  {"source_base": {}, "eval_layer_enabled": False, "eval_layer_mode": "rule",
                      "focus_quota": {"critical_min": 2, "important_min": 0},
                      "final_top_k_per_focus": 4,
                      "source_overrides": {
                          "bailian": {"max_loops": 2, "source_timeout_s": 30.0},
                          "google": {"google_download_timeout_s": 7.0, "google_api_timeout_s": 7.0,
                                     "google_per_file_timeout_s": 9.0,
                                     # PPTX 是 balanced 主要尾延迟（avg 3.8s, p95 8s, avg 27MB）。
                                     # 5s 下载超时让大 PPTX 提前失败，不影响小文件解析。
                                     "google_pptx_download_timeout_s": 5.0,
                                     "google_word_download_timeout_s": 7.0,
                                     # balanced：禁用 PPTX 搜索，将整组 nonnative pool 资源让给 doc/excel/csv。
                                     # 实测 PPTX 占 balanced 文件读取总耗时 51%，禁用后预期 -3~5s/query；
                                     # native slide / docx / xlsx 仍可获取大部分演示与表格类 critical 信号。
                                     # 需要 PPTX 召回的场景请用 deep_dive 模式。
                                     "google_nonnative_search_query_types": {
                                         "sheet": {"structured", "personal_recent"},
                                         "slide": set(),                                          # PPTX off
                                         "doc":   {"structured", "concept", "personal_recent"},
                                     },
                                     "google_enable_gmail_attachments": True,
                                     "google_enable_native_slide_size_gate": True,
                                     "google_enable_type_pools": True,
                                     "google_enable_gmail_isolation": True,
                                     "google_doc_search_per_sq": 10, "google_slide_search_per_sq": 10,
                                     # doc rerank 候选 +1（5→6），PPTX 已禁后把预算转给 doc 召回。
                                     "google_doc_candidates_per_sq": 6, "google_slide_candidates_per_sq": 4,
                                     # sheet tab-level cap=3：实验证明 2 没省 wall-clock（sheet read
                                     # 已并行，4 workers 吞得下 -28% per-read），但 important 召回 -1。
                                     "google_sheet_candidates_per_sq": 3,
                                     # Sheet worker 4 → 8：让 typical 6 sheets/query 全并行无排队。
                                     # Drive Sheets API 配额每分钟 60 reads/user，8 并发不触限。
                                     "google_type_pool_sheet_workers": 8,
                                     # Budget is handled before chunk_pool:
                                     # type-pool emergency rerank + per-sq mixed rerank.
                                     "sub_query_top_n": 10,
                                     "google_enable_cross_sq_pre_filter": False,
                                     # eval_only 输入限制（google_eval_input_max_chunks）实验：
                                     # 8 vs default 10 实测 critical 平均 -1.6，Q4 全 0C，
                                     # eval LLM 看不到第 9-10 名候选时漏掉关键 chunk。回滚到 None
                                     # （沿用 source_top_k*2=10）。如未来发现 eval RTT 是主瓶颈
                                     # 可单独打开。
                                     "google_max_gmail_threads_per_subquery": 3,
                                     "google_gmail_rerank_top_n": 3},
                      }},
        "deep_dive": {"source_base": {"source_top_k": 8, "max_loops": 2, "sub_query_top_n": 12, "source_timeout_s": 40.0},
                      "eval_layer_enabled": True,  "eval_layer_mode": "llm_tool",
                      "focus_quota": {"critical_min": 2, "important_min": 0},
                      "final_top_k_per_focus": 5,
                      "source_overrides": {
                          "feishu":  {"max_loops": 2, "source_timeout_s": 45.0},
                          "bailian": {"max_loops": 3, "source_timeout_s": 50.0},
                          "google": {
                              # 单轮（覆盖 LOOSE 的 max_loops=2，eval_layer 仍兜底）
                              "max_loops": 1,
                              # Timeout：balanced + 1s
                              "google_download_timeout_s": 8.0,
                              "google_api_timeout_s": 8.0,
                              "google_per_file_timeout_s": 10.0,
                              "google_pptx_download_timeout_s": 6.0,
                              "google_word_download_timeout_s": 8.0,
                              # Size gate：balanced × 1.10
                              "google_native_doc_size_gate_bytes": 50_000_000,      # 45 → 50MB
                              "google_native_slide_size_gate_bytes": 60_000_000,    # 55 → 60MB
                              "google_pptx_size_gate_bytes": 38_500_000,            # 35 → 38.5MB
                              "google_word_size_gate_bytes": 33_000_000,            # 30 → 33MB
                              # Worker pool：每池 +1
                              "google_type_pool_sheet_workers": 5,                  # 4 → 5
                              "google_type_pool_doc_workers": 4,                    # 3 → 4
                              "google_type_pool_slide_workers": 4,                  # 3 → 4
                              "google_type_pool_nonnative_workers": 3,              # 2 → 3
                              "google_type_pool_gmail_workers": 3,                  # 2 → 3
                              # nonnative gate 全开（所有家族 × 所有 query_type）
                              "google_nonnative_search_query_types": {
                                  "sheet": {"structured", "concept", "personal_recent"},
                                  "slide": {"structured", "concept", "personal_recent"},
                                  "doc":   {"structured", "concept", "personal_recent"},
                              },
                              # 沿用现有积极召回（其他参数不变）
                              "google_enable_gmail_attachments": True,
                              "google_enable_native_slide_size_gate": True,
                              "google_enable_type_pools": True,
                              "google_enable_gmail_isolation": True,
                              "google_doc_search_per_sq": 16, "google_slide_search_per_sq": 15,
                              "google_doc_candidates_per_sq": 9, "google_slide_candidates_per_sq": 9,
                              "google_type_bucket_rerank_top_n": 9,
                              "sub_query_top_n": 14,
                          },
                      }},
    }

    @property
    def focus_quota(self) -> Dict[str, int]:
        """每 focus 满足判据：{critical_min, important_min}。完全由 preset 推导。"""
        return self._PRESET_DEFAULTS.get(self.preset, {}).get(
            "focus_quota", {"critical_min": 2, "important_min": 0}
        )

    @property
    def final_top_k_per_focus(self) -> int:
        """retrieve_agent 出口的每 focus 平均上限（× n_focuses 得到 final_top_k 硬上限）。"""
        return int(self._PRESET_DEFAULTS.get(self.preset, {}).get("final_top_k_per_focus", 4))

    def final_top_k(self, n_focuses: int) -> int:
        """硬上限。候选不足时输出实际数量，可少不可多。"""
        return self.final_top_k_per_focus * max(1, n_focuses)

    @property
    def eval_layer_active(self) -> bool:
        """eval_layer 是否启用：完全由 preset 推导（lite/balanced=False, deep_dive=True）。"""
        return bool(self._PRESET_DEFAULTS.get(self.preset, {}).get("eval_layer_enabled", False))

    @property
    def eval_layer_effective_mode(self) -> EvalLayerMode:
        """eval_layer 模式：完全由 preset 推导。"""
        return self._PRESET_DEFAULTS.get(self.preset, {}).get("eval_layer_mode", "rule")  # type: ignore[return-value]

    def config_for(self, source: str) -> SourceAgentConfig:
        """取某源配置，缺省返回默认 ``SourceAgentConfig()``（不叠加 preset，兼容旧调用）。"""
        return self.source_configs.get(source, SourceAgentConfig())

    def effective_config_for(self, source: str) -> SourceAgentConfig:
        """取某源的最终生效配置。

        优先级：``source_configs[source]`` 显式字段 >
                preset.source_overrides[source] >
                preset.source_base >
                ``SourceAgentConfig()`` 默认值。
        """
        preset_def = self._PRESET_DEFAULTS.get(self.preset) or {}

        # 1) 基底 = 默认 SourceAgentConfig + preset.source_base
        base_kwargs = preset_def.get("source_base") or {}
        base = replace(SourceAgentConfig(), **base_kwargs) if base_kwargs else SourceAgentConfig()

        # 2) preset 内 per-source 覆写（如 balanced.bailian.max_loops=2）
        preset_overrides = preset_def.get("source_overrides", {})
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
            "google_enable_sheet_search",
            "google_enable_sheet_filter", "google_enable_excel_filter",
            "google_enable_fulltext_search", "google_enable_native_slide_size_gate",
            "google_enable_type_pools",
            "google_enable_gmail_isolation", "google_order_by_mode",
            "google_per_file_timeout_s", "google_native_doc_size_gate_bytes",
            "google_native_doc_outline_threshold_bytes", "google_native_slide_size_gate_bytes",
            "google_pptx_size_gate_bytes", "google_word_size_gate_bytes",
            "google_pptx_download_timeout_s", "google_word_download_timeout_s",
            "google_sheet_chunk_cap", "google_doc_chunk_cap", "google_slide_chunk_cap",
            "google_sheet_prefilter_trigger_count", "google_sheet_prefilter_qwen_trigger_count",
            "google_sheet_prefilter_close_delta",
            "google_sheet_files_per_sq_structured", "google_sheet_files_per_sq_concept",
            "google_nonnative_search_query_types", "google_dedup_native_vs_nonnative",
            "google_file_emergency_rerank_threshold", "google_file_emergency_rerank_top_n",
            "google_type_bucket_rerank_threshold", "google_type_bucket_rerank_top_n",
            "google_type_bucket_rerank_top_n_gmail",
            "google_type_pool_sheet_workers", "google_type_pool_doc_workers",
            "google_type_pool_slide_workers", "google_type_pool_gmail_workers",
            "google_type_pool_nonnative_workers",
            "google_eval_input_max_chunks",
            "google_gmail_attachment_max_files",
            "google_doc_search_per_sq", "google_slide_search_per_sq", "google_sheet_search_per_sq",
            "google_doc_candidates_per_sq", "google_slide_candidates_per_sq", "google_sheet_candidates_per_sq",
            "google_max_gmail_threads_per_subquery", "google_gmail_rerank_top_n",
        ):
            if getattr(override, f) != getattr(defaults, f):
                diff[f] = getattr(override, f)
        return replace(base, **diff) if diff else base
