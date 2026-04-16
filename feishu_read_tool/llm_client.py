"""Feishu RAG — Stage 5 LLM scoring & summarization.

Imports generic call_llm/parse_json_response from tools.llm_client.
Pipeline-specific types from .pipeline_models.
"""

import concurrent.futures
import json
import logging
from typing import List

# 延迟 import tools.llm_client，避免循环：
# feishu_read_tool/llm_client → tools/__init__ → feishu_retrieve → feishu_read_tool/pipeline
# call_llm / parse_json_response 在函数调用时才导入（不影响性能，导入缓存后后续调用零开销）
from .pipeline_models import (
    Chunk, ChunkResult, ChunkContentType, PipelineConfig,
)

logger = logging.getLogger(__name__)

SCORE_PROMPT_TEMPLATE = """You are a content relevance scoring assistant.
User query: "{query}"

Below are all content chunks extracted from multiple Feishu documents.
Score each chunk uniformly (scores are comparable across documents):
{chunks_json}

Each chunk format:
{{
  "chunk_id": "docID_index",
  "doc_title": "document title",
  "content_type": "text | table_md | table_json",
  "heading_path": "section path",
  "content_preview": "first 400 chars or table summary"
}}

For each chunk:
1. Assign relevance score (0.00-1.00, two decimal places, unified scale across all chunks)
2. Write one-sentence summary (<=15 words, explain how it relates to the query)

Return a JSON array only, no other text:
[{{"chunk_id": "...", "score": 0.80, "summary": "..."}}]

Scoring scale:
- 0.90-1.00: Directly answers the query, contains core data or conclusions
- 0.60-0.89: Strongly related, contains important background or supplementary info
- 0.30-0.59: Weakly related, topic overlap but low information value
- 0.00-0.29: Unrelated
- table_md or table_json with matching topic: score +0.10 (max 1.00)
"""

_SCORE_BATCH_SIZE = 20


def _make_chunk_preview(chunk: Chunk, cfg: PipelineConfig) -> str:
    if chunk.content_type == ChunkContentType.TABLE_JSON:
        try:
            data = json.loads(chunk.content)
            return f"[Table] {data.get('table_summary', '')} cols: {data.get('col_headers', [])}"
        except Exception:
            return chunk.content
    max_chars = cfg.table_preview_max_chars if chunk.content_type == ChunkContentType.TABLE_MD else cfg.chunk_preview_max_chars
    preview = chunk.content[:max_chars]
    return preview + ("..." if len(chunk.content) > max_chars else "")


def llm_score_and_summarize(
    query: str,
    all_chunks: List[Chunk],
    cfg: PipelineConfig = None,
) -> List[ChunkResult]:
    """Stage 5: score and summarize chunks via LLM. Returns filtered ChunkResult list."""
    if not all_chunks:
        return []
    if cfg is None:
        cfg = PipelineConfig()

    rel_thresh = cfg.relevance_threshold
    tbl_thresh = cfg.table_relevance_threshold
    id_to_chunk = {c.chunk_id: c for c in all_chunks}
    batches = [all_chunks[i:i + _SCORE_BATCH_SIZE] for i in range(0, len(all_chunks), _SCORE_BATCH_SIZE)]

    logger.info("Stage 5: scoring %d chunks in %d batch(es) [concurrent]", len(all_chunks), len(batches))

    def score_batch(args):
        batch_idx, batch = args
        chunks_payload = [
            {
                "chunk_id": chunk.chunk_id,
                "doc_title": chunk.doc_title,
                "content_type": chunk.content_type.value,
                "heading_path": chunk.heading_path,
                "content_preview": _make_chunk_preview(chunk, cfg),
            }
            for chunk in batch
        ]
        try:
            prompt = SCORE_PROMPT_TEMPLATE.format(
                query=query,
                chunks_json=json.dumps(chunks_payload, ensure_ascii=False, indent=2),
            )
            from tools.llm_client import call_llm, parse_json_response  # noqa: PLC0415 (lazy import, breaks circular)
            response_text = call_llm(prompt, max_tokens=8192, _caller_tag=f"score_batch_{batch_idx}")
            return parse_json_response(response_text)
        except Exception as e:
            logger.warning("Stage 5 LLM scoring failed (batch %d): %s", batch_idx, e)
            return []

    with concurrent.futures.ThreadPoolExecutor() as executor:
        batch_results = list(executor.map(score_batch, enumerate(batches)))

    chunk_results: List[ChunkResult] = []
    for score_result_list in batch_results:
        for item in score_result_list:
            chunk_id = item.get("chunk_id")
            score    = min(1.0, max(0.0, float(item.get("score", 0))))
            summary  = str(item.get("summary", ""))
            chunk = id_to_chunk.get(chunk_id)
            if chunk is None:
                continue
            is_table  = chunk.content_type in (ChunkContentType.TABLE_MD, ChunkContentType.TABLE_JSON)
            threshold = tbl_thresh if is_table else rel_thresh
            if score >= threshold:
                chunk_results.append(ChunkResult(
                    summary=summary,
                    chunks_content=chunk.content,
                    content_type=chunk.content_type,
                    file_name=chunk.doc_title,
                    file_url=chunk.doc_url,
                    chunk_id=chunk.chunk_id,
                    heading_path=chunk.heading_path,
                    relevance_score=score,
                    token_count=chunk.token_count,
                ))

    chunk_results.sort(key=lambda r: r.relevance_score, reverse=True)
    return chunk_results
