# tools/feishu/pipeline_models.py

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict

# ── Config constants ─────────────────────────────────────────────

# Text chunk splitting
MAX_CHUNK_TOKENS  = 800
MIN_CHUNK_TOKENS  = 50
OVERLAP_TOKENS    = 80

# Table size thresholds
TABLE_SMALL_MAX_ROW       = 20
TABLE_SMALL_MAX_COL       = 10
TABLE_WIDE_MAX_ROW        = 10
TABLE_WIDE_MAX_COL        = 20

# Relevance score thresholds (0.00-1.00)
RELEVANCE_THRESHOLD       = 0.50
TABLE_RELEVANCE_THRESHOLD = 0.40

# Token budget
TOTAL_TOKEN_BUDGET        = 12000
PER_DOC_TOKEN_BUDGET      = 4000
MAX_CHUNKS_TOTAL          = 20
MAX_CHUNKS_PER_DOC        = 5

# LLM
CHUNK_PREVIEW_MAX_CHARS   = 400
# LLM constants — inline to break circular import with tools/__init__.py
# (tools.llm_client triggers tools/__init__.py → feishu_retrieve → pipeline circular)
LLM_MODEL       = "gemini-3-flash-preview"
LLM_MAX_TOKENS  = 8192
LLM_TEMPERATURE = 0.0


class ChunkContentType(Enum):
    TEXT       = "text"
    TABLE_MD   = "table_md"
    TABLE_JSON = "table_json"



@dataclass
class Chunk:
    """Raw chunk produced by Stage 4, not yet scored."""
    chunk_id:     str
    doc_id:       str
    doc_title:    str
    doc_url:      str
    heading_path: str                 # "Report > Q1 > South", "" if no heading
    content_type: ChunkContentType
    content:      str                 # Markdown for TEXT/TABLE_MD; JSON str for TABLE_JSON
    block_ids:    list                # source block_id list for tracing
    token_count:  int
    has_table:    bool
    metadata:     Dict = field(default_factory=dict)


@dataclass
class ChunkResult:
    """Scored chunk produced by Stage 5, includes LLM-generated summary."""
    summary:         str
    chunks_content:  str
    content_type:    ChunkContentType
    file_name:       str
    file_url:        str
    chunk_id:        str
    heading_path:    str
    relevance_score: float            # 0-10
    token_count:     int


@dataclass
class RAGResult:
    """Final output passed to the downstream LLM Agent."""
    query:               str
    total_docs_searched: int
    total_docs_read:     int
    total_chunks:        int
    total_tokens:        int
    chunks:              list         # list[ChunkResult], sorted by relevance_score desc


@dataclass
class PipelineConfig:
    """
    Runtime config for run_rag_pipeline(). Override any field to change behavior.
    All fields default to the values in config.py.
    """
    # Stage 1 search (used in test_pipeline.py)
    search_page_size:   int   = 20
    search_max_pages:   int   = 1

    # Stage 2 filter
    max_candidates:     int   = 10

    # query_type: affects Stage 2 BM25 filter and Stage 5 scoring params
    query_type:         str   = "mixed"   # "data" | "concept" | "mixed"
    # Stage 5: BM25+embedding hybrid scoring (D3)
    use_embedding_score: bool = True       # False = pure BM25, True = BM25+embedding
    bm25_weight:        float = 0.5       # Stage 5 hybrid: BM25 weight
    emb_weight_score:   float = 0.5       # Stage 5 hybrid: embedding weight

    # Stage 4 text splitting
    max_chunk_tokens:   int   = 800
    min_chunk_tokens:   int   = 50
    overlap_tokens:     int   = 80

    # Stage 4 table size thresholds
    table_small_max_row:  int = 20
    table_small_max_col:  int = 10
    table_wide_max_row:   int = 10
    table_wide_max_col:   int = 20

    # Stage 5 scoring
    relevance_threshold:       float = 0.50
    table_relevance_threshold: float = 0.40
    chunk_preview_max_chars:   int   = 800      # TEXT chunks (was 400)
    table_preview_max_chars:   int   = 1200     # TABLE_MD chunks

    # Stage 6 budget
    total_token_budget:   int = 12000
    # DEPRECATED: no longer used after allocate_budget refactor
    per_doc_token_budget: int = 3000
    # DEPRECATED: no longer used after allocate_budget refactor
    max_chunks_total:     int = 20
    # DEPRECATED: no longer used after allocate_budget refactor
    max_chunks_per_doc:   int = 5

    # Stage 3 concurrency
    stage3_max_concurrency: int = 5  # max simultaneous block-fetch requests (QPS cap)

    # DocPool 模式：跳过 Stage 2 (BM25 candidate filter) + Stage 5 (BM25+embedding scoring)
    # Stage 3 (fetch blocks) + Stage 4 (chunking) 保留；评分由 DocPool.wait_and_score 完成
    skip_scoring: bool = False


# ── Path A data classes ───────────────────────────────────────────

@dataclass
class ResolveResult:
    """resolve_doc_ref() 的返回值"""
    resolved: bool              # True = 已确定唯一 doc_id；False = 需用户确认
    doc_id: Optional[str]       # resolved=True 时有值
    input_type: str             # "url" | "doc_id" | "keyword"
    candidates: List[Dict]      # resolved=False 时返回候选列表
    error: Optional[str] = None # resolve 失败时的错误说明（如不支持的文档类型）


@dataclass
class FullDocContent:
    """read_full_doc() 的返回值"""
    doc_id: str
    doc_title: str
    doc_url: str
    chunks: List[Chunk]         # 构建好的 Chunk 列表，内部使用，不对外输出
    estimated_tokens: int       # sum(chunk.token_count for chunk in chunks)
    reading_strategy: str       # "full_text" | "full_text_structured" | "chunked_summary"


@dataclass
class RelevantSection:
    """analyze_doc() query_focused 模式下每个相关段落"""
    heading_path: str           # 章节路径，如 "Q1总结 > 销售达成"
    original_text: str          # 原文，不做任何改动
    factual_summary: str        # 事实性提炼结果
    importance: str             # "high" | "medium"


@dataclass
class DocAnalysisResult:
    """analyze_doc() 的返回值"""
    doc_id: str
    doc_title: str
    intent_type: str                              # "query_focused" | "general_summary"
    query_intent: Optional[str]                   # query_focused 时有值
    relevant_sections: Optional[List[RelevantSection]]  # query_focused 时有值
    doc_summary: Optional[str]                    # general_summary 时有值


# ── Path B data classes ───────────────────────────────────────────

@dataclass
class SubQuery:
    """单个子查询"""
    query: str      # 关键词风格，2-3个关键词
    focus: str      # 语义标注，说明这个子查询关注什么
    weight: float   # 合并排序权重，0.0-1.0


@dataclass
class SubQueryPlan:
    """decompose_query() 的返回值"""
    original_query: str
    sub_queries: List[SubQuery]
    time_filter: Optional[str]   # "last_N_days" | None
    query_type: str              # "data" | "concept" | "mixed"


@dataclass
class JudgeAndFilterResult:
    """judge_and_filter() 的返回值"""
    coverage_score: float        # 0.0-1.0
    covered_aspects: List[str]
    missing_aspects: List[str]
    verdict: str                 # "sufficient" | "insufficient"
    gap_reason: str
    filtered_doc_ids: List[str]


@dataclass
class ChunkSufficiencyResult:
    """judge_chunk_sufficiency() 的返回值"""
    verdict: str                   # "sufficient" | "insufficient"
    sufficiency_score: float       # 0.0-1.0
    selected_chunk_ids: List[str]
    missing_aspects: List[str]
    expand_suggestion: str


@dataclass
class RoundTrace:
    """每轮检索的执行记录，用于调试"""
    round: int
    sub_queries: List[SubQuery]
    candidates_count: int
    filtered_doc_ids: List[str]
    chunks_built: int
    chunks_scored: int
    verdict_judge_filter: str      # "sufficient" | "insufficient"
    verdict_sufficiency: str       # "sufficient" | "insufficient" | "skipped"


@dataclass
class AdaptiveRAGResult:
    """run_adaptive_rag() 的最终返回值"""
    query: str
    final_chunks: List[ChunkResult]       # 含完整原文，按 score 降序
    total_rounds: int
    is_sufficient: bool
    missing_aspects: List[str]
    all_searched_queries: List[str]       # 所有历史子 query 字符串（去重）
    round_traces: List[RoundTrace]


@dataclass
class OrchestratorConfig:
    """run_adaptive_rag() 的运行配置"""
    max_rounds: int = 3
    max_sub_queries: int = 4
    coverage_threshold: float = 0.5      # judge_and_filter 充分性阈值
    sufficiency_threshold: float = 0.6   # judge_chunk_sufficiency 充分性阈值
    min_candidates_to_llm: int = 3       # 候选文档数低于此值直接判 insufficient
    search_concurrency: int = 5
    pipeline_cfg: PipelineConfig = field(default_factory=PipelineConfig)
    time_hint_keywords: List[str] = field(default_factory=lambda: [
        "今年", "本月", "本周", "最新", "近期", "最近"
    ])
