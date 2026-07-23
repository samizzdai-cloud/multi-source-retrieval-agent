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
    """Scored chunk produced by Stage 5 (legacy mode) or raw chunk (DocPool mode)."""
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

    # Candidate trim params（仅在 skip_scoring=False 遗留模式下生效）
    max_candidates:     int   = 10

    # query_type: affects downstream routing (feishu structured vs concept)
    query_type:         str   = "structured"   # "structured" | "concept"
    # Stage 5 scoring params（仅在 skip_scoring=False 的遗留模式下生效）
    # Deprecated: source-agent bounded reads make this embedding branch unused.
    use_embedding_score: bool = False      # False = pure BM25, True = BM25+embedding
    bm25_weight:        float = 0.5       # hybrid BM25 weight
    emb_weight_score:   float = 0.5       # hybrid embedding weight

    # Stage 4 text splitting
    max_chunk_tokens:   int   = 800
    min_chunk_tokens:   int   = 50
    overlap_tokens:     int   = 80

    # Stage 4 table size thresholds
    table_small_max_row:  int = 20
    table_small_max_col:  int = 10
    table_wide_max_row:   int = 10
    table_wide_max_col:   int = 20

    # Chunk scoring params（仅在 skip_scoring=False 遗留模式下生效）
    relevance_threshold:       float = 0.50
    table_relevance_threshold: float = 0.40
    chunk_preview_max_chars:   int   = 800      # TEXT chunks
    table_preview_max_chars:   int   = 1200     # TABLE_MD chunks

    # Stage 6 budget
    total_token_budget:   int = 12000

    # Stage 3 concurrency
    stage3_max_concurrency: int = 5  # max simultaneous block-fetch requests (QPS cap)

    # Docx type-30 embeds use the Feishu Sheets API, not the Docx Block API
    # nor the standalone TablePool.  Fetch their raw rows in an independent
    # bounded pool; chunk construction remains ordered on the caller thread.
    embedded_sheet_read_concurrency: int = 10

    # DocPool 模式：跳过 Stage 2 (BM25 candidate filter) + Stage 5 (BM25+embedding scoring)
    # Stage 3 (fetch blocks) + Stage 4 (chunking) 保留；评分由 DocPool.wait_and_score 完成
    skip_scoring: bool = False

    # lite 模式专用：只用一次 `list_document_blocks` 主路径，**不**再额外调用
    # IO 类块的扩展 API（type30 sheet / type53 bitable / type43 whiteboard）以及
    # comments 接口——把所有 99991400 共享桶预算让给 P_BLOCK_API。
    # 影响：lite 模式下 block_api 分流文档**只**得到正文文本 chunks，嵌入表/画板
    # 部分内容会缺失；对极速场景可接受。
    lite_block_only: bool = False

    # Optional request-local collector for recoverable Stage-4 I/O failures.
    # The caller owns the list; entries contain only IDs/types, never body text.
    io_failure_sink: Optional[List[Dict[str, str]]] = None
