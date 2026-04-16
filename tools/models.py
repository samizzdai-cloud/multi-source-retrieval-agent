"""统一数据模型 — 多源检索 Agent 的核心类型定义"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


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
class SubQueryTask:
    """单个子查询任务 — FocusGroup 内的数据获取单元。"""
    query: str                         # 拆解后的子 query
    targets: List[str] = field(default_factory=list)  # ["feishu", "bailian"]
    tags: List[str] = field(default_factory=list)      # 百炼 tag 过滤（l2分类 + 时效）
    indexes: List[str] = field(default_factory=list)   # 指定知识库名（空=查全部）


@dataclass
class FocusGroup:
    """一组检索意图 + 对应的数据获取任务。

    focuses: 可评估的检索目标列表，每个 focus 描述一个维度/方面，独立追踪命中。
             示例: ["nubia DTC渠道的销售数据和转化率", "nubia Marketplace渠道的流量表现"]
    sub_queries: 为满足这些 focuses 而发出的多源检索任务。
    required_targets: 该 FocusGroup 中必须等待完成的 source 列表。
             query_planner 在检测到用户显式指定 source（如"在飞书里找"）时自动填充。
             只对该 FG 的 sub_queries.targets 中存在的 source 有效。
             示例: ["feishu"] 表示该 FG 的 feishu task 必须完成才允许早停。
    """
    focuses: List[str] = field(default_factory=list)
    sub_queries: List[SubQueryTask] = field(default_factory=list)
    required_targets: List[str] = field(default_factory=list)


@dataclass
class QueryPlan:
    """query_planner 的完整输出。"""
    original_query: str
    focus_groups: List[FocusGroup] = field(default_factory=list)
    time_filter: Optional[str] = None  # "last_N_days" | None
    query_type: str = "mixed"          # "data" | "concept" | "mixed"


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
