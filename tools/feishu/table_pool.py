"""TablePool — 跨 sub_query 共享的表格读取池。

核心职责：
  1. 前置 budget：按 focus_id 控制每类表格的读取上限
  2. 去重与缓存：多个 sub_query/loop 命中同一父文件 → 只读一次
  3. 结构缓存：sheet tabs / bitable tables 元数据复用
  4. 调度：按 API 原始顺序安排 reader
  5. 原始 chunk 回收：返回已读父文件的 raw UnifiedChunk
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import functools
import logging
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Set

from feishu_read_tool.client import FeishuClient
from feishu_read_tool.basic_tool.bitable_read import list_tables
from feishu_read_tool.rate_limiter import PriorityTokenBucket

from tools.core.models import UnifiedChunk

logger = logging.getLogger(__name__)

MAX_SHEETS_PER_FOCUS = 3
MAX_BITABLES_PER_FOCUS = 3
SHEET_STRUCTURE_CONCURRENCY = 10   # QPS 充足但避免 429，适度并发
SHEET_STRUCTURE_QPS = 100.0 / 60.0
SHEET_STRUCTURE_BUCKET_CAPACITY = 2
BITABLE_STRUCTURE_CONCURRENCY = 10  # QPS 充足但避免 429，适度并发
SHEET_READ_CONCURRENCY = 6   # sheet API QPS 充足，放宽并发
BITABLE_READ_CONCURRENCY = 6  # bitable API QPS 充足，放宽并发


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def _merge_unique_strs(*values: List[str]) -> List[str]:
    merged: List[str] = []
    seen = set()
    for items in values:
        for item in items or []:
            if not item or item in seen:
                continue
            seen.add(item)
            merged.append(item)
    return merged


class TablePool:
    """跨 sub_query 共享的表格读取池。"""

    def __init__(
        self,
        uat: str,
        max_sheets: int = MAX_SHEETS_PER_FOCUS,
        max_bitables: int = MAX_BITABLES_PER_FOCUS,
        executor: Optional[concurrent.futures.Executor] = None,
    ):
        self.uat = uat
        self.max_sheets = max_sheets
        self.max_bitables = max_bitables
        # 注入的源专属线程池；None 时走 asyncio 默认全局池
        self._executor = executor

        self._cache: Dict[str, List[UnifiedChunk]] = {}
        self._reading: Set[str] = set()
        self._done: Set[str] = set()
        self._ready_events: Dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

        self._parent_meta: Dict[str, dict] = {}
        self._sheet_count_by_focus: Dict[str, int] = defaultdict(int)
        self._bitable_count_by_focus: Dict[str, int] = defaultdict(int)

        self._sheet_struct_cache: Dict[str, List[dict]] = {}
        self._bitable_struct_cache: Dict[str, List[dict]] = {}
        # single-flight 事件：同 token 的并发请求等待第一个完成，避免重复 API 调用
        self._sheet_struct_inflight: Dict[str, asyncio.Event] = {}
        self._bitable_struct_inflight: Dict[str, asyncio.Event] = {}
        self._sheet_struct_sem = asyncio.Semaphore(SHEET_STRUCTURE_CONCURRENCY)
        self._sheet_struct_bucket = PriorityTokenBucket(
            qps=SHEET_STRUCTURE_QPS,
            capacity=SHEET_STRUCTURE_BUCKET_CAPACITY,
            name="feishu_sheet_structure",
        )
        self._bitable_struct_sem = asyncio.Semaphore(BITABLE_STRUCTURE_CONCURRENCY)
        self._sheet_read_sem = asyncio.Semaphore(SHEET_READ_CONCURRENCY)
        self._bitable_read_sem = asyncio.Semaphore(BITABLE_READ_CONCURRENCY)
        # Detached reader tasks and executor futures are kept visible to the
        # request cancellation gate; cancelling a waiter cannot stop HTTP in
        # its underlying thread.
        self._inflight_futures: Set[asyncio.Future] = set()
        self._read_tasks: Set[asyncio.Task] = set()

    async def _to_thread(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """走注入 executor 的 to_thread；executor=None 时退回默认池。保留 contextvars 传播。"""
        loop = asyncio.get_event_loop()
        ctx = contextvars.copy_context()
        func_call = functools.partial(ctx.run, fn, *args, **kwargs)
        future = loop.run_in_executor(self._executor, func_call)
        self._inflight_futures.add(future)
        future.add_done_callback(self._inflight_futures.discard)
        return await asyncio.shield(future)

    def _start_read_task(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._read_tasks.add(task)
        task.add_done_callback(self._read_tasks.discard)

    async def drain_inflight(self, timeout_s: float = 10.0) -> int:
        pending: Set[asyncio.Future] = {
            future for future in self._inflight_futures if not future.done()
        } | {task for task in self._read_tasks if not task.done()}
        if not pending:
            return 0
        _done, remaining = await asyncio.wait(pending, timeout=max(0.0, timeout_s))
        return len(remaining)

    @property
    def stats(self) -> Dict:
        return {
            "cached": len(self._cache),
            "reading": len(self._reading),
            "sheets_selected_by_focus": dict(self._sheet_count_by_focus),
            "bitables_selected_by_focus": dict(self._bitable_count_by_focus),
            "sheet_structure_cached": len(self._sheet_struct_cache),
            "bitable_structure_cached": len(self._bitable_struct_cache),
        }

    def _token_of(self, candidate: dict) -> str:
        return (
            candidate.get("id")
            or candidate.get("document_id")
            or candidate.get("doc_id")
            or candidate.get("url")
            or ""
        )

    def _focus_ids_of(self, candidate: dict) -> List[str]:
        return [str(fid) for fid in (candidate.get("selected_focus_ids") or []) if fid]

    def _merge_parent_meta(self, existing: Optional[dict], candidate: dict) -> dict:
        existing = dict(existing or {})
        read_queries = list(existing.get("_read_queries") or [])
        new_query = str(candidate.get("read_query", "") or "").strip()
        if new_query and new_query not in read_queries:
            read_queries.append(new_query)

        merged = {
            "id": existing.get("id") or candidate.get("id", ""),
            "title": existing.get("title") or candidate.get("title", ""),
            "url": existing.get("url") or candidate.get("url", ""),
            "doc_type": existing.get("doc_type") or candidate.get("doc_type", ""),
            "_source": existing.get("_source") or candidate.get("_source", ""),
            "_sources": _merge_unique_strs(
                list(existing.get("_sources") or []),
                list(candidate.get("_sources") or []),
                [candidate.get("_source", "")] if candidate.get("_source") else [],
            ),
            "node_id": existing.get("node_id") or candidate.get("node_id", ""),
            "space_id": existing.get("space_id") or candidate.get("space_id", ""),
            "selected_focus_ids": _merge_unique_strs(
                list(existing.get("selected_focus_ids") or []),
                list(candidate.get("selected_focus_ids") or []),
            ),
            "selected_sub_query_ids": _merge_unique_strs(
                list(existing.get("selected_sub_query_ids") or []),
                list(candidate.get("selected_sub_query_ids") or []),
            ),
            "selected_feishu_urls": _merge_unique_strs(
                list(existing.get("selected_feishu_urls") or []),
                list(candidate.get("selected_feishu_urls") or []),
            ),
            "selected_query_types": sorted(
                set(existing.get("selected_query_types") or [])
                | set(candidate.get("selected_query_types") or [])
            ),
            "selected_sheet_ids": _merge_unique_strs(
                list(existing.get("selected_sheet_ids") or []),
                list(candidate.get("selected_sheet_ids") or []),
            ),
            "selected_sheet_titles": _merge_unique_strs(
                list(existing.get("selected_sheet_titles") or []),
                list(candidate.get("selected_sheet_titles") or []),
            ),
            "selected_table_ids": _merge_unique_strs(
                list(existing.get("selected_table_ids") or []),
                list(candidate.get("selected_table_ids") or []),
            ),
            "selected_table_titles": _merge_unique_strs(
                list(existing.get("selected_table_titles") or []),
                list(candidate.get("selected_table_titles") or []),
            ),
            "cross_query_hit_count": int(existing.get("cross_query_hit_count", 1) or 1)
            + int(candidate.get("cross_query_hit_count", 1) or 1),
            "best_api_rank": min(
                int(existing.get("best_api_rank", 10**9) or 10**9),
                int(candidate.get("best_api_rank", 10**9) or 10**9),
            ),
            "first_seen_order": min(
                int(existing.get("first_seen_order", 10**9) or 10**9),
                int(candidate.get("first_seen_order", 10**9) or 10**9),
            ),
            "table_selected_child_count": max(
                int(existing.get("table_selected_child_count", 0) or 0),
                int(candidate.get("table_selected_child_count", 0) or 0),
                len(existing.get("selected_sheet_ids") or []),
                len(existing.get("selected_table_ids") or []),
                len(candidate.get("selected_sheet_ids") or []),
                len(candidate.get("selected_table_ids") or []),
            ),
            "table_structure_child_count": max(
                int(existing.get("table_structure_child_count", 0) or 0),
                int(candidate.get("table_structure_child_count", 0) or 0),
            ),
            "table_read_ms": float(existing.get("table_read_ms", 0.0) or 0.0),
            "table_read_chunks": int(existing.get("table_read_chunks", 0) or 0),
            "table_submit_selected": bool(existing.get("table_submit_selected", False)),
            "table_submit_skipped": bool(existing.get("table_submit_skipped", False)),
            "_read_queries": read_queries,
        }
        merged["read_query"] = " ".join(read_queries[:3]).strip()
        return merged

    def _mark_parent_submit_state(self, token: str, *, selected: bool, skipped: bool = False) -> None:
        meta = dict(self._parent_meta.get(token) or {})
        meta["table_submit_selected"] = bool(selected or meta.get("table_submit_selected", False))
        meta["table_submit_skipped"] = bool(skipped or meta.get("table_submit_skipped", False))
        self._parent_meta[token] = meta

    def _mark_parent_read_result(self, token: str, *, read_ms: float, chunk_count: int) -> None:
        meta = dict(self._parent_meta.get(token) or {})
        meta["table_read_ms"] = float(read_ms)
        meta["table_read_chunks"] = int(chunk_count)
        self._parent_meta[token] = meta

    def _has_budget_for_candidate(self, candidate: dict) -> bool:
        doc_type = str(candidate.get("doc_type", "")).upper()
        focus_ids = self._focus_ids_of(candidate)
        if not focus_ids:
            return True
        if doc_type == "SHEET":
            return any(self._sheet_count_by_focus[fid] < self.max_sheets for fid in focus_ids)
        if doc_type == "BITABLE":
            return any(self._bitable_count_by_focus[fid] < self.max_bitables for fid in focus_ids)
        return True

    def _consume_budget(self, candidate: dict) -> None:
        doc_type = str(candidate.get("doc_type", "")).upper()
        for fid in self._focus_ids_of(candidate):
            if doc_type == "SHEET" and self._sheet_count_by_focus[fid] < self.max_sheets:
                self._sheet_count_by_focus[fid] += 1
            elif doc_type == "BITABLE" and self._bitable_count_by_focus[fid] < self.max_bitables:
                self._bitable_count_by_focus[fid] += 1

    async def get_sheet_structure(self, candidate: dict) -> List[dict]:
        started = time.perf_counter()
        token = self._token_of(candidate)
        if not token:
            return []
        # 1) 缓存命中（最快路径）
        if token in self._sheet_struct_cache:
            rows = list(self._sheet_struct_cache[token])
            logger.info(
                "TIMING table_pool.sheet_structure token=%s count=%d ms=%.1f cache=hit",
                token[:16], len(rows), _elapsed_ms(started),
            )
            return rows
        # 2) single-flight：已有同 token 在飞行 → 等它完成再取缓存
        inflight = self._sheet_struct_inflight.get(token)
        if inflight is not None:
            await inflight.wait()
            rows = list(self._sheet_struct_cache.get(token, []))
            logger.info(
                "TIMING table_pool.sheet_structure token=%s count=%d ms=%.1f cache=inflight",
                token[:16], len(rows), _elapsed_ms(started),
            )
            return rows
        # 3) 我是首发请求：注册 inflight 事件，发起 API
        evt = asyncio.Event()
        self._sheet_struct_inflight[token] = evt
        try:
            async with self._sheet_struct_sem:
                if token in self._sheet_struct_cache:                # 双检
                    return list(self._sheet_struct_cache[token])
                try:
                    client = FeishuClient(user_access_token=self.uat)
                    tabs = await self._to_thread(client.query_all_sheets_metadata, token)
                    visible_tabs = [
                        t for t in tabs
                        if not t.get("hidden") and t.get("resource_type") == "sheet"
                    ]
                    self._sheet_struct_cache[token] = list(visible_tabs)
                except Exception as e:
                    logger.warning("table_pool: sheet structure fetch failed for %s: %s", token[:16], e)
                    self._sheet_struct_cache[token] = []
        finally:
            evt.set()
            self._sheet_struct_inflight.pop(token, None)
        rows = list(self._sheet_struct_cache.get(token, []))
        logger.info(
            "TIMING table_pool.sheet_structure token=%s count=%d ms=%.1f cache=miss",
            token[:16], len(rows), _elapsed_ms(started),
        )
        return rows

    async def get_bitable_structure(self, candidate: dict) -> List[dict]:
        started = time.perf_counter()
        token = self._token_of(candidate)
        if not token:
            return []
        # 1) 缓存命中
        if token in self._bitable_struct_cache:
            rows = list(self._bitable_struct_cache[token])
            logger.info(
                "TIMING table_pool.bitable_structure token=%s count=%d ms=%.1f cache=hit",
                token[:16], len(rows), _elapsed_ms(started),
            )
            return rows
        # 2) single-flight：等待已在飞行的同 token
        inflight = self._bitable_struct_inflight.get(token)
        if inflight is not None:
            await inflight.wait()
            rows = list(self._bitable_struct_cache.get(token, []))
            logger.info(
                "TIMING table_pool.bitable_structure token=%s count=%d ms=%.1f cache=inflight",
                token[:16], len(rows), _elapsed_ms(started),
            )
            return rows
        # 3) 首发请求
        evt = asyncio.Event()
        self._bitable_struct_inflight[token] = evt
        try:
            async with self._bitable_struct_sem:
                if token in self._bitable_struct_cache:
                    return list(self._bitable_struct_cache[token])
                try:
                    tables = await self._to_thread(list_tables, token, self.uat)
                    self._bitable_struct_cache[token] = list(tables)
                except Exception as e:
                    logger.warning("table_pool: bitable structure fetch failed for %s: %s", token[:16], e)
                    self._bitable_struct_cache[token] = []
        finally:
            evt.set()
            self._bitable_struct_inflight.pop(token, None)
        rows = list(self._bitable_struct_cache.get(token, []))
        logger.info(
            "TIMING table_pool.bitable_structure token=%s count=%d ms=%.1f cache=miss",
            token[:16], len(rows), _elapsed_ms(started),
        )
        return rows

    async def submit_and_read(self, candidates: List[dict]) -> List[str]:
        """注册已选中的表格父文件，返回本轮可管理的父文件 token 列表。"""
        started = time.perf_counter()
        if not candidates:
            return []

        ordered_candidates = sorted(
            candidates,
            key=lambda c: (
                -int(c.get("cross_query_hit_count", 1) or 1),
                int(c.get("best_api_rank", 10**9) or 10**9),
                int(c.get("first_seen_order", 10**9) or 10**9),
                str(c.get("title", "") or ""),
            ),
        )

        selected_ids: List[str] = []
        to_read_sheet: List[dict] = []
        to_read_bitable: List[dict] = []

        async with self._lock:
            for candidate in ordered_candidates:
                token = self._token_of(candidate)
                if not token:
                    continue
                merged_meta = self._merge_parent_meta(self._parent_meta.get(token), candidate)
                self._parent_meta[token] = merged_meta

                if token in self._done or token in self._reading:
                    self._mark_parent_submit_state(token, selected=True)
                    selected_ids.append(token)
                    continue
                if not self._has_budget_for_candidate(merged_meta):
                    self._mark_parent_submit_state(token, selected=False, skipped=True)
                    continue

                self._consume_budget(merged_meta)
                self._reading.add(token)
                self._ready_events[token] = asyncio.Event()
                self._mark_parent_submit_state(token, selected=True)
                selected_ids.append(token)

                doc_type = str(merged_meta.get("doc_type", "")).upper()
                # 修复1: 把已缓存的结构信息注入 payload，reader 可跳过重复 API 调用
                payload = {"id": token, **merged_meta}
                if doc_type == "SHEET" and token in self._sheet_struct_cache:
                    payload["_cached_tabs"] = self._sheet_struct_cache[token]
                elif doc_type == "BITABLE" and token in self._bitable_struct_cache:
                    payload["_cached_tables"] = self._bitable_struct_cache[token]
                if doc_type == "SHEET":
                    to_read_sheet.append(payload)
                elif doc_type == "BITABLE":
                    to_read_bitable.append(payload)

        if to_read_sheet:
            self._start_read_task(self._read_sheet_batch(to_read_sheet))
        if to_read_bitable:
            self._start_read_task(self._read_bitable_batch(to_read_bitable))
        logger.info(
            "TIMING table_pool.submit parents=%d selected=%d new_sheet=%d new_bitable=%d ms=%.1f",
            len(candidates),
            len(selected_ids),
            len(to_read_sheet),
            len(to_read_bitable),
            _elapsed_ms(started),
        )
        return selected_ids

    def has_result_or_pending(self, token: str) -> bool:
        return token in self._done or token in self._reading

    def _normalize_chunks_for_storage(self, token: str, chunks: List[UnifiedChunk]) -> List[UnifiedChunk]:
        meta = self._parent_meta.get(token, {})
        normalized: List[UnifiedChunk] = []
        for uc in chunks:
            merged_meta = dict(uc.metadata or {})
            merged_meta["reader_score"] = float(uc.score or 0.0)
            merged_meta["doc_id"] = token
            merged_meta["doc_token"] = token
            merged_meta["route"] = "sheet_api" if str(meta.get("doc_type", "")).upper() == "SHEET" else "bitable_api"
            merged_meta["selected_focus_ids"] = list(meta.get("selected_focus_ids") or [])
            merged_meta["selected_sub_query_ids"] = list(meta.get("selected_sub_query_ids") or [])
            merged_meta["selected_feishu_urls"] = list(meta.get("selected_feishu_urls") or [])
            merged_meta["selected_query_types"] = list(meta.get("selected_query_types") or [])
            merged_meta["cross_query_hit_count"] = int(meta.get("cross_query_hit_count", 1) or 1)
            merged_meta["best_api_rank"] = int(meta.get("best_api_rank", 10**9) or 10**9)
            merged_meta["first_seen_order"] = int(meta.get("first_seen_order", 10**9) or 10**9)
            merged_meta["candidate_source"] = meta.get("_source", "")
            merged_meta["candidate_sources"] = list(meta.get("_sources") or [])
            merged_meta["read_query"] = meta.get("read_query", "")
            merged_meta["table_selected_child_count"] = int(meta.get("table_selected_child_count", 0) or 0)
            merged_meta["table_structure_child_count"] = int(meta.get("table_structure_child_count", 0) or 0)
            merged_meta["table_read_ms"] = float(meta.get("table_read_ms", 0.0) or 0.0)
            merged_meta["table_read_chunks"] = int(meta.get("table_read_chunks", 0) or 0)
            merged_meta["table_submit_selected"] = bool(meta.get("table_submit_selected", False))
            merged_meta["table_submit_skipped"] = bool(meta.get("table_submit_skipped", False))
            normalized.append(
                UnifiedChunk(
                    text=uc.text,
                    source_name=uc.source_name,
                    source_url=uc.source_url,
                    source_tool=uc.source_tool,
                    heading_path=uc.heading_path,
                    score=0.0,
                    confidence_tier=uc.confidence_tier,
                    metadata=merged_meta,
                )
            )
        return normalized

    async def store_result(self, token: str, chunks: List[UnifiedChunk]) -> None:
        async with self._lock:
            self._cache[token] = self._normalize_chunks_for_storage(token, chunks)
            self._reading.discard(token)
            self._done.add(token)
            event = self._ready_events.get(token)
            if event:
                event.set()

    async def get_cached(self, token: str, timeout: float = 30.0) -> List[UnifiedChunk]:
        if token in self._done:
            return self._cache.get(token, [])
        event = self._ready_events.get(token)
        if event:
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(
                    "feishu.read_fail source=table_pool_wait token=%s kind=TimeoutError timeout_s=%.1f",
                    token[:16], timeout,
                )
                return []
        return self._cache.get(token, [])

    async def wait_and_collect(self, tokens: List[str], total_timeout: float = 20.0) -> List[UnifiedChunk]:
        started = time.perf_counter()
        pending = set(tokens)
        collected_by_token: Dict[str, List[UnifiedChunk]] = {}
        deadline = asyncio.get_event_loop().time() + total_timeout

        while pending and asyncio.get_event_loop().time() < deadline:
            newly_done = []
            for token in list(pending):
                event = self._ready_events.get(token)
                if event is None or event.is_set():
                    newly_done.append(token)
            for token in newly_done:
                pending.discard(token)
                collected_by_token[token] = list(self._cache.get(token, []))
            if not newly_done and pending:
                await asyncio.sleep(0.15)

        if pending:
            logger.warning(
                "TablePool.wait_and_collect: total_timeout %.1fs — %d/%d parents still pending",
                total_timeout,
                len(pending),
                len(tokens),
            )

        result: List[UnifiedChunk] = []
        for token in tokens:
            result.extend(collected_by_token.get(token, self._cache.get(token, [])))
        logger.info(
            "TIMING table_pool.wait_and_collect parents=%d chunks=%d pending=%d ms=%.1f",
            len(tokens),
            len(result),
            len(pending),
            _elapsed_ms(started),
        )
        return result

    async def _read_sheet_batch(self, candidates: List[dict]) -> None:
        from feishu_read_tool.sheet_reader import read_sheets

        async def _read_one(candidate: dict) -> None:
            token = self._token_of(candidate)
            row_started = time.perf_counter()
            async with self._sheet_read_sem:
                try:
                    chunks = await self._to_thread(
                        read_sheets,
                        candidate.get("read_query", "") or "",
                        [candidate],
                        self.uat,
                    )
                    read_ms = _elapsed_ms(row_started)
                    self._mark_parent_read_result(token, read_ms=read_ms, chunk_count=len(chunks))
                    await self.store_result(token, chunks)
                    logger.info(
                        "TIMING table_pool.read_sheet token=%s chunks=%d ms=%.1f",
                        token[:16],
                        len(chunks),
                        read_ms,
                    )
                except Exception as e:
                    logger.warning("table_pool: sheet read failed for %s: %s", token[:16], e)
                    self._mark_parent_read_result(token, read_ms=_elapsed_ms(row_started), chunk_count=0)
                    await self.store_result(token, [])

        await asyncio.gather(*[_read_one(candidate) for candidate in candidates], return_exceptions=True)

    async def _read_bitable_batch(self, candidates: List[dict]) -> None:
        from feishu_read_tool.bitable_reader import read_bitables

        async def _read_one(candidate: dict) -> None:
            token = self._token_of(candidate)
            row_started = time.perf_counter()
            async with self._bitable_read_sem:
                try:
                    chunks = await self._to_thread(
                        read_bitables,
                        candidate.get("read_query", "") or "",
                        [candidate],
                        self.uat,
                    )
                    read_ms = _elapsed_ms(row_started)
                    self._mark_parent_read_result(token, read_ms=read_ms, chunk_count=len(chunks))
                    await self.store_result(token, chunks)
                    logger.info(
                        "TIMING table_pool.read_bitable token=%s chunks=%d ms=%.1f",
                        token[:16],
                        len(chunks),
                        read_ms,
                    )
                except Exception as e:
                    logger.warning("table_pool: bitable read failed for %s: %s", token[:16], e)
                    self._mark_parent_read_result(token, read_ms=_elapsed_ms(row_started), chunk_count=0)
                    await self.store_result(token, [])

        await asyncio.gather(*[_read_one(candidate) for candidate in candidates], return_exceptions=True)

    def get_roi_rows(self, final_selected_tokens: Optional[Set[str]] = None) -> List[dict]:
        final_selected_tokens = set(final_selected_tokens or set())
        rows: List[dict] = []
        for token, meta in self._parent_meta.items():
            raw_structure_count = int(meta.get("table_structure_child_count", 0) or 0)
            structure_count = max(1, raw_structure_count)
            selected_count = int(meta.get("table_selected_child_count", 0) or 0)
            rows.append({
                "token": token,
                "title": meta.get("title", ""),
                "doc_type": meta.get("doc_type", ""),
                "cross_query_hit_count": int(meta.get("cross_query_hit_count", 1) or 1),
                "best_api_rank": int(meta.get("best_api_rank", 10**9) or 10**9),
                "first_seen_order": int(meta.get("first_seen_order", 10**9) or 10**9),
                "selected_child_count": selected_count,
                "structure_child_count": raw_structure_count,
                "selected_child_ratio": round(selected_count / structure_count, 4),
                "read_ms": float(meta.get("table_read_ms", 0.0) or 0.0),
                "read_chunks": int(meta.get("table_read_chunks", 0) or 0),
                "submit_selected": bool(meta.get("table_submit_selected", False)),
                "submit_skipped": bool(meta.get("table_submit_skipped", False)),
                "final_selected": token in final_selected_tokens,
                "selected_focus_ids": list(meta.get("selected_focus_ids") or []),
                "selected_sub_query_ids": list(meta.get("selected_sub_query_ids") or []),
                "selected_query_types": list(meta.get("selected_query_types") or []),
            })
        rows.sort(
            key=lambda row: (
                not row["submit_selected"],
                not row["final_selected"],
                -row["read_ms"],
                -row["cross_query_hit_count"],
                row["best_api_rank"],
                row["first_seen_order"],
                row["title"],
            )
        )
        return rows
