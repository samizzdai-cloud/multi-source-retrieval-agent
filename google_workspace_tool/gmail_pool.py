"""Gmail Thread 读取状态池 — 跨 sub_query 复用读取结果。

功能：
1. 缓存 Thread 元数据（从 Gmail search results 提取）
2. 标记读取状态（reading / done），支持并发等待
3. 合并跨 sq 的相同 thread 读取需求
4. 超时感知：wait_for_done 返回已完成 chunks
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from tools.core.models import UnifiedChunk

logger = logging.getLogger(__name__)


@dataclass
class GmailThreadMeta:
    """单个 Gmail Thread 的元数据。"""
    thread_id: str
    subject: str
    sender: str
    date: str
    snippet: str
    url: str


@dataclass
class GmailSelection:
    """某个 sub_query 对某个 Thread 的选择结果。"""
    thread_id: str
    sub_query_id: str
    focus_id: str


class GmailPool:
    """Gmail Thread 读取状态池。生命周期：跨 run() 持久化。"""

    def __init__(self):
        self._meta_cache: Dict[str, GmailThreadMeta] = {}
        self._chunk_cache: Dict[str, List[UnifiedChunk]] = {}
        self._done: Set[str] = set()
        self._reading: Set[str] = set()
        self._events: Dict[str, asyncio.Event] = {}

    # ── 元数据 ──────────────────────────────────────────────────────────

    def add_meta(self, meta: GmailThreadMeta) -> None:
        if meta.thread_id not in self._meta_cache:
            self._meta_cache[meta.thread_id] = meta

    def get_meta(self, thread_id: str) -> Optional[GmailThreadMeta]:
        return self._meta_cache.get(thread_id)

    # ── 状态管理 ────────────────────────────────────────────────────────

    def mark_reading(self, thread_id: str) -> bool:
        """标记为读取中。返回 True 表示首次标记。"""
        if thread_id in self._done or thread_id in self._reading:
            return False
        self._reading.add(thread_id)
        self._events[thread_id] = asyncio.Event()
        return True

    def mark_done(self, thread_id: str, chunks: List[UnifiedChunk]) -> None:
        self._reading.discard(thread_id)
        self._done.add(thread_id)
        self._chunk_cache[thread_id] = chunks
        evt = self._events.pop(thread_id, None)
        if evt:
            evt.set()

    async def wait_for_done(self, thread_id: str, timeout: float) -> List[UnifiedChunk]:
        if thread_id in self._chunk_cache:
            return self._chunk_cache[thread_id]
        evt = self._events.get(thread_id)
        if not evt:
            return []
        try:
            await asyncio.wait_for(evt.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        return self._chunk_cache.get(thread_id, [])

    def is_done(self, thread_id: str) -> bool:
        return thread_id in self._done

    def is_reading(self, thread_id: str) -> bool:
        return thread_id in self._reading

    # ── 合并 ────────────────────────────────────────────────────────────

    @staticmethod
    def merge_selections(selections: List[GmailSelection]) -> Dict[str, Set[str]]:
        """合并跨 sq 的 Thread 选择，返回 thread_id → set(sq_ids)。"""
        merged: Dict[str, Set[str]] = {}
        for sel in selections:
            merged.setdefault(sel.thread_id, set()).add(sel.sub_query_id)
        return merged
