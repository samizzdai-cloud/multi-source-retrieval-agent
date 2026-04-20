"""TablePool — 跨 sub_query 共享的表格读取池（轻量版）

核心职责：
  1. 跨 sub_query 去重：多个 sub_query 搜到同一 spreadsheet/bitable → 只读一次
  2. Budget 控制：每 focus 最多 3 sheet + 3 bitable
  3. BM25 排序：用 sub_query 对 candidates 排序，取 top-N
  4. 缓存：读取结果缓存，后续 sub_query 直接复用

生命周期：retrieval_agent 创建一个实例，传给所有 feishu_retrieve task。
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .models import UnifiedChunk

logger = logging.getLogger(__name__)

MAX_SHEETS_PER_FOCUS = 3
MAX_BITABLES_PER_FOCUS = 3


def _bm25_score(query: str, text: str, k1: float = 1.5, b: float = 0.75) -> float:
    """极简 BM25：query 词在 text 中的 tf-idf 近似。"""
    query_terms = set(re.findall(r'[\w\u4e00-\u9fff]{2,}', query.lower()))
    if not query_terms:
        return 0.0
    text_lower = text.lower()
    text_words = re.findall(r'[\w\u4e00-\u9fff]+', text_lower)
    doc_len = len(text_words)
    avg_dl = 20  # 表格标题平均长度短
    score = 0.0
    for term in query_terms:
        tf = text_lower.count(term)
        if tf > 0:
            norm_tf = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / avg_dl))
            score += norm_tf
    return score


class TablePool:
    """跨 sub_query 共享的表格读取池。

    与 FeishuDocPool 的区别：
    - 更简单：无渐进缓存、无 MCP/Block 路由
    - 只做去重 + budget + BM25 排序 + 结果缓存
    """

    def __init__(self, uat: str, max_sheets: int = MAX_SHEETS_PER_FOCUS,
                 max_bitables: int = MAX_BITABLES_PER_FOCUS):
        self.uat = uat
        self.max_sheets = max_sheets
        self.max_bitables = max_bitables

        # 缓存: token → List[UnifiedChunk]
        self._cache: Dict[str, List[UnifiedChunk]] = {}
        # 正在读取的 token
        self._reading: Set[str] = set()
        # 已完成的 token
        self._done: Set[str] = set()
        # 已选中的 sheet / bitable 计数
        self._sheet_count = 0
        self._bitable_count = 0
        # 完成事件
        self._ready_events: Dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    @property
    def stats(self) -> Dict:
        return {
            "cached": len(self._cache),
            "sheets_selected": self._sheet_count,
            "bitables_selected": self._bitable_count,
        }

    async def submit_tables(
        self,
        candidates: List[dict],
        query: str,
    ) -> List[dict]:
        """BM25 排序 + 去重 + budget 控制，返回需要读取的候选列表。

        已缓存的直接跳过（后续 get_cached 取结果）。
        """
        if not candidates:
            return []

        # BM25 排序
        scored = []
        for c in candidates:
            title = c.get("title", "")
            s = _bm25_score(query, title)
            scored.append((s, c))
        scored.sort(key=lambda x: x[0], reverse=True)

        # selected 只包含 *本次 submit 新登记到 _reading 的 candidate*。
        # 已 done / 他人在读的 token 不再进 selected，由调用方通过
        # get_cached() 等事件拿结果，避免被重复调度读取。
        selected: List[dict] = []
        async with self._lock:
            for _, c in scored:
                token = c.get("id", "")
                if not token:
                    continue
                doc_type = c.get("doc_type", "").upper()

                # 已完成或他人正在读 → 不登记、不返回
                if token in self._done or token in self._reading:
                    continue

                # Budget 检查
                if doc_type == "SHEET" and self._sheet_count >= self.max_sheets:
                    continue
                if doc_type == "BITABLE" and self._bitable_count >= self.max_bitables:
                    continue

                # 选中
                if doc_type == "SHEET":
                    self._sheet_count += 1
                elif doc_type == "BITABLE":
                    self._bitable_count += 1

                self._reading.add(token)
                self._ready_events[token] = asyncio.Event()
                selected.append(c)

        return selected

    def needs_read(self, token: str) -> bool:
        """检查某个 token 是否需要读取（未完成）。

        注意：返回 True 不代表"本 sub_query 应该去读"——还要检查
        token 是否在当前 sub_query 自己的 `selected` 集合里（即 submit_tables
        刚为本 sub_query 新登记的）。正在被 *他人* 读的 token 不在本次
        selected 中，因此不会被调度重读；后续用 has_result_or_pending +
        get_cached 等事件。
        """
        return token not in self._done

    def has_result_or_pending(self, token: str) -> bool:
        """token 是否已由 TablePool 管理（已完成或他人正在读）。

        True → 调用方可用 get_cached 直接取缓存或等待事件；
        False → token 未被 submit 过，调用方自己处理。
        """
        return token in self._done or token in self._reading

    async def store_result(self, token: str, chunks: List[UnifiedChunk]) -> None:
        """存储读取结果，通知等待者。"""
        async with self._lock:
            self._cache[token] = chunks
            self._reading.discard(token)
            self._done.add(token)
            event = self._ready_events.get(token)
            if event:
                event.set()

    async def get_cached(self, token: str, timeout: float = 30.0) -> List[UnifiedChunk]:
        """获取缓存的结果。如果正在读取，等待完成。"""
        if token in self._done:
            return self._cache.get(token, [])
        event = self._ready_events.get(token)
        if event:
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                # S2.1 结构化失败日志（用 read_fail 前缀方便统一 grep）
                logger.warning(
                    "feishu.read_fail source=table_pool_wait token=%s kind=TimeoutError timeout_s=%.1f",
                    token[:16], timeout,
                )
                return []
        return self._cache.get(token, [])
