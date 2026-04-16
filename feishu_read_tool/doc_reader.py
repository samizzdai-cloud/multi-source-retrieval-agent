# feishu_doc_load/doc_reader.py
# Path A: single document deep reading (DocCache, resolve, read, analyze)

import asyncio
import logging
import re
from typing import Dict, List, Optional

from .client import FeishuClient
from .chunker import build_text_chunks, HeadingStack
from .table_handler import build_table_chunk, build_sheet_chunk
from tools.llm_client import call_llm, parse_json_response
from .pipeline_models import (
    Chunk, PipelineConfig, FullDocContent,
    ResolveResult, RelevantSection, DocAnalysisResult,
)
from .search import search_feishu_file

logger = logging.getLogger(__name__)

# URL patterns for Feishu document links
_FEISHU_WIKI_URL_RE = re.compile(r'https?://[^/]+\.feishu\.cn/wiki/([A-Za-z0-9]+)')
_FEISHU_DOC_URL_RE = re.compile(r'https?://[^/]+\.feishu\.cn/docx?/([A-Za-z0-9]+)')
_RAW_DOC_ID_RE = re.compile(r'^[A-Za-z0-9]{8,}$')


def _call_llm_table(prompt: str) -> str:
    """Wrapper so table LLM calls carry a recognizable caller_tag."""
    return call_llm(prompt, _caller_tag="table")


# ── DocCache ──────────────────────────────────────────────────────


class DocCache:
    """Session-level document cache. key=doc_id, value=FullDocContent.
    Lifetime matches the ADK session; destroyed when session ends.
    """

    def __init__(self):
        self._cache: Dict[str, FullDocContent] = {}

    def has(self, doc_id: str) -> bool:
        return doc_id in self._cache

    def get(self, doc_id: str) -> Optional[FullDocContent]:
        return self._cache.get(doc_id)

    def set(self, doc_id: str, content: FullDocContent) -> None:
        self._cache[doc_id] = content

    def list_cached(self) -> List[str]:
        """Return list of cached doc_ids."""
        return list(self._cache.keys())


# ── resolve_doc_ref ───────────────────────────────────────────────


def resolve_doc_ref(doc_ref: str, uat: str) -> ResolveResult:
    """Resolve a user-provided document reference to a unique doc_id or candidate list.

    Args:
        doc_ref: Feishu URL, raw doc_id, or keyword.
        uat: Feishu user_access_token.

    Returns:
        ResolveResult with resolved=True if a unique doc_id was found.
    """
    # 1. Try extracting doc_id from Feishu URL
    # Wiki URLs need an extra API call to get the actual obj_token
    wiki_m = _FEISHU_WIKI_URL_RE.search(doc_ref)
    if wiki_m:
        wiki_node_token = wiki_m.group(1)
        try:
            node_info = FeishuClient(user_access_token=uat).get_wiki_node_info(wiki_node_token)
            obj_token = node_info.get("obj_token", "")
            obj_type = node_info.get("obj_type", "")
            if not obj_token:
                return ResolveResult(resolved=False, doc_id=None, input_type="url", candidates=[],
                                     error=f"Wiki node {wiki_node_token} returned empty obj_token")
            if obj_type not in ("docx", "doc"):
                return ResolveResult(resolved=False, doc_id=None, input_type="url", candidates=[],
                                     error=f"Wiki node type '{obj_type}' is not supported (only docx/doc). "
                                           f"title={node_info.get('title', '')}")
            return ResolveResult(resolved=True, doc_id=obj_token, input_type="url", candidates=[])
        except Exception as e:
            logger.warning("resolve_doc_ref: failed to resolve wiki node %s: %s", wiki_node_token, e)
            return ResolveResult(resolved=False, doc_id=None, input_type="url", candidates=[],
                                 error=str(e))

    m = _FEISHU_DOC_URL_RE.search(doc_ref)
    if m:
        return ResolveResult(resolved=True, doc_id=m.group(1), input_type="url", candidates=[])

    # 2. Check if it's a raw doc_id
    stripped = doc_ref.strip()
    if _RAW_DOC_ID_RE.match(stripped):
        return ResolveResult(resolved=True, doc_id=stripped, input_type="doc_id", candidates=[])

    # 3. Keyword search
    result = search_feishu_file(query=stripped, page_size=5)
    if result.get("status") == "error":
        return ResolveResult(resolved=False, doc_id=None, input_type="keyword", candidates=[],
                             error=result.get("message", "搜索失败"))
    items = result.get("items", [])
    if not items:
        return ResolveResult(resolved=False, doc_id=None, input_type="keyword", candidates=[])
    if len(items) == 1:
        return ResolveResult(resolved=True, doc_id=items[0].get("id", ""), input_type="keyword", candidates=[])
    return ResolveResult(resolved=False, doc_id=None, input_type="keyword", candidates=items)


# ── read_full_doc ─────────────────────────────────────────────────


def read_full_doc(
    doc_id: str,
    uat: str,
    cache: Optional[DocCache] = None,
    cfg: Optional[PipelineConfig] = None,
) -> FullDocContent:
    """Read a Feishu document via Block API, build chunks, and cache.

    Reuses the same code path as Path B Stage 3-4 (build_table_chunk, build_text_chunks).

    Args:
        doc_id: Feishu document ID.
        uat: Feishu user_access_token.
        cache: Optional DocCache for session-level caching.
        cfg: PipelineConfig for chunk building parameters.

    Returns:
        FullDocContent with chunks and reading strategy.

    Raises:
        RuntimeError: If Block API call fails.
    """
    # Check cache
    if cache is not None and cache.has(doc_id):
        return cache.get(doc_id)

    if cfg is None:
        cfg = PipelineConfig()

    # Fetch all blocks
    client = FeishuClient(user_access_token=uat)
    blocks = client.list_document_blocks(doc_id)
    if not blocks:
        raise RuntimeError(f"Failed to fetch blocks for doc {doc_id} (empty result)")

    # Extract title from page root block (type=1)
    doc_title = ""
    for b in blocks:
        btype = b.get("block_type") if isinstance(b, dict) else getattr(b, "block_type", None)
        if btype == 1:
            # Page block — try to get title from its text
            page_obj = b.get("page") if isinstance(b, dict) else getattr(b, "page", None)
            if page_obj:
                if isinstance(page_obj, dict):
                    elements = page_obj.get("elements", [])
                else:
                    elements = getattr(page_obj, "elements", []) or []
                for el in elements:
                    if isinstance(el, dict):
                        tr = el.get("text_run")
                        if tr:
                            doc_title += tr.get("content", "") if isinstance(tr, dict) else getattr(tr, "content", "")
                    else:
                        tr = getattr(el, "text_run", None)
                        if tr:
                            doc_title += getattr(tr, "content", "")
            break

    doc_url = f"https://feishu.cn/docx/{doc_id}"

    # Build blocks_dict for table handler
    blocks_dict: Dict = {}
    for b in blocks:
        bid = b.get("block_id") if isinstance(b, dict) else getattr(b, "block_id", None)
        if bid:
            blocks_dict[bid] = b

    consumed_table_block_ids: set = set()
    heading_stack = HeadingStack()
    table_chunks: List[Chunk] = []
    chunk_counter = [0]

    # Pass 1: tables (track headings for correct heading_path assignment)
    from .chunker import HEADING_TYPES, _extract_block_text as _extr_text
    for block in blocks:
        btype = block.get("block_type") if isinstance(block, dict) else getattr(block, "block_type", None)
        bid = block.get("block_id") if isinstance(block, dict) else getattr(block, "block_id", None)

        if btype in HEADING_TYPES:
            heading_stack.push(btype, _extr_text(block))

        if btype == 31:
            try:
                _tbl_chunks, consumed = build_table_chunk(
                    block=block,
                    blocks_dict=blocks_dict,
                    doc_id=doc_id,
                    doc_title=doc_title,
                    doc_url=doc_url,
                    heading_path=heading_stack.current_path(),
                    chunk_counter=chunk_counter,
                    query="",  # Path A: no query context for table building
                    llm_caller=_call_llm_table,
                    cfg=cfg,
                )
                table_chunks.extend(_tbl_chunks)
                consumed_table_block_ids.update(consumed)
            except Exception as e:
                logger.warning("doc_reader: table chunk (type31) failed block %s: %s", bid, e)

        elif btype == 30:
            try:
                _sht_chunks, consumed = build_sheet_chunk(
                    block=block,
                    client=client,
                    doc_id=doc_id,
                    doc_title=doc_title,
                    doc_url=doc_url,
                    heading_path=heading_stack.current_path(),
                    chunk_counter=chunk_counter,
                    query="",
                    llm_caller=_call_llm_table,
                    cfg=cfg,
                )
                table_chunks.extend(_sht_chunks)
                consumed_table_block_ids.update(consumed)
            except Exception as e:
                logger.warning("doc_reader: sheet chunk (type30) failed block %s: %s", bid, e)

    # Pass 2: text blocks
    text_chunks = build_text_chunks(
        blocks=blocks,
        doc_id=doc_id,
        doc_title=doc_title,
        doc_url=doc_url,
        heading_stack=heading_stack,
        chunk_counter=chunk_counter,
        table_block_ids=consumed_table_block_ids,
        cfg=cfg,
    )

    all_chunks = table_chunks + text_chunks
    estimated_tokens = sum(c.token_count for c in all_chunks)

    # Determine reading strategy
    if estimated_tokens < 100_000:
        reading_strategy = "full_text"
    elif estimated_tokens < 500_000:
        reading_strategy = "full_text_structured"
    else:
        reading_strategy = "chunked_summary"

    full_doc = FullDocContent(
        doc_id=doc_id,
        doc_title=doc_title,
        doc_url=doc_url,
        chunks=all_chunks,
        estimated_tokens=estimated_tokens,
        reading_strategy=reading_strategy,
    )

    if cache is not None:
        cache.set(doc_id, full_doc)

    return full_doc


# ── analyze_doc ───────────────────────────────────────────────────


def _chunks_to_text(chunks: List[Chunk]) -> str:
    """Reassemble chunks into continuous text organized by heading_path."""
    parts = []
    for c in chunks:
        if c.heading_path:
            parts.append(f"# {c.heading_path}\n\n{c.content}")
        else:
            parts.append(c.content)
    return "\n\n".join(parts)


async def _score_chunk(chunk: Chunk, query_intent: str, index: int) -> Dict:
    """Score a single chunk for relevance to query_intent."""
    prompt = f"""你是文档内容相关性评分助手。
用户关注方向："{query_intent}"

以下是文档中的一个段落：
章节路径：{chunk.heading_path}
内容：{chunk.content}

评估此段落与用户关注方向的相关程度，返回 JSON：
{{"score": 0.00-1.00}}

评分标准：
- ≥0.80：直接包含核心数据或结论
- 0.60-0.79：部分相关，背景或补充信息
- <0.60：不相关或仅泛泛提及

Return JSON only."""

    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, lambda: call_llm(prompt, max_tokens=64, timeout=20.0, _caller_tag=f"doc_score_{index}")
        )
        result = parse_json_response(response)
        score = min(1.0, max(0.0, float(result.get("score", 0))))
        return {"chunk": chunk, "score": score}
    except Exception as e:
        logger.warning("Chunk scoring failed for chunk %s: %s", chunk.chunk_id, e)
        return {"chunk": chunk, "score": 0.0}


async def _extract_facts(chunk: Chunk, index: int) -> str:
    """Extract factual summary from a chunk."""
    prompt = f"""你是文档事实提炼助手。
以下是文档中的一个段落（原文）：
{chunk.content}

任务：对此段落做事实性提炼，规则：
- 保留：具体数字、日期、百分比、公司/产品名称、明确的结论性陈述
- 删除：修饰语、背景铺垫、过渡句、重复表述
- 输出不超过 150 字，不添加原文没有的信息

直接输出提炼后的文字，无需 JSON。"""

    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, lambda: call_llm(prompt, max_tokens=512, timeout=30.0, _caller_tag=f"doc_extract_{index}")
        )
        return response.strip()
    except Exception as e:
        logger.warning("Fact extraction failed for chunk %s: %s", chunk.chunk_id, e)
        return "（提炼失败，请查看原文）"


async def _analyze_query_focused(full_content: FullDocContent, query_intent: str) -> DocAnalysisResult:
    """query_focused mode: score chunks, filter by threshold, extract facts."""
    chunks = full_content.chunks

    # Step 1: Concurrent chunk scoring
    score_tasks = [_score_chunk(c, query_intent, i) for i, c in enumerate(chunks)]
    score_results = await asyncio.gather(*score_tasks)

    # Filter and categorize by score
    high_chunks = []   # score >= 0.8
    medium_chunks = [] # 0.6 <= score < 0.8

    for sr in score_results:
        score = sr["score"]
        chunk = sr["chunk"]
        if score >= 0.8:
            high_chunks.append((chunk, score))
        elif score >= 0.6:
            medium_chunks.append((chunk, score))
        # score < 0.6: discarded

    # Sort by score desc and apply hard limits
    high_chunks.sort(key=lambda x: x[1], reverse=True)
    medium_chunks.sort(key=lambda x: x[1], reverse=True)
    high_chunks = high_chunks[:3]
    medium_chunks = medium_chunks[:5]

    qualifying = [(c, s, "high") for c, s in high_chunks] + [(c, s, "medium") for c, s in medium_chunks]

    if not qualifying:
        return DocAnalysisResult(
            doc_id=full_content.doc_id,
            doc_title=full_content.doc_title,
            intent_type="query_focused",
            query_intent=query_intent,
            relevant_sections=[],
            doc_summary=None,
        )

    # Step 2: Concurrent fact extraction
    extract_tasks = [_extract_facts(c, i) for i, (c, _, _) in enumerate(qualifying)]
    summaries = await asyncio.gather(*extract_tasks)

    sections = []
    for (chunk, score, importance), summary in zip(qualifying, summaries):
        sections.append(RelevantSection(
            heading_path=chunk.heading_path,
            original_text=chunk.content,
            factual_summary=summary,
            importance=importance,
        ))

    return DocAnalysisResult(
        doc_id=full_content.doc_id,
        doc_title=full_content.doc_title,
        intent_type="query_focused",
        query_intent=query_intent,
        relevant_sections=sections,
        doc_summary=None,
    )


async def _analyze_general_summary(full_content: FullDocContent) -> DocAnalysisResult:
    """general_summary mode: generate structured summary based on reading strategy."""
    strategy = full_content.reading_strategy
    chunks = full_content.chunks

    summary_prompt_template = """你是文档摘要助手。
以下是文档全文：{full_text}

请输出结构化摘要（Markdown 格式）：
## 背景
## 核心结论（3-5 条）
## 关键数据（指标名：数值，每条一行）
## 待跟进事项（无则写"无"）

直接输出 Markdown，无需 JSON。"""

    loop = asyncio.get_event_loop()

    if strategy == "full_text":
        full_text = _chunks_to_text(chunks)
        prompt = summary_prompt_template.format(full_text=full_text)
        try:
            doc_summary = await loop.run_in_executor(
                None, lambda: call_llm(prompt, max_tokens=4096, timeout=90.0, _caller_tag="doc_summary")
            )
        except Exception as e:
            logger.warning("full_text summary failed: %s", e)
            doc_summary = "（全文摘要生成失败）"

    elif strategy == "full_text_structured":
        # Group chunks by top-level H1 section
        h1_groups: Dict[str, List[Chunk]] = {}
        for c in chunks:
            h1 = c.heading_path.split(" > ")[0] if c.heading_path else "（无标题）"
            h1_groups.setdefault(h1, []).append(c)

        section_prompt = """你是文档章节摘要助手。
以下是文档某个章节的内容：
{section_text}

请用不超过 200 字总结这个章节的核心信息，保留关键数字和结论。
直接输出摘要文字。"""

        async def summarize_section(heading: str, section_chunks: List[Chunk]) -> str:
            section_text = _chunks_to_text(section_chunks)
            prompt = section_prompt.format(section_text=section_text)
            try:
                result = await loop.run_in_executor(
                    None, lambda p=prompt: call_llm(p, max_tokens=1024, timeout=30.0,
                                                    _caller_tag=f"section_{heading[:20]}")
                )
                return f"### {heading}\n{result.strip()}"
            except Exception as e:
                logger.warning("Section summary failed for %s: %s", heading, e)
                return f"### {heading}\n（该章节摘要生成失败）"

        section_tasks = [summarize_section(h, cs) for h, cs in h1_groups.items()]
        section_summaries = await asyncio.gather(*section_tasks)

        # Synthesize final summary
        combined = "\n\n".join(section_summaries)
        synthesis_prompt = summary_prompt_template.format(full_text=combined)
        try:
            doc_summary = await loop.run_in_executor(
                None, lambda: call_llm(synthesis_prompt, max_tokens=4096, timeout=90.0,
                                       _caller_tag="doc_summary_synth")
            )
        except Exception as e:
            logger.warning("Synthesis summary failed: %s", e)
            doc_summary = combined  # fallback: use section summaries directly

    else:  # chunked_summary
        # Batch chunks by ~5000 tokens each, serial processing
        batches: List[List[Chunk]] = []
        current_batch: List[Chunk] = []
        current_tokens = 0
        for c in chunks:
            if current_tokens + c.token_count > 5000 and current_batch:
                batches.append(current_batch)
                current_batch = [c]
                current_tokens = c.token_count
            else:
                current_batch.append(c)
                current_tokens += c.token_count
        if current_batch:
            batches.append(current_batch)

        batch_summaries = []
        for i, batch in enumerate(batches):
            batch_text = _chunks_to_text(batch)
            prompt = f"""你是文档摘要助手。
以下是文档的第 {i + 1}/{len(batches)} 部分：
{batch_text}

请用不超过 300 字总结这部分的核心信息。直接输出摘要文字。"""
            try:
                result = call_llm(prompt, max_tokens=1024, timeout=30.0,
                                  _caller_tag=f"batch_summary_{i}")
                batch_summaries.append(result.strip())
            except Exception as e:
                logger.warning("Batch %d summary failed: %s", i, e)
                batch_summaries.append("（该批次摘要生成失败）")

        # Final synthesis
        combined = "\n\n".join(batch_summaries)
        synthesis_prompt = summary_prompt_template.format(full_text=combined)
        try:
            doc_summary = call_llm(synthesis_prompt, max_tokens=4096, timeout=90.0,
                                   _caller_tag="doc_summary_synth")
        except Exception as e:
            logger.warning("Final synthesis failed: %s", e)
            doc_summary = combined

    return DocAnalysisResult(
        doc_id=full_content.doc_id,
        doc_title=full_content.doc_title,
        intent_type="general_summary",
        query_intent=None,
        relevant_sections=None,
        doc_summary=doc_summary.strip(),
    )


def analyze_doc(
    full_content: FullDocContent,
    intent_type: str,
    query_intent: Optional[str] = None,
) -> DocAnalysisResult:
    """Analyze document content: query_focused or general_summary.

    Args:
        full_content: FullDocContent from read_full_doc.
        intent_type: "query_focused" or "general_summary".
        query_intent: User's focus direction (required for query_focused).

    Returns:
        DocAnalysisResult with relevant sections or summary.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, _run_analyze(full_content, intent_type, query_intent))
                return future.result()
        else:
            return loop.run_until_complete(_run_analyze(full_content, intent_type, query_intent))
    except RuntimeError:
        return asyncio.run(_run_analyze(full_content, intent_type, query_intent))


async def _run_analyze(
    full_content: FullDocContent,
    intent_type: str,
    query_intent: Optional[str],
) -> DocAnalysisResult:
    """Internal async dispatcher for analyze_doc."""
    if intent_type == "query_focused":
        return await _analyze_query_focused(full_content, query_intent or "")
    else:
        return await _analyze_general_summary(full_content)
