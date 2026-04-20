"""
Docx Pipeline — doc/docx 文档的 RAG 读取链路（Stage 2-7）

专门处理飞书 doc/docx 类型文档的 block 读取、chunking、评分全流程。
由 feishu_retrieve.py 的 Dispatcher 层按 doc_type 路由调用，
不直接处理 sheet/bitable/slides（各有独立 reader）。

Stage 2: LLM/BM25 filter candidates（候选文档过滤）
Stage 3: Fetch document blocks（Block API 并发读取）
Stage 4: Build chunks（文本 + 表格块切割）
Stage 5: LLM/BM25 score & summarize → ChunkResult list
Stage 6: Budget allocation（token 预算分配）
Stage 7: Assemble RAGResult
"""
import asyncio
import concurrent.futures
import logging
from typing import List, Dict, Optional, Set

from .client import FeishuClient
from .pipeline_models import Chunk, ChunkResult, RAGResult, PipelineConfig, ChunkContentType
from .chunker import build_text_chunks, HeadingStack
from .table_handler import build_table_chunk, build_sheet_chunk
# LLM scoring (legacy, kept as fallback)
from .llm_client import llm_score_and_summarize
from .retrieval_judge import _legacy_filter_candidates as llm_filter_candidates
# BM25 + embedding hybrid scoring (D-series: replaces LLM)
from tools.chunk_scorer import bm25_filter_candidates, hybrid_score_chunks

def _call_llm_table(prompt: str) -> str:
    """Wrapper so table/sheet LLM calls carry a recognizable caller_tag."""
    from tools.llm_client import call_llm  # noqa: PLC0415 (lazy import, breaks circular)
    return call_llm(prompt, _caller_tag="table")


def _build_bitable_embed_chunk(
    bitable_token: str,
    block_id: str,
    uat: str,
    doc_id: str,
    doc_title: str,
    doc_url: str,
    heading_path: str,
    chunk_counter: List[int],
    view_id: str = "",
) -> List[Chunk]:
    """type-53 bitable embed 快捷解析：1 次 API 调用获取数据。

    快捷路径（type-53, token 含 _tbl + 有 view_id）:
      token 解析 → app_token + table_id → search_records(view_id) → 1 次 HTTP
      字段名从 record.fields.keys() 提取（无需 list_fields）

    Large tables (>10 rows) are split with header carry-forward.

    Returns:
        List[Chunk]（表为空 / API 失败时返回空列表）
    """
    import requests as _requests

    # 从 token 解析 app_token + table_id
    if '_tbl' in bitable_token:
        parts = bitable_token.rsplit('_tbl', 1)
        app_token = parts[0]
        table_id = 'tbl' + parts[1]
    else:
        app_token = bitable_token
        # 无 _tbl 后缀，需要 list_tables（fallback）
        from .basic_tool.bitable_read import list_tables
        try:
            tables = list_tables(app_token, uat)
            if not tables:
                return []
            table_id = tables[0]["table_id"]
        except Exception:
            return []

    try:
        # 1 次 API 调用：search_records(view_id) → 数据 + 字段名
        url = (f"https://open.feishu.cn/open-apis/bitable/v1"
               f"/apps/{app_token}/tables/{table_id}/records/search")
        headers = {
            "Authorization": f"Bearer {uat}",
            "Content-Type": "application/json; charset=utf-8",
        }
        params = {"page_size": 50}
        if view_id:
            params["view_id"] = view_id

        resp = _requests.post(url, headers=headers, params=params, json={}, timeout=15)
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            logger.warning("_build_bitable_embed_chunk: search_records error %s: %s",
                           body.get("code"), body.get("msg", "")[:60])
            return []

        records = body.get("data", {}).get("items", [])
        if not records:
            return []

        # 字段名从第一条记录的 fields keys 提取（无需 list_fields API）
        field_names = list(records[0].get("fields", {}).keys())
        if not field_names:
            return []

        from .bitable_reader import _flatten_value
        from .table_handler import _split_rows_into_chunks, detect_header_axes

        # Build 2D rows: header row + data rows
        rows: List[List[str]] = [list(field_names)]  # header
        for rec in records[:50]:
            row_fields = rec.get("fields", {})
            rows.append([_flatten_value(row_fields.get(fn, "")) for fn in field_names])

        header_info = detect_header_axes(rows)
        consumed = {block_id}
        table_name = table_id

        chunks = _split_rows_into_chunks(
            rows, doc_id, doc_title, doc_url, heading_path,
            chunk_counter, consumed, header_info, rows_per_chunk=20,
            section_meta={"bitable_table": table_name},
        )
        return chunks
    except Exception as e:
        logger.warning("_build_bitable_embed_chunk failed (token=%s): %s", bitable_token, e)
        return []


def _inject_comments(doc_chunks: List, client, doc_id: str,
                     blocks: Optional[List] = None, pre_fetched_comments: Optional[List] = None) -> None:
    """将文档评论注入到对应 chunk 内容的最前面（in-place 修改）。

    匹配逻辑（双重策略）：
      1. block_id 精确匹配：若 chunk 的 block_ids 与评论所附 block 有交集，直接注入
      2. quote 文本匹配（fallback）：全局评论或无 block 绑定的评论，
         找第一个 content 包含 quote 的 chunk

    Args:
        blocks: Stage 3 已获取的 blocks 列表。传入后复用，避免重复调用 list_document_blocks。
        pre_fetched_comments: 提前并发获取到的 comments（如果有的话，可省去一次网络请求）
    """
    if not doc_chunks:
        return

    # [S1.2] 二道防线：即便上游（client.get_doc_comments / IO pool future）意外返回
    # None，也强制转成 []，避免 ``for c in None`` 炸在下面的遍历里。
    if pre_fetched_comments is not None:
        comments = pre_fetched_comments or []
    else:
        comments = client.get_doc_comments(doc_id, file_type="docx") or []

    if not comments:
        return

    logger.info("Stage 4 comments: %d unresolved comments for doc %s", len(comments), doc_id)

    block_to_chunk: Dict[str, Chunk] = {}
    for chunk in doc_chunks:
        for bid in chunk.block_ids:
            block_to_chunk[bid] = chunk

    # [P2.3-F4] 复用 Stage 3 已获取的 blocks，避免重复 API 调用
    comment_id_to_block_id: Dict[str, str] = {}
    source_blocks = blocks  # 优先使用传入的 blocks
    if source_blocks:
        for blk in source_blocks:
            for cid in (blk.get("comment_ids", []) if isinstance(blk, dict) else []):
                comment_id_to_block_id[cid] = blk["block_id"] if isinstance(blk, dict) else getattr(blk, "block_id", "")
    else:
        # Fallback: 未传入 blocks 时才调用 API（向后兼容）
        try:
            all_blocks = client.list_document_blocks(doc_id)
            for blk in all_blocks:
                for cid in blk.get("comment_ids", []):
                    comment_id_to_block_id[cid] = blk["block_id"]
        except Exception:
            pass

    # 全局评论 fallback：第一个 TEXT chunk
    first_text_idx = next(
        (i for i, c in enumerate(doc_chunks)
         if c.content_type.value == "text" and not c.content.startswith("[画板]")),
        None
    )

    for comment in comments:
        replies_str = "；".join(comment["replies"])
        is_whole = comment["is_whole"]
        quote = comment["quote"]
        comment_id = comment.get("comment_id", "")

        target_chunk = None

        # 策略 1：comment_id → block_id → chunk（精确）
        if comment_id and comment_id in comment_id_to_block_id:
            bid = comment_id_to_block_id[comment_id]
            target_chunk = block_to_chunk.get(bid)

        # 策略 2：quote 文本匹配（fallback）
        if target_chunk is None and not is_whole and quote:
            for chunk in doc_chunks:
                if quote in chunk.content:
                    target_chunk = chunk
                    break

        # 策略 3：全局评论 → 第一个 TEXT chunk
        if target_chunk is None and is_whole and first_text_idx is not None:
            target_chunk = doc_chunks[first_text_idx]

        if target_chunk is None:
            continue

        if is_whole or not quote:
            prefix = f"[批注] {replies_str}\n"
        else:
            prefix = f'[批注] "{quote}" → {replies_str}\n'
        target_chunk.content = prefix + target_chunk.content


def _build_whiteboard_chunk(
    board_token: str,
    block_id: str,
    client,
    doc_id: str,
    doc_title: str,
    doc_url: str,
    heading_path: str,
    chunk_counter: List[int],
    cfg,
) -> Optional["Chunk"]:
    """获取画板节点文字，组装为 Chunk。

    调用 Board API 取得所有节点，聚合有文字的节点内容。
    以 [画板] 前缀标注，便于 LLM/BM25 评分时识别来源。

    Returns:
        Chunk 或 None（画板无文字节点时）
    """
    # 优先使用 mermaid（保留节点连线语义），fallback 到 flat pipe 格式
    mermaid = client.get_whiteboard_mermaid(board_token)
    if mermaid:
        content = "[画板]\n```mermaid\n" + mermaid + "\n```"
    else:
        nodes = client.get_whiteboard_nodes(board_token)
        if not nodes:
            return None
        seen: set = set()
        unique_texts: List[str] = []
        for node in nodes:
            t = " ".join(node["text"].split())
            if t and t not in seen:
                seen.add(t)
                unique_texts.append(t)
        if not unique_texts:
            return None
        content = "[画板] " + " | ".join(unique_texts)
    chunk_counter[0] += 1
    return Chunk(
        chunk_id=f"{doc_id}_{chunk_counter[0]}",
        doc_id=doc_id,
        doc_title=doc_title,
        doc_url=doc_url,
        heading_path=heading_path,
        content_type=ChunkContentType.TEXT,
        content=content,
        block_ids=[block_id],
        token_count=max(1, len(content) // 2),
        has_table=False,
    )

logger = logging.getLogger(__name__)

# block_types that trigger table processing
TABLE_TYPES = {30, 31, 32}


# ── Stage 6: Budget Allocation ────────────────────────────────────────────────

def allocate_budget(
    all_chunks: List[ChunkResult],
    selected_chunk_ids: List[str],
    cfg: PipelineConfig,
) -> List[ChunkResult]:
    """
    Select chunks by selected_chunk_ids, sort by relevance desc, then hard-truncate
    at total_token_budget.

    Args:
        all_chunks: Full scored chunk list from Stage 5.
        selected_chunk_ids: Chunk IDs chosen by judge_chunk_sufficiency.
        cfg: Pipeline configuration.

    Returns:
        Selected chunks within token budget, sorted by relevance_score desc.
    """
    id_to_chunk: Dict[str, ChunkResult] = {c.chunk_id: c for c in all_chunks}
    selected: List[ChunkResult] = []
    for cid in selected_chunk_ids:
        chunk = id_to_chunk.get(cid)
        if chunk is None:
            logger.warning("allocate_budget: unknown chunk_id %s, skipping", cid)
            continue
        selected.append(chunk)

    selected.sort(key=lambda c: c.relevance_score, reverse=True)

    # Hard token truncation
    result: List[ChunkResult] = []
    total_tokens = 0
    for chunk in selected:
        if total_tokens + chunk.token_count > cfg.total_token_budget:
            break
        result.append(chunk)
        total_tokens += chunk.token_count

    return result


def _truncate_to_budget(chunks: List[ChunkResult], total_token_budget: int) -> List[ChunkResult]:
    """Final token protection after cross-round merging. Chunks must be sorted by score desc."""
    result: List[ChunkResult] = []
    total_tokens = 0
    for chunk in chunks:
        if total_tokens + chunk.token_count > total_token_budget:
            break
        result.append(chunk)
        total_tokens += chunk.token_count
    return result


def _truncate_text(text: str, max_tokens: int) -> str:
    """Truncate text to fit within max_tokens, preferring sentence boundaries."""
    max_chars = max_tokens * 2
    if len(text) <= max_chars:
        return text
    # Try to cut at a sentence boundary
    candidates = []
    for sep in ("。", "！", "？", "\n"):
        pos = text.rfind(sep, 0, max_chars)
        if pos > 0:
            candidates.append(pos + len(sep))
    if candidates:
        return text[: max(candidates)]
    # Hard cut
    return text[:max_chars]


# ── Async helpers for Stage 3 concurrent block fetch ─────────────────────────

class _RateLimiter:
    """Token bucket: at most `rate` calls per second."""
    def __init__(self, rate: int):
        self._rate = rate
        self._tokens = float(rate)
        self._last_refill: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = asyncio.get_event_loop().time()
            if self._last_refill == 0.0:
                self._last_refill = now
            elapsed = now - self._last_refill
            self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
            self._last_refill = now
            if self._tokens >= 1:
                self._tokens -= 1
            else:
                wait = (1 - self._tokens) / self._rate
                await asyncio.sleep(wait)


# Shared RateLimiter across pipeline invocations (app-level QPS cap).
# Initialized lazily in _fetch_all_docs_async(); keeps overall calls <= configured QPS.
_GLOBAL_RATE_LIMITER: Optional[_RateLimiter] = None


async def _fetch_single_doc(
    client,
    doc: Dict,
    rate_limiter: _RateLimiter,
    loop: asyncio.AbstractEventLoop,
    executor: concurrent.futures.ThreadPoolExecutor,
) -> Optional[Dict]:
    """Fetch blocks for one doc under rate-limiter guard. Returns None on failure."""
    doc_id    = doc.get("id", doc.get("document_id", doc.get("doc_id", "")))
    doc_title = doc.get("title", "")
    doc_url   = doc.get("url", doc.get("doc_url", ""))

    # ── [BUG-1 FIX] Wiki-hosted docx: wiki node token ≠ docx document_id
    # MCP search: id = wiki_token, node_id = None
    # Wiki search: id = obj_token (WRONG for wiki API!), node_id = wiki_token
    # SOLUTION: Extract wiki_token from URL (most reliable), then fallback to node_id.
    if "/wiki/" in doc_url:
        # 优先从 URL 提取 wiki token（格式：/wiki/{wiki_token}?xxx#yyy）
        wiki_token = doc_url.split("/wiki/")[-1].split("?")[0].split("#")[0]
        if not wiki_token:
            # Fallback: node_id (wiki search) or id (MCP search)
            wiki_token = doc.get("node_id") or doc.get("id") or doc_id
        if wiki_token:
            try:
                node_info = await loop.run_in_executor(
                    executor, client.get_wiki_node_info, wiki_token
                )
                obj_token = node_info.get("obj_token", "")
                if obj_token:
                    logger.debug("Stage 3: wiki node %s → obj_token %s", wiki_token, obj_token)
                    doc_id = obj_token
                else:
                    logger.warning("Stage 3: wiki node %s returned empty obj_token, skipping", wiki_token)
                    return None
            except Exception as e:
                logger.warning("Stage 3: wiki node resolve failed for %s: %s", wiki_token, e)
                return None

    last_exc: Optional[Exception] = None
    # Retry with exponential backoff on Feishu rate-limit (99991400).
    for attempt in range(5):
        await rate_limiter.acquire()
        try:
            blocks = await loop.run_in_executor(
                executor, client.list_document_blocks, doc_id
            )
            if blocks:
                return {"doc_id": doc_id, "doc_title": doc_title,
                        "doc_url": doc_url, "blocks": blocks}
            return None
        except Exception as e:
            last_exc = e
            err_s = str(e)
            # Detect Feishu app-level rate limit and back off with jitter
            if "99991400" in err_s and attempt < 4:
                import random
                backoff = min(10.0, (2 ** attempt) + random.random())
                logger.warning(
                    "Stage 3: rate-limited on doc %s (attempt %d), backing off %.2fs",
                    doc_id, attempt + 1, backoff,
                )
                await asyncio.sleep(backoff)
                continue
            else:
                break

    logger.warning("Stage 3: failed to fetch doc %s (%s): %s",
                   doc_id, doc_title, last_exc)
    return None


async def _fetch_all_docs_async(
    client,
    filtered_docs: List[Dict],
    max_concurrency: int = 5,
) -> List[Dict]:
    """Fetch blocks for all docs concurrently, rate-limited to max_concurrency req/s."""
    global _GLOBAL_RATE_LIMITER
    # Lazily initialize a shared RateLimiter for the process so multiple
    # pipeline invocations coordinate to respect app-level QPS limits.
    if _GLOBAL_RATE_LIMITER is None:
        _GLOBAL_RATE_LIMITER = _RateLimiter(rate=max_concurrency)
    rate_limiter = _GLOBAL_RATE_LIMITER
    loop         = asyncio.get_event_loop()
    executor     = concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrency)
    tasks = [
        _fetch_single_doc(client, doc, rate_limiter, loop, executor)
        for doc in filtered_docs
    ]
    results = await asyncio.gather(*tasks)
    executor.shutdown(wait=False)
    return [r for r in results if r is not None]


# ── Main Pipeline Entry Point ─────────────────────────────────────────────────

def run_rag_pipeline(
    query: str,
    candidates: List[Dict],
    user_access_token: str,
    cfg: Optional[PipelineConfig] = None,
    seen_doc_ids: Optional[Set[str]] = None,
) -> RAGResult:
    """
    Runs Stage 3-7 of the RAG pipeline (Stage 2 moved to orchestrator).

    Args:
        query: User search query string.
        candidates: List of dicts already filtered by judge_and_filter.
        user_access_token: UAT for Feishu API calls.
        cfg: Pipeline configuration. Uses PipelineConfig() defaults if None.
        seen_doc_ids: Doc IDs already read in prior rounds (skip re-fetching).

    Returns:
        RAGResult with scored_chunks (unclipped). Caller decides how to truncate.
    """
    if cfg is None:
        cfg = PipelineConfig()

    total_docs_searched = len(candidates)

    # Filter out already-seen documents
    filtered_docs = candidates
    if seen_doc_ids:
        filtered_docs = [
            c for c in candidates
            if c.get("id", c.get("document_id", c.get("doc_id", ""))) not in seen_doc_ids
        ]
    if not filtered_docs:
        logger.warning("run_rag_pipeline: all candidates already seen or empty")
        return RAGResult(
            query=query,
            total_docs_searched=total_docs_searched,
            total_docs_read=0,
            total_chunks=0,
            total_tokens=0,
            chunks=[],
        )

    # ── Stage 2: BM25 filter candidates (D2: replaces LLM filter) ────────────
    if not cfg.skip_scoring:
        logger.info("Stage 2: filtering %d candidates with BM25 (max_candidates=%d)",
                    len(filtered_docs), cfg.max_candidates)
        filtered_doc_ids = bm25_filter_candidates(query, filtered_docs, cfg.max_candidates)
        id_set = set(filtered_doc_ids)
        filtered_docs = [
            c for c in filtered_docs
            if c.get("id", c.get("document_id", c.get("doc_id", ""))) in id_set
        ]
        logger.info("Stage 2: %d docs retained after BM25 filter", len(filtered_docs))
    else:
        logger.info("Stage 2: SKIP (skip_scoring=True), passing %d candidates through", len(filtered_docs))

    if not filtered_docs:
        logger.warning("run_rag_pipeline: no docs remain after filter")
        return RAGResult(
            query=query,
            total_docs_searched=total_docs_searched,
            total_docs_read=0,
            total_chunks=0,
            total_tokens=0,
            chunks=[],
        )

    # ── Stage 3: Fetch document blocks (concurrent, QPS-bounded) ─────────────
    logger.info("Stage 3: fetching blocks for %d docs (max_concurrency=%d)",
                len(filtered_docs), cfg.stage3_max_concurrency)
    client = FeishuClient(user_access_token=user_access_token)

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # ADK (or Jupyter) already owns the event loop — run in a worker thread
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    asyncio.run,
                    _fetch_all_docs_async(client, filtered_docs, cfg.stage3_max_concurrency),
                )
                doc_blocks: List[Dict] = future.result()
        else:
            doc_blocks: List[Dict] = loop.run_until_complete(
                _fetch_all_docs_async(client, filtered_docs, cfg.stage3_max_concurrency)
            )
    except RuntimeError:
        doc_blocks: List[Dict] = asyncio.run(
            _fetch_all_docs_async(client, filtered_docs, cfg.stage3_max_concurrency)
        )
    total_docs_read = len(doc_blocks)

    if not doc_blocks:
        logger.warning("Stage 3: no blocks fetched for any doc")
        return RAGResult(
            query=query,
            total_docs_searched=total_docs_searched,
            total_docs_read=0,
            total_chunks=0,
            total_tokens=0,
            chunks=[],
        )

    # ── Stage 4: Build chunks ─────────────────────────────────────────────────
    logger.info("Stage 4: building chunks for %d docs", len(doc_blocks))
    all_chunks: List[Chunk] = []
    chunk_counter = [0]  # mutable counter shared across docs

    for doc in doc_blocks:
        doc_id = doc["doc_id"]
        doc_title = doc["doc_title"]
        doc_url = doc["doc_url"]
        blocks = doc["blocks"]

        # Build lookup dict for cross-referencing child blocks
        blocks_dict = {}
        for b in blocks:
            bid = b.get("block_id") if isinstance(b, dict) else getattr(b, "block_id", None)
            if bid:
                blocks_dict[bid] = b

        consumed_table_block_ids: set = set()
        heading_stack = HeadingStack()
        doc_chunks: List[Chunk] = []

        # Pass 1a: CPU-only blocks (type-31 table, type-40 mermaid) — 串行，<1ms
        # Track headings during pass-1 so table/mermaid chunks get correct heading_path
        from .chunker import HEADING_TYPES, _extract_block_text
        for block in blocks:
            btype = block.get("block_type") if isinstance(block, dict) else getattr(block, "block_type", None)
            bid   = block.get("block_id")   if isinstance(block, dict) else getattr(block, "block_id",   None)

            # Update heading stack for ALL heading blocks (type 3-11)
            if btype in HEADING_TYPES:
                heading_stack.push(btype, _extract_block_text(block))

            if btype == 31:
                try:
                    table_chunks, consumed = build_table_chunk(
                        block=block,
                        blocks_dict=blocks_dict,
                        doc_id=doc_id,
                        doc_title=doc_title,
                        doc_url=doc_url,
                        heading_path=heading_stack.current_path(),
                        chunk_counter=chunk_counter,
                        query=query,
                        llm_caller=_call_llm_table,
                        cfg=cfg,
                    )
                    doc_chunks.extend(table_chunks)
                    consumed_table_block_ids.update(consumed)
                except Exception as e:
                    logger.warning("Stage 4: table chunk (type31) failed block %s: %s", bid, e)

            elif btype == 40:
                # Mermaid flowchart 小组件 (add_ons.record.data)
                mermaid_data = block.get("mermaid_data") if isinstance(block, dict) else None
                if mermaid_data:
                    try:
                        clean = mermaid_data.replace('\xa0', ' ').strip()
                        content = f"[流程图]\n```mermaid\n{clean}\n```"
                        chunk_counter[0] += 1
                        mermaid_chunk = Chunk(
                            chunk_id=f"{doc_id}_{chunk_counter[0]}",
                            doc_id=doc_id,
                            doc_title=doc_title,
                            doc_url=doc_url,
                            heading_path=heading_stack.current_path(),
                            content_type=ChunkContentType.TEXT,
                            content=content,
                            block_ids=[bid],
                            token_count=max(1, len(content) // 2),
                            has_table=False,
                        )
                        doc_chunks.append(mermaid_chunk)
                        consumed_table_block_ids.add(bid)
                    except Exception as e:
                        logger.warning("Stage 4: mermaid chunk (type40) failed block %s: %s", bid, e)

        # Pass 1b: IO-bound blocks (type-30 sheet, type-53 bitable, type-43 whiteboard) + comments — 并行
        # Pre-compute heading_path for each IO block at its document position
        _IO_BLOCK_TYPES = {30, 53, 43}
        _io_hp_map = {}  # block_id → heading_path
        _tmp_hs = HeadingStack()
        for _b in blocks:
            _bt = _b.get("block_type") if isinstance(_b, dict) else getattr(_b, "block_type", None)
            _bi = _b.get("block_id") if isinstance(_b, dict) else getattr(_b, "block_id", None)
            if _bt in HEADING_TYPES:
                _tmp_hs.push(_bt, _extract_block_text(_b))
            if _bt in _IO_BLOCK_TYPES and _bi:
                _io_hp_map[_bi] = _tmp_hs.current_path()

        io_tasks = [("block", block, block.get("block_type"), block.get("block_id", ""))
                     for block in blocks
                     if (block.get("block_type") if isinstance(block, dict) else getattr(block, "block_type", None))
                     in _IO_BLOCK_TYPES]
        
        # 将获取评论也作为一个 IO 任务加入并行池
        io_tasks.append(("comments", None, None, None))
        
        fetched_comments = []

        if io_tasks:
            _IO_PARALLEL = 4  # 3 个 block 并发 + 1 个 comment 并发

            def _process_io_task(args):
                task_type, block, btype, bid = args
                if task_type == "comments":
                    try:
                        # [S1.2] ``get_doc_comments`` 契约为 List[Dict]，但历史 log 曾出现
                        # ``'NoneType' object is not iterable`` —— ``or []`` 做最后一道防线。
                        fetched = client.get_doc_comments(doc_id, file_type="docx") or []
                        return ("comments", fetched)
                    except Exception as e:
                        logger.warning("Stage 4: get_doc_comments failed: %s", e)
                        return ("comments", [])
                
                # 处理 IO blocks
                try:
                    _hp = _io_hp_map.get(bid, "")
                    if btype == 30:
                        return ("chunks", build_sheet_chunk(
                            block=block, client=client, doc_id=doc_id,
                            doc_title=doc_title, doc_url=doc_url,
                            heading_path=_hp,
                            chunk_counter=chunk_counter, query=query,
                            llm_caller=_call_llm_table, cfg=cfg,
                        ))
                    elif btype == 53:
                        bitable_token = block.get("bitable_token") if isinstance(block, dict) else None
                        view_id = block.get("bitable_view_id", "") if isinstance(block, dict) else ""
                        if bitable_token:
                            bt_chunk = _build_bitable_embed_chunk(
                                bitable_token=bitable_token, block_id=bid,
                                uat=user_access_token, doc_id=doc_id,
                                doc_title=doc_title, doc_url=doc_url,
                                heading_path=_hp,
                                chunk_counter=chunk_counter,
                                view_id=view_id,
                            )
                            if bt_chunk:
                                return ("chunks", (bt_chunk, {bid}))
                        return ("chunks", ([], set()))
                    elif btype == 43:
                        board_token = block.get("board_token") if isinstance(block, dict) else None
                        if board_token:
                            wb_chunk = _build_whiteboard_chunk(
                                board_token=board_token, block_id=bid,
                                client=client, doc_id=doc_id,
                                doc_title=doc_title, doc_url=doc_url,
                                heading_path=_hp,
                                chunk_counter=chunk_counter, cfg=cfg,
                            )
                            if wb_chunk:
                                return ("chunks", ([wb_chunk], {bid}))
                        return ("chunks", ([], set()))
                except Exception as e:
                    logger.warning("Stage 4: IO block (type%s) failed block %s: %s", btype, bid, e)
                return ("chunks", ([], set()))

            with concurrent.futures.ThreadPoolExecutor(max_workers=_IO_PARALLEL) as pool:
                futures = [pool.submit(_process_io_task, args) for args in io_tasks]
                for fut in concurrent.futures.as_completed(futures):
                    try:
                        res_type, payload = fut.result()
                        if res_type == "chunks":
                            chunks_result, consumed = payload
                            if chunks_result:
                                doc_chunks.extend(chunks_result)
                                consumed_table_block_ids.update(consumed)
                        elif res_type == "comments":
                            fetched_comments = payload
                    except Exception as e:
                        logger.warning("Stage 4: IO task future failed: %s", e)

        # Pass 2: text blocks (skipping consumed table block IDs)
        try:
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
            doc_chunks.extend(text_chunks)
        except Exception as e:
            logger.warning("Stage 4: text chunking failed for doc %s: %s", doc_id, e)

        # Pass 3: inject doc comments into matching chunks
        # [P2.3-F4] 传入 Stage 3 已获取的 blocks，避免重复 list_document_blocks API 调用
        # [并发优化] 传入前序 IO 池中获取到的 fetched_comments，消除 400ms 的串行耗时
        try:
            _inject_comments(doc_chunks + text_chunks, client, doc_id, blocks=blocks, pre_fetched_comments=fetched_comments)
        except Exception as e:
            logger.warning("Stage 4: comment injection failed for doc %s: %s", doc_id, e)

        all_chunks.extend(doc_chunks)

    # Post-merge: combine adjacent small chunks with same parent heading
    from .chunker import merge_adjacent_chunks
    pre_merge_count = len(all_chunks)
    all_chunks = merge_adjacent_chunks(all_chunks, max_chunk_tokens=cfg.max_chunk_tokens)
    logger.info("Stage 4: built %d chunks total (before merge: %d)", len(all_chunks), pre_merge_count)

    if not all_chunks:
        return RAGResult(
            query=query,
            total_docs_searched=total_docs_searched,
            total_docs_read=total_docs_read,
            total_chunks=0,
            total_tokens=0,
            chunks=[],
        )

    # ── Stage 5: BM25 pre-filter + embedding hybrid score ──────────────────
    if cfg.skip_scoring:
        # DocPool 模式：跳过 Stage 5，直接将 Chunk 转为 ChunkResult（score=0）
        # 评分由 DocPool.wait_and_score 用扩展 BM25 完成
        logger.info("Stage 5: SKIP (skip_scoring=True), converting %d chunks directly", len(all_chunks))
        scored_chunks: List[ChunkResult] = []
        for chunk in all_chunks:
            scored_chunks.append(ChunkResult(
                summary="",
                chunks_content=chunk.content,
                content_type=chunk.content_type,
                file_name=chunk.doc_title,
                file_url=chunk.doc_url,
                chunk_id=chunk.chunk_id,
                heading_path=chunk.heading_path,
                relevance_score=0.0,
                token_count=chunk.token_count,
            ))
    else:
        # [P2.5] BM25 粗筛 → 只对 top-K 做 embedding 精排，节省 ~50% embedding API 时间
        import os as _os
        dashscope_key = _os.getenv("DASHSCOPE_API_KEY", "")
        use_emb = cfg.use_embedding_score and bool(dashscope_key)

        # Step 5a: BM25 快速粗筛（<1ms for 50 chunks）
        from tools.chunk_scorer import bm25_score_batch
        chunk_texts = [c.content for c in all_chunks]
        bm25_raw = bm25_score_batch(query, chunk_texts)

        # 只对 BM25 top-K 做 embedding（K = min(30, total)，覆盖 >95% 有效 chunks）
        _EMB_TOP_K = 30
        if use_emb and len(all_chunks) > _EMB_TOP_K:
            # 按 BM25 排序，取 top-K 的索引
            indexed_scores = sorted(enumerate(bm25_raw), key=lambda x: x[1], reverse=True)
            top_indices = set(i for i, _ in indexed_scores[:_EMB_TOP_K])
            top_texts = [chunk_texts[i] for i in sorted(top_indices)]

            logger.info("Stage 5: BM25 pre-filter %d → %d chunks for embedding",
                         len(all_chunks), len(top_texts))

            # 对 top-K 做 embedding 精排
            top_scores = hybrid_score_chunks(
                query=query, chunk_texts=top_texts,
                use_embedding=True, bm25_weight=cfg.bm25_weight,
                emb_weight=cfg.emb_weight_score, api_key=dashscope_key,
            )

            # 对未进入 top-K 的 chunks 用纯 BM25 归一化
            from tools.chunk_scorer import normalize_scores
            bm25_only_scores = normalize_scores(bm25_raw)

            # 合并分数：top-K 用 hybrid, 其余用 BM25
            scores = list(bm25_only_scores)  # 默认全部用 BM25
            top_sorted = sorted(top_indices)
            for rank, orig_idx in enumerate(top_sorted):
                scores[orig_idx] = top_scores[rank]
        else:
            logger.info("Stage 5: scoring %d chunks with %s",
                         len(all_chunks),
                         "BM25+embedding" if use_emb else "BM25-only")
            scores = hybrid_score_chunks(
                query=query, chunk_texts=chunk_texts,
                use_embedding=use_emb, bm25_weight=cfg.bm25_weight,
                emb_weight=cfg.emb_weight_score, api_key=dashscope_key,
            )

        scored_chunks: List[ChunkResult] = []
        for chunk, score in zip(all_chunks, scores):
            content_type = chunk.content_type
            # Apply table score boost (×1.1)
            effective_score = score * 1.1 if content_type in (
                ChunkContentType.TABLE_MD, ChunkContentType.TABLE_JSON) else score

            scored_chunks.append(ChunkResult(
                summary="",  # BM25 mode: no LLM summary
                chunks_content=chunk.content,
                content_type=content_type,
                file_name=chunk.doc_title,
                file_url=chunk.doc_url,
                chunk_id=chunk.chunk_id,
                heading_path=chunk.heading_path,
                relevance_score=effective_score,
                token_count=chunk.token_count,
            ))

        # Sort by effective score descending
        scored_chunks.sort(key=lambda r: r.relevance_score, reverse=True)

        logger.info("Stage 5: %d chunks scored (top score=%.3f)",
                    len(scored_chunks),
                    scored_chunks[0].relevance_score if scored_chunks else 0)

    # ── Stage 6-7: Return scored_chunks (unclipped) ─────────────────────────
    # Budget allocation (Stage 6) is now done by orchestrator after judge_chunk_sufficiency.
    logger.info("Stage 5 complete: %d scored chunks, returning to caller for budget decisions",
                len(scored_chunks))
    return RAGResult(
        query=query,
        total_docs_searched=total_docs_searched,
        total_docs_read=total_docs_read,
        total_chunks=0,       # filled by caller after budget allocation
        total_tokens=0,       # filled by caller after budget allocation
        chunks=scored_chunks,
    )

