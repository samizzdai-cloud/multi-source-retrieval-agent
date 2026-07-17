"""Google Spreadsheet 元数据缓存池 — 跨 sub_query 复用。

借鉴 Feishu table_pool 的跨 sub_query 合并读取模式：
1. 批量获取多个 spreadsheet 的元数据（含 merges + gridProperties）
2. 缓存到 _meta_cache，避免重复 API 调用
3. 按 Spreadsheet 元数据原始顺序读取 sheets
4. 合并跨 sq 的相同 spreadsheet 读取需求
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tools.core.models import UnifiedChunk

logger = logging.getLogger(__name__)


@dataclass
class SheetMeta:
    """单个 Spreadsheet 的元数据。"""
    file_id: str
    title: str
    url: str
    sheets: List[dict] = field(default_factory=list)
    drive_size: Optional[int] = None
    modified_time: Optional[str] = None
    # sheets 中每个 dict: {sheetId, title, index, gridProperties: {rowCount, columnCount}, merges}


@dataclass
class SheetSelection:
    """某个 sub_query 对某个 spreadsheet 的 sheet 选择结果。"""
    file_id: str
    selected_titles: List[str]
    sub_query_id: str
    focus_id: str


class SheetPool:
    """Google Spreadsheet 元数据缓存池。

    生命周期：在 GoogleSourceAgent.__init__ 中创建，跨多个 run() 调用持久化。
    """

    def __init__(self):
        self._meta_cache: Dict[str, SheetMeta] = {}
        self._chunk_cache: Dict[str, List[UnifiedChunk]] = {}
        self._done: Set[str] = set()
        self._reading: Set[str] = set()
        self._events: Dict[str, asyncio.Event] = {}
        self._event_loops: Dict[str, asyncio.AbstractEventLoop] = {}

    # ── 元数据获取 ──────────────────────────────────────────────────────

    def batch_fetch_metadata(
        self,
        file_ids: List[str],
        creds,
        native_api_timeout: int = 10,
        max_workers: int = 8,
        batch_timeout_s: Optional[float] = None,
    ) -> Dict[str, SheetMeta]:
        """并发获取多个 spreadsheet 的元数据（含 merges + gridProperties）。

        Args:
            file_ids: Spreadsheet ID 列表
            creds: 已授权的 Credentials 对象
            native_api_timeout: 每个请求 API 超时（秒）
            max_workers: 并发 worker 数
            batch_timeout_s: 整批元数据预取最长等待秒数；超时返回已完成结果

        Returns:
            file_id → SheetMeta 的字典（仅包含成功获取的）
        """
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
        from google_workspace_tool.readers.sheet_reader import _build_service

        # 注意：故意省略 ``merges``。Drive 对每个 spreadsheet 会返回**全部** merge 区间，
        # 大表（多 sheet × 多 merge）单次响应可达 MB 级，是 Phase B 的主要尾延迟源。
        # process_section_to_markdown 仅在 merges 存在时增强表头/合并格，缺省也能稳定输出。
        # 如未来需要 merge-aware 渲染，可在 sheet_reader.read_one 内补一次轻量
        # spreadsheets().get(fields="sheets(properties.sheetId,merges)") 的按需调用。
        fields = "properties.title,sheets(properties.sheetId,properties.title,properties.index,properties.gridProperties)"
        if batch_timeout_s is None:
            batch_timeout_s = max(float(native_api_timeout) + 2.0, 20.0)

        # 分离已缓存和未缓存的
        uncached_ids = [fid for fid in file_ids if fid not in self._meta_cache]
        results: Dict[str, SheetMeta] = {
            fid: self._meta_cache[fid] for fid in file_ids if fid in self._meta_cache
        }

        if not uncached_ids:
            return results

        def _fetch_one(fid: str) -> tuple:
            """获取单个 spreadsheet 的元数据。每个线程独立创建 service。"""
            try:
                service = _build_service(creds, native_api_timeout=native_api_timeout)
                meta = service.spreadsheets().get(
                    spreadsheetId=fid, fields=fields
                ).execute()
                props = meta.get("properties", {})
                title = props.get("title", "")
                sheets = meta.get("sheets", [])
                sheet_list = []
                for s in sheets:
                    p = s.get("properties", {})
                    sheet_list.append({
                        "sheetId": p.get("sheetId"),
                        "title": p.get("title", ""),
                        "index": p.get("index", 0),
                        "gridProperties": p.get("gridProperties", {}),
                        # merges 故意省略；fields 没拉，s.get("merges") 总是 []
                        "merges": [],
                    })
                sm = SheetMeta(
                    file_id=fid,
                    title=title,
                    url=f"https://docs.google.com/spreadsheets/d/{fid}/edit",
                    sheets=sheet_list,
                )
                return fid, sm, None
            except HttpError as e:
                logger.warning("[sheet_pool] metadata fetch failed for %s: %s", fid, e)
                return fid, None, e
            except (TimeoutError, OSError) as e:
                logger.warning("[sheet_pool] metadata fetch timeout/error for %s: %s", fid, e)
                return fid, None, e
            except Exception as e:
                logger.warning("[sheet_pool] metadata fetch unexpected error for %s: %s", fid, e)
                return fid, None, e

        executor = ThreadPoolExecutor(max_workers=max_workers)
        future_to_fid = {executor.submit(_fetch_one, fid): fid for fid in uncached_ids}
        try:
            for future in as_completed(future_to_fid, timeout=batch_timeout_s):
                fid = future_to_fid[future]
                try:
                    fid, sm, error = future.result()
                except Exception as e:
                    logger.warning("[sheet_pool] metadata future failed for %s: %s", fid, e)
                    continue
                if sm is not None:
                    self._meta_cache[fid] = sm
                    results[fid] = sm
        except FuturesTimeoutError:
            pending = [fid for fut, fid in future_to_fid.items() if not fut.done()]
            logger.warning(
                "[sheet_pool] metadata batch timeout after %.1fs: completed=%d pending=%d pending_ids=%s",
                batch_timeout_s, len(results), len(pending), [fid[:12] for fid in pending[:8]],
            )
            for fut in future_to_fid:
                if not fut.done():
                    fut.cancel()
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        return results

    # ── 查询 ────────────────────────────────────────────────────────────

    def get_meta(self, file_id: str) -> Optional[SheetMeta]:
        return self._meta_cache.get(file_id)

    def get_sheet_titles(self, file_id: str) -> List[str]:
        meta = self._meta_cache.get(file_id)
        if not meta:
            return []
        return [s.get("title", "") for s in meta.sheets]

    def get_sheet_by_title(self, file_id: str, title: str) -> Optional[dict]:
        meta = self._meta_cache.get(file_id)
        if not meta:
            return None
        for s in meta.sheets:
            if s.get("title") == title:
                return s
        return None

    def get_merges(self, file_id: str, title: str) -> List[dict]:
        s = self.get_sheet_by_title(file_id, title)
        return s.get("merges", []) if s else []

    def get_row_count(self, file_id: str, title: str) -> int:
        s = self.get_sheet_by_title(file_id, title)
        if not s:
            return 0
        return s.get("gridProperties", {}).get("rowCount", 0)

    def ensure_waitable(self, file_id: str) -> None:
        if file_id in self._done or file_id in self._events:
            return
        self._events[file_id] = asyncio.Event()
        self._event_loops[file_id] = asyncio.get_running_loop()

    # ── 状态管理 ────────────────────────────────────────────────────────

    def mark_reading(self, file_id: str) -> bool:
        """标记为读取中。返回 True 表示首次标记，False 表示已在读取/已读完。"""
        if file_id in self._done or file_id in self._reading:
            return False
        self._reading.add(file_id)
        if file_id not in self._events:
            self._events[file_id] = asyncio.Event()
            self._event_loops[file_id] = asyncio.get_running_loop()
        return True

    def mark_done(self, file_id: str, chunks: List[UnifiedChunk]) -> None:
        """读取完成（含超时/失败），唤醒所有等待的 sq。"""
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
        """等待 file 读取完成（含超时）。返回已完成的 chunks。"""
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

    # ── Native sheet order ──────────────────────────────────────────────

    def native_sheet_titles(self, file_id: str) -> List[str]:
        """Return every tab in the Sheets API order for a matched workbook."""
        meta = self._meta_cache.get(file_id)
        if not meta or not meta.sheets:
            return []
        return [s.get("title", "") for s in meta.sheets if s.get("title")]

    # ── 跨 sub_query 合并 ───────────────────────────────────────────────

    @staticmethod
    def merge_selections(
        selections: List[SheetSelection],
    ) -> Dict[str, List[str]]:
        """合并跨 sub_query 的相同 spreadsheet 的选中 sheets。

        Args:
            selections: 所有 sub_query 的 sheet 选择结果

        Returns:
            file_id → 合并后的 sheet titles 列表（去重）
        """
        merged: Dict[str, Set[str]] = {}
        for sel in selections:
            if sel.file_id not in merged:
                merged[sel.file_id] = set()
            merged[sel.file_id].update(sel.selected_titles)

        # 控制总量：单个 spreadsheet 最多 200 ranges（batchGet 限制）
        result: Dict[str, List[str]] = {}
        for fid, titles in merged.items():
            result[fid] = list(titles)[:200]
        return result
