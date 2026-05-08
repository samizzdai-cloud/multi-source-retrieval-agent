"""FeishuDocPool — 跨 sub_query 共享的飞书文档读取池

核心职责：
  1. 文档去重：多个 sub_query 搜到相同 doc_id → 只读一次
  2. API 调度：Block API 4/s rate limiter + MCP 无限频并发
  3. reader 调度：按候选上的 ``route_hint`` 分配到 Block API / MCP
  4. 渐进缓存：一篇文档 chunk 完立即可用，不等全部完成
  5. 原始 chunk 回收：等待指定 doc_id 完成后，返回原始 UnifiedChunk 列表

生命周期：RetrievalAgent._run_async_impl 创建一个实例，传给所有 feishu task。
"""

import asyncio
import concurrent.futures
import contextvars
import functools
import logging
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set

from feishu_read_tool.pipeline_models import Chunk, PipelineConfig

if TYPE_CHECKING:
    from tools.core.models import UnifiedChunk

logger = logging.getLogger(__name__)

# ── 路由参数 ─────────────────────────────────────────────────────

BLOCK_API_CONCURRENCY = 4  # 提升并发，减少排队等待（原为3）
MCP_CONCURRENCY = 6        # 飞书 MCP 接口 QPS 上限（避免 NETWORK:5002 频控）
MCP_RETRY_BACKOFF_S = 0.8  # MCP 失败重试 1 次时的 backoff
MCP_MAX_ATTEMPTS = 2       # 初次 + 1 次重试 = 共 2 次


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


# ── FeishuDocPool ────────────────────────────────────────────────

class FeishuDocPool:
    """跨 sub_query 共享的飞书文档读取池。

    v5：doc_pool 退化为 reader scheduler + raw chunk collector。
    - ``submit_and_read`` 信任候选上的 ``route_hint``，不再自行推断 reader 路由
    - ``wait_and_collect`` 只等待 doc 完成并返回原始 UnifiedChunk，不再做 BM25 /
      embedding / noise_threshold / max_per_doc / max_output
    - 跨 sub_query 的 doc 缓存 (``_doc_chunks`` / ``_done`` / ``_reading``) 与
      Block API 全局并发 semaphore 仍在 pool 级共享（缓存决定语义不受 query_type 影响）
    """

    def __init__(
        self,
        uat: str,
        query_type: str = "structured",
        executor: Optional[concurrent.futures.Executor] = None,
    ):
        self.uat = uat
        self.default_query_type = query_type
        # 注入的源专属线程池；None 时走 asyncio 默认全局池（向后兼容）
        self._executor = executor

        # 共享缓存: doc_id → List[Chunk]
        self._doc_chunks: Dict[str, List[Chunk]] = {}
        # doc_id → doc metadata (title, url, doc_type)
        self._doc_meta: Dict[str, dict] = {}
        # doc_id → 读取路径 ("block_api" | "mcp")
        self._doc_route: Dict[str, str] = {}
        # 正在读取中的 doc_ids (防止重复调度)
        self._reading: Set[str] = set()
        # 已完成的 doc_ids
        self._done: Set[str] = set()
        # 全局锁
        self._lock = asyncio.Lock()
        # Block API 全局并发控制（跨所有 sub_query 共享，防止 99991400 限频）
        self._block_api_sem = asyncio.Semaphore(BLOCK_API_CONCURRENCY)
        # MCP 全局并发控制（QPS=6，防止 NETWORK:5002 request trigger frequency limit）
        self._mcp_sem = asyncio.Semaphore(MCP_CONCURRENCY)
        # doc 完成通知 (渐进式：一篇完成立即通知等待的 task)
        self._doc_ready_events: Dict[str, asyncio.Event] = {}
        # doc_id → 第一个请求该文档的 sub_query（用于 Stage 4 大表 LLM 提取）
        self._doc_query: Dict[str, str] = {}

    async def _to_thread(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """走注入 executor 的 to_thread；executor=None 时退回 asyncio 默认。

        与 ``asyncio.to_thread`` 同义：保留 contextvars 传播。
        """
        if self._executor is None:
            return await asyncio.to_thread(fn, *args, **kwargs)
        loop = asyncio.get_event_loop()
        ctx = contextvars.copy_context()
        func_call = functools.partial(ctx.run, fn, *args, **kwargs)
        return await loop.run_in_executor(self._executor, func_call)

    def _get_doc_id(self, candidate: dict) -> str:
        return candidate.get("id", candidate.get("document_id", candidate.get("doc_id", "")))

    def _merge_doc_meta(self, existing: Optional[dict], candidate: dict) -> dict:
        existing = dict(existing or {})
        candidate_route = candidate.get("route_hint") or "block_api"
        existing_route = existing.get("route_hint") or candidate_route
        merged_route = "block_api" if "block_api" in {existing_route, candidate_route} else "mcp"
        read_query = existing.get("read_query") or candidate.get("read_query", "")
        if candidate_route == "block_api" and existing_route != "block_api":
            read_query = candidate.get("read_query", "") or read_query
        merged = {
            "title": existing.get("title") or candidate.get("title", ""),
            "url": existing.get("url") or candidate.get("url", ""),
            "doc_type": existing.get("doc_type") or candidate.get("doc_type", "DOCX"),
            "summary": existing.get("summary") or candidate.get("summary", ""),
            "_source": existing.get("_source") or candidate.get("_source", ""),
            "route_hint": merged_route,
            "read_query": read_query,
            "selected_focus_ids": sorted(
                set(existing.get("selected_focus_ids") or []) | set(candidate.get("selected_focus_ids") or [])
            ),
            "selected_sub_query_ids": sorted(
                set(existing.get("selected_sub_query_ids") or []) | set(candidate.get("selected_sub_query_ids") or [])
            ),
            "selected_feishu_urls": sorted(
                set(existing.get("selected_feishu_urls") or []) | set(candidate.get("selected_feishu_urls") or [])
            ),
            "selected_query_types": sorted(
                set(existing.get("selected_query_types") or []) | set(candidate.get("selected_query_types") or [])
            ),
            "candidate_rerank_score": max(
                float(existing.get("candidate_rerank_score", 0.0) or 0.0),
                float(candidate.get("candidate_rerank_score", 0.0) or 0.0),
            ),
        }
        return merged

    async def submit_and_read(
        self, candidates: List[dict], query: str,
        query_type: Optional[str] = None,
    ) -> List[str]:
        """注册已经筛选好的候选文档，触发读取。返回选中的 doc_ids。

        非阻塞：已缓存的 doc 不重复读取；未缓存的启动异步读取。
        调用方随后用 wait_and_collect() 等待 + 回收原始 chunks。

        Args:
            candidates: 候选文档列表
            query: 候选未携带 ``read_query`` 时的兜底 query
            query_type: 保留兼容签名；reader 路由不再由该字段决定
        """
        started = time.perf_counter()
        selected_ids = []
        selected_seen: Set[str] = set()
        to_read_block: List[dict] = []
        to_read_mcp: List[dict] = []
        # [S2.2-FIX] 只记录"本次 submit 真正新登记到 _reading 的 doc_id"。
        # 之前把 `uncached = [... if did in self._reading]` 作为路由依据，会把
        # *其他 sub_query 正在读的同一篇 doc* 也当成 new，导致重复启动
        # _read_block_api_batch task、重复打 list_document_blocks API。
        newly_registered: List[str] = []

        async with self._lock:
            for c in candidates:
                doc_id = self._get_doc_id(c)
                if not doc_id:
                    continue
                if doc_id not in selected_seen:
                    selected_ids.append(doc_id)
                    selected_seen.add(doc_id)
                # 缓存 meta
                self._doc_meta[doc_id] = self._merge_doc_meta(self._doc_meta.get(doc_id), c)
                desired_route = self._doc_meta[doc_id].get("route_hint", "block_api")
                # 记录第一个请求该文档的 sub_query（用于 Stage 4 大表 LLM）
                if doc_id not in self._doc_query:
                    self._doc_query[doc_id] = self._doc_meta[doc_id].get("read_query") or query
                # 已完成或正在读 → 跳过（不登记、不调度）
                if doc_id in self._done or doc_id in self._reading:
                    existing_route = self._doc_route.get(doc_id, "unknown")
                    if desired_route == "block_api" and existing_route == "mcp":
                        logger.info(
                            "DocPool.submit_and_read: stronger route requested for doc=%s but reuse existing route=%s",
                            doc_id[:16], existing_route,
                        )
                    continue
                # 创建完成 event
                if doc_id not in self._doc_ready_events:
                    self._doc_ready_events[doc_id] = asyncio.Event()
                self._reading.add(doc_id)
                newly_registered.append(doc_id)

            # [S2.2-FIX] 路由 *仅* 对本次 submit 新登记的 doc 生效；
            # 其余 selected_ids（done / 他人 reading）不再重新调度读取。
            uncached = newly_registered

        # 在锁外调度读取（避免持锁 IO）
        if uncached:
            for did in uncached:
                c = self._doc_meta[did]
                route = c.get("route_hint", "block_api")
                self._doc_route[did] = route
                if route == "mcp":
                    to_read_mcp.append({"id": did, **c})
                else:
                    to_read_block.append({"id": did, **c})

            # 启动异步读取（不 await，让调用方通过 event 等待）
            if to_read_block:
                asyncio.create_task(self._read_block_api_batch(to_read_block))
            if to_read_mcp:
                asyncio.create_task(self._read_mcp_batch(to_read_mcp))

        # 观测分桶统计（debug 级，排查重复读时再开）
        if logger.isEnabledFor(logging.DEBUG):
            n_done = sum(1 for did in selected_ids if did in self._done)
            n_reading = sum(1 for did in selected_ids if did in self._reading)
            logger.debug(
                "DocPool.submit_and_read: query=%.20r → %d preselected (done=%d reading=%d new_block=%d new_mcp=%d)",
                query, len(selected_ids), n_done, n_reading, len(to_read_block), len(to_read_mcp),
            )
        logger.info(
            "TIMING doc_pool.submit selected=%d new_block=%d new_mcp=%d ms=%.1f",
            len(selected_ids),
            len(to_read_block),
            len(to_read_mcp),
            _elapsed_ms(started),
        )
        return selected_ids

    async def wait_and_collect(
        self,
        doc_ids: List[str],
        total_timeout: float = 12.0,
    ) -> "List[UnifiedChunk]":
        """等待指定文档就绪，返回原始 UnifiedChunks。"""
        started = time.perf_counter()
        from tools.core.models import UnifiedChunk

        pending = set(doc_ids)
        collected_by_doc: Dict[str, List[UnifiedChunk]] = {}
        collected_docs = 0
        deadline = time.monotonic() + total_timeout

        while pending and time.monotonic() < deadline:
            newly_done = []
            for doc_id in list(pending):
                event = self._doc_ready_events.get(doc_id)
                if event is None or event.is_set():
                    newly_done.append(doc_id)

            for doc_id in newly_done:
                pending.discard(doc_id)
                chunks = self._doc_chunks.get(doc_id, [])
                if not chunks:
                    collected_by_doc[doc_id] = []
                    continue

                meta = self._doc_meta.get(doc_id, {})
                collected_docs += 1
                doc_chunks: List[UnifiedChunk] = []
                for chunk in chunks:
                    uc = UnifiedChunk(
                        text=chunk.content,
                        source_name=meta.get("title", chunk.doc_title),
                        source_url=meta.get("url", chunk.doc_url),
                        source_tool="feishu",
                        heading_path=chunk.heading_path,
                        score=0.0,
                        metadata={
                            "content_type": chunk.content_type.value if hasattr(chunk.content_type, 'value') else str(chunk.content_type),
                            "doc_id": doc_id,
                            "route": self._doc_route.get(doc_id, "unknown"),
                            "reader_score": 0.0,
                            "selected_focus_ids": list(meta.get("selected_focus_ids") or []),
                            "selected_sub_query_ids": list(meta.get("selected_sub_query_ids") or []),
                            "selected_feishu_urls": list(meta.get("selected_feishu_urls") or []),
                            "selected_query_types": list(meta.get("selected_query_types") or []),
                            "candidate_rerank_score": float(meta.get("candidate_rerank_score", 0.0) or 0.0),
                        },
                    )
                    doc_chunks.append(uc)
                collected_by_doc[doc_id] = doc_chunks

            if not newly_done and pending:
                await asyncio.sleep(0.15)

        if pending:
            logger.warning(
                "DocPool.wait_and_collect: total_timeout %.1fs — %d/%d docs still pending, returning %d collected",
                total_timeout, len(pending), len(doc_ids), collected_docs,
            )

        result: List[UnifiedChunk] = []
        for doc_id in doc_ids:
            result.extend(collected_by_doc.get(doc_id, []))

        logger.info(
            "DocPool.wait_and_collect: %d chunks from %d/%d docs (timeout=%.1fs)",
            len(result), collected_docs, len(doc_ids), total_timeout,
        )
        logger.info(
            "TIMING doc_pool.wait_and_collect docs=%d collected_docs=%d chunks=%d ms=%.1f",
            len(doc_ids),
            collected_docs,
            len(result),
            _elapsed_ms(started),
        )
        return result

    # ── Block API 批量读取 ───────────────────────────────────────

    async def _read_block_api_batch(self, docs: List[dict]) -> None:
        """Block API 读取 + chunking，逐篇完成后立即通知。"""
        from feishu_read_tool.client import FeishuClient
        from feishu_read_tool.docx_pipeline import run_rag_pipeline

        # 使用 pool 级别的 semaphore（而非每次调用新建），确保跨 sub_query 并发安全
        sem = self._block_api_sem

        async def _read_one(doc: dict):
            doc_id = doc["id"]
            doc_started = time.perf_counter()
            async with sem:
                for attempt in range(3):
                    try:
                        # run_rag_pipeline 是同步的，放线程池
                        # 传真实 sub_query（用于 Stage 4 大表 LLM 提取），跳过 Stage 2+5 评分
                        rag = await self._to_thread(
                            run_rag_pipeline,
                            query=self._doc_query.get(doc_id, ""),
                            candidates=[doc],
                            user_access_token=self.uat,
                            cfg=PipelineConfig(max_candidates=1, skip_scoring=True),
                        )
                        # 转换 ChunkResult → Chunk（保留原始格式）
                        chunks = []
                        for cr in rag.chunks:
                            ct = cr.content_type
                            ct_val = ct.value if hasattr(ct, 'value') else str(ct)
                            chunks.append(Chunk(
                                chunk_id=cr.chunk_id,
                                doc_id=doc_id,
                                doc_title=cr.file_name or doc.get("title", ""),
                                doc_url=cr.file_url or doc.get("url", ""),
                                heading_path=cr.heading_path or "",
                                content_type=ct,
                                content=cr.chunks_content or cr.summary or "",
                                block_ids=[],
                                token_count=cr.token_count,
                                has_table=ct_val in ("table_md", "table_json"),
                            ))
                        self._doc_chunks[doc_id] = chunks
                        self._done.add(doc_id)
                        self._reading.discard(doc_id)
                        evt = self._doc_ready_events.get(doc_id)
                        if evt:
                            evt.set()  # 通知等待的 task
                        logger.info("DocPool: block_api done %s → %d chunks", doc_id[:16], len(chunks))
                        logger.info(
                            "TIMING doc_pool.read_block_api doc=%s chunks=%d ms=%.1f",
                            doc_id[:16],
                            len(chunks),
                            _elapsed_ms(doc_started),
                        )
                        return
                    except Exception as e:
                        if attempt < 2:
                            await asyncio.sleep(0.5 * (attempt + 1))
                            continue
                        logger.warning("DocPool: block_api failed %s after 3 retries: %s", doc_id[:16], e)
                        self._doc_chunks[doc_id] = []
                        self._done.add(doc_id)
                        self._reading.discard(doc_id)
                        evt = self._doc_ready_events.get(doc_id)
                        if evt:
                            evt.set()

        tasks = [_read_one(doc) for doc in docs]
        await asyncio.gather(*tasks, return_exceptions=True)

    # ── MCP 批量读取 ─────────────────────────────────────────────

    async def _read_mcp_batch(self, docs: List[dict]) -> None:
        """MCP 高并发读取 + parse → chunks，逐篇完成后立即通知。"""
        from feishu_read_tool.basic_tool.mcp_fetch_doc import fetch_doc_markdown
        from feishu_read_tool.basic_tool.mcp_fetch_doc import parse_mcp_markdown_to_chunks

        async def _read_one(doc: dict):
            doc_id = doc["id"]
            doc_started = time.perf_counter()
            # MCP_MAX_ATTEMPTS = 2 → 初次 + 1 次重试；每次都过 QPS 信号量
            for attempt in range(MCP_MAX_ATTEMPTS):
                try:
                    async with self._mcp_sem:
                        result = await self._to_thread(
                            fetch_doc_markdown, doc_id, self.uat, 300000,
                        )
                    if result["status"] != "success":
                        if attempt < MCP_MAX_ATTEMPTS - 1:
                            logger.info(
                                "DocPool: mcp retry %s (attempt=%d) reason=%s",
                                doc_id[:16], attempt + 1, result.get("message", "")[:80],
                            )
                            await asyncio.sleep(MCP_RETRY_BACKOFF_S)
                            continue
                        logger.warning("DocPool: mcp failed %s: %s", doc_id[:16], result.get("message"))
                        self._doc_chunks[doc_id] = []
                        break

                    chunks = parse_mcp_markdown_to_chunks(
                        markdown=result["content"],
                        doc_id=doc_id,
                        doc_title=doc.get("title", ""),
                        doc_url=doc.get("url", ""),
                        max_chunk_tokens=800,
                    )
                    self._doc_chunks[doc_id] = chunks
                    logger.info("DocPool: mcp done %s → %d chunks", doc_id[:16], len(chunks))
                    logger.info(
                        "TIMING doc_pool.read_mcp doc=%s chunks=%d ms=%.1f",
                        doc_id[:16],
                        len(chunks),
                        _elapsed_ms(doc_started),
                    )
                    break
                except Exception as e:
                    if attempt < MCP_MAX_ATTEMPTS - 1:
                        logger.info(
                            "DocPool: mcp retry %s (attempt=%d) exc=%s",
                            doc_id[:16], attempt + 1, type(e).__name__,
                        )
                        await asyncio.sleep(MCP_RETRY_BACKOFF_S)
                        continue
                    logger.warning(
                        "DocPool: mcp failed %s after %d attempts: %s",
                        doc_id[:16], MCP_MAX_ATTEMPTS, e,
                    )
                    self._doc_chunks[doc_id] = []

            self._done.add(doc_id)
            self._reading.discard(doc_id)
            evt = self._doc_ready_events.get(doc_id)
            if evt:
                evt.set()

        tasks = [_read_one(doc) for doc in docs]
        await asyncio.gather(*tasks, return_exceptions=True)

    # ── 状态查询 ─────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        return {
            "cached": len(self._done),
            "reading": len(self._reading),
            "total_chunks": sum(len(v) for v in self._doc_chunks.values()),
            "routes": dict(self._doc_route),
        }
