"""Google Slide 文件池 — 跨 sub_query 复用读取结果。

功能与 doc_pool 一致，独立类以便未来扩展 Slide 特有逻辑（如 slide notes 处理）。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from tools.core.models import UnifiedChunk

logger = logging.getLogger(__name__)


@dataclass
class SlideMeta:
    """单个 Slide 文件的元数据。"""
    file_id: str
    title: str
    url: str
    mime_type: str = ""
    description: str = ""
    drive_size: Optional[int] = None
    modified_time: Optional[str] = None
    owner: str = ""


@dataclass
class SlideSelection:
    """某个 sub_query 对某个 Slide 的选择结果。"""
    file_id: str
    sub_query_id: str
    focus_id: str


class SlidePool:
    """Google Slide 文件池。生命周期：跨 run() 持久化。"""

    def __init__(self):
        self._meta_cache: Dict[str, SlideMeta] = {}
        self._chunk_cache: Dict[str, List[UnifiedChunk]] = {}
        self._done: Set[str] = set()
        self._reading: Set[str] = set()
        self._events: Dict[str, asyncio.Event] = {}
        self._event_loops: Dict[str, asyncio.AbstractEventLoop] = {}

    # ── 元数据 ──────────────────────────────────────────────────────────

    def add_meta(self, meta: SlideMeta) -> None:
        if meta.file_id not in self._meta_cache:
            self._meta_cache[meta.file_id] = meta

    def get_meta(self, file_id: str) -> Optional[SlideMeta]:
        return self._meta_cache.get(file_id)

    def ensure_waitable(self, file_id: str) -> None:
        if file_id in self._done or file_id in self._events:
            return
        self._events[file_id] = asyncio.Event()
        self._event_loops[file_id] = asyncio.get_running_loop()

    # ── 状态管理 ────────────────────────────────────────────────────────

    def mark_reading(self, file_id: str) -> bool:
        if file_id in self._done or file_id in self._reading:
            return False
        self._reading.add(file_id)
        if file_id not in self._events:
            self._events[file_id] = asyncio.Event()
            self._event_loops[file_id] = asyncio.get_running_loop()
        return True

    def mark_done(self, file_id: str, chunks: List[UnifiedChunk]) -> None:
        self._reading.discard(file_id)
        self._done.add(file_id)
        self._chunk_cache[file_id] = chunks
        evt = self._events.get(file_id)
        loop = self._event_loops.get(file_id)
        if evt:
            if loop and loop.is_running():
                loop.call_soon_threadsafe(evt.set)
            else:
                evt.set()

    async def wait_for_done(self, file_id: str, timeout: float) -> List[UnifiedChunk]:
        if file_id in self._chunk_cache:
            return self._chunk_cache[file_id]
        self.ensure_waitable(file_id)
        evt = self._events.get(file_id)
        try:
            await asyncio.wait_for(evt.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        if file_id in self._done:
            self._events.pop(file_id, None)
            self._event_loops.pop(file_id, None)
        return self._chunk_cache.get(file_id, [])

    def is_done(self, file_id: str) -> bool:
        return file_id in self._done

    def is_reading(self, file_id: str) -> bool:
        return file_id in self._reading

    # ── 合并 ────────────────────────────────────────────────────────────

    @staticmethod
    def merge_selections(selections: List[SlideSelection]) -> Dict[str, Set[str]]:
        merged: Dict[str, Set[str]] = {}
        for sel in selections:
            merged.setdefault(sel.file_id, set()).add(sel.sub_query_id)
        return merged
