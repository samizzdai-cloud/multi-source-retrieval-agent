"""Google Doc 文件池 — 跨 sub_query 复用读取结果。

功能：
1. 缓存 Doc 元数据（从 Drive search results 提取）
2. 标记读取状态（reading / done），支持并发等待
3. 合并跨 sq 的相同文件读取需求
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
class DocMeta:
    """单个 Doc 文件的元数据。"""
    file_id: str
    title: str
    url: str
    mime_type: str = ""
    description: str = ""
    drive_size: Optional[int] = None
    modified_time: Optional[str] = None
    owner: str = ""


@dataclass
class DocSelection:
    """某个 sub_query 对某个 Doc 的选择结果。"""
    file_id: str
    sub_query_id: str
    focus_id: str


class DocPool:
    """Google Doc 文件池。生命周期：跨 run() 持久化。"""

    def __init__(self):
        self._meta_cache: Dict[str, DocMeta] = {}
        self._chunk_cache: Dict[str, List[UnifiedChunk]] = {}
        self._done: Set[str] = set()
        self._reading: Set[str] = set()
        self._events: Dict[str, asyncio.Event] = {}

    # ── 元数据 ──────────────────────────────────────────────────────────

    def add_meta(self, meta: DocMeta) -> None:
        if meta.file_id not in self._meta_cache:
            self._meta_cache[meta.file_id] = meta

    def get_meta(self, file_id: str) -> Optional[DocMeta]:
        return self._meta_cache.get(file_id)

    # ── 状态管理 ────────────────────────────────────────────────────────

    def mark_reading(self, file_id: str) -> bool:
        """标记为读取中。返回 True 表示首次标记。"""
        if file_id in self._done or file_id in self._reading:
            return False
        self._reading.add(file_id)
        self._events[file_id] = asyncio.Event()
        return True

    def mark_done(self, file_id: str, chunks: List[UnifiedChunk]) -> None:
        self._reading.discard(file_id)
        self._done.add(file_id)
        self._chunk_cache[file_id] = chunks
        evt = self._events.pop(file_id, None)
        if evt:
            evt.set()

    async def wait_for_done(self, file_id: str, timeout: float) -> List[UnifiedChunk]:
        if file_id in self._chunk_cache:
            return self._chunk_cache[file_id]
        evt = self._events.get(file_id)
        if not evt:
            return []
        try:
            await asyncio.wait_for(evt.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        return self._chunk_cache.get(file_id, [])

    def is_done(self, file_id: str) -> bool:
        return file_id in self._done

    def is_reading(self, file_id: str) -> bool:
        return file_id in self._reading

    # ── 合并 ────────────────────────────────────────────────────────────

    @staticmethod
    def merge_selections(selections: List[DocSelection]) -> Dict[str, Set[str]]:
        """合并跨 sq 的 Doc 选择，返回 file_id → set(sq_ids)。"""
        merged: Dict[str, Set[str]] = {}
        for sel in selections:
            merged.setdefault(sel.file_id, set()).add(sel.sub_query_id)
        return merged
