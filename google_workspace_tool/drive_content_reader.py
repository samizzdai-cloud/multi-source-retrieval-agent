"""Google Drive 文件内容统一读取路由层。

本模块作为统一入口，负责：
1. 输入校验（url / file_id 二选一，mime_type 必填）
2. 从 URL 提取 file_id
3. 获取文件元数据（size 用于阈值判断）
4. Size Gate：超过阈值则跳过
5. 根据 mime_type 分发到对应的 type-specific reader
6. 将元数据合并到返回结果中

Type-specific readers 位于 readers/ 目录下：
- readers/doc_reader.py      — Google Docs
- readers/word_reader.py     — Word (.docx)
- readers/slide_reader.py    — Google Slides
- readers/pdf_reader.py      — PDF
- readers/sheet_reader.py    — Google Sheets (stub)

每个 reader 提供统一的 3 方法接口：
- read_one(file_id, creds, ...) -> dict
- read_batch(file_ids, creds, ...) -> list[dict]
- read_concurrent(file_ids, creds, max_workers, ...) -> list[dict]
"""

import logging
import threading
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from google_workspace_tool.utils.url_parser import resolve_file_id

logger = logging.getLogger(__name__)
from google_workspace_tool.utils.metadata_utils import get_file_metadata
from google_workspace_tool.readers import doc_reader, word_reader, slide_reader, sheet_reader, text_reader, excel_reader, pptx_reader

# MIME 类型常量
_MIME_DOCS = "application/vnd.google-apps.document"
_MIME_SHEETS = "application/vnd.google-apps.spreadsheet"
_MIME_SLIDES = "application/vnd.google-apps.presentation"
_MIME_WORD = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_MIME_WORD_OLD = "application/msword"
_MIME_TXT = "text/plain"
_MIME_CSV = "text/csv"
_MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MIME_XLS = "application/vnd.ms-excel"
_MIME_XLSM = "application/vnd.ms-excel.sheet.macroEnabled.12"
_MIME_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

# MIME → reader module 映射
_READER_MAP = {
    _MIME_DOCS: doc_reader,
    _MIME_SHEETS: sheet_reader,
    _MIME_SLIDES: slide_reader,
    _MIME_WORD: word_reader,
    _MIME_WORD_OLD: word_reader,
    _MIME_TXT: text_reader,
    _MIME_CSV: text_reader,
    _MIME_XLSX: excel_reader,
    _MIME_XLS: excel_reader,
    _MIME_XLSM: excel_reader,
    _MIME_PPTX: pptx_reader,
}

# 默认阈值（按文件类型可覆盖）
# 原生 Docs: 56MB Drive size -> ~30s API fetch. 建议 10MB outline, 15MB skip.
# Word: 48MB -> ~13s (download + parse). 下载比 API 快，阈值可放宽.
DEFAULT_AUTO_OUTLINE_THRESHOLD = 10 * 1024 * 1024   # 10 MB (Docs only)
DEFAULT_MAX_DRIVE_SIZE = 15 * 1024 * 1024           # 15 MB (global default)

# Sheets 读取时间预估算参数
# 实测: 7sheets→3.93s, 10sheets→4.75s, 15sheets→6.14s ≈ 0.5s/sheet
_SHEET_ESTIMATE_SECONDS_PER_SHEET = 0.5
_MAX_SHEET_COUNT_FOR_ESTIMATE = 20     # 20 * 0.5 + 0.5 = 10.5s > 7s 兜底
_DEFAULT_MAX_READ_TIME = 7             # 默认 7 秒读取超时

# 按文件类型的默认 max_drive_size 覆盖（None 表示使用全局默认值）
DEFAULT_MAX_DRIVE_SIZE_BY_TYPE = {
    _MIME_DOCS: 15 * 1024 * 1024,      # 15 MB — API fetch 慢
    _MIME_WORD: 50 * 1024 * 1024,      # 50 MB — 下载+解析很快
    _MIME_WORD_OLD: 50 * 1024 * 1024,
    _MIME_SLIDES: 15 * 1024 * 1024,    # 15 MB — Slides API
    _MIME_SHEETS: 15 * 1024 * 1024,    # 15 MB — Sheets API
    _MIME_XLSX: 50 * 1024 * 1024,
    _MIME_XLS: 50 * 1024 * 1024,
    _MIME_XLSM: 50 * 1024 * 1024,
    _MIME_PPTX: 50 * 1024 * 1024,
    _MIME_TXT: 50 * 1024 * 1024,
    _MIME_CSV: 50 * 1024 * 1024,
}


def _error_result(file_id: str, error_msg: str, drive_size=None) -> dict:
    """构造错误返回结构。"""
    return {
        "file_id": file_id,
        "mime_type": None,
        "title": "",
        "content": None,
        "structured_md": None,
        "content_type": None,
        "raw_bytes": None,
        "error": error_msg,
        "mode": None,
        "drive_size": drive_size,
        "skip_reason": None,
    }


def _timeout_result(file_id: str, mime_type: str, title: str, drive_size, timeout_seconds: int) -> dict:
    """构造超时返回结构。"""
    return {
        "file_id": file_id,
        "mime_type": mime_type,
        "title": title or "",
        "content": None,
        "structured_md": None,
        "content_type": None,
        "raw_bytes": None,
        "error": f"读取超时（{timeout_seconds}s），文件可能过大",
        "mode": "timeout",
        "drive_size": drive_size,
        "skip_reason": f"timeout_exceeded_{timeout_seconds}s",
    }


def _estimate_sheet_read_time(file_id: str, creds) -> float:
    """快速估算 Sheets 读取时间（秒）。
    
    通过 Sheets API 元数据获取 sheet_count，基于经验公式估算。
    成本约 0.5-1s。
    """
    try:
        service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        meta = service.spreadsheets().get(
            spreadsheetId=file_id,
            fields="sheets.properties.sheetId"
        ).execute()
        sheet_count = len(meta.get("sheets", []))
        return 0.5 + sheet_count * _SHEET_ESTIMATE_SECONDS_PER_SHEET
    except Exception:
        # 如果元数据获取失败，返回保守估计（确保会被兜底超时捕获）
        return 0.0


def _run_with_timeout(func, timeout_seconds: int, *args, **kwargs):
    """在 timeout_seconds 内执行 func，超时返回 timeout 结果。
    
    使用 threading.Thread 实现跨平台超时控制。
    注意：超时的线程不会真正停止（Python 无法安全中断线程），
    但 API 调用是 I/O 操作，完成后会自动退出，不会泄漏资源。
    """
    result = [None]
    exc = [None]
    
    def target():
        try:
            result[0] = func(*args, **kwargs)
        except Exception as e:
            exc[0] = e
    
    thread = threading.Thread(target=target)
    thread.daemon = True
    thread.start()
    thread.join(timeout=timeout_seconds)
    
    if thread.is_alive():
        return None  # 超时标记
    
    if exc[0]:
        raise exc[0]
    
    return result[0]


def read_drive_content(
    *,
    mime_type: str,
    url: Optional[str] = None,
    file_id: Optional[str] = None,
    creds=None,
    max_tabs: Optional[int] = None,
    auto_outline_threshold: Optional[int] = DEFAULT_AUTO_OUTLINE_THRESHOLD,
    max_drive_size: Optional[int] = None,
    download_timeout: int = 12,
    native_api_timeout: int = 12,
    supports_all_drives: bool = True,
    include_metadata: bool = True,
) -> dict:
    """读取单个 Drive 文件内容。

    Args:
        url: Google Drive URL（与 file_id 二选一）
        file_id: Drive 文件 ID（与 url 二选一）
        mime_type: 文件 MIME 类型（必填）
        creds: 已授权的 Credentials 对象（必填）
        max_tabs: Docs 专用，最多保留 N 个 tabs
        auto_outline_threshold: Docs 专用，超过此大小自动降级为 outline 模式（字节）
        max_drive_size: 最大 Drive 文件大小限制，超过则跳过（字节）
        max_read_time: 最大读取时间（秒），超过则返回超时。默认 7 秒。
        supports_all_drives: 是否支持共享驱动器
        include_metadata: 是否在结果中包含文件元数据

    Returns:
        统一格式的 dict
    """
    if creds is None:
        raise ValueError("creds 为必填参数。请在外部完成 OAuth2 授权后传入 Credentials 对象。")

    # 校验并解析 file_id
    try:
        resolved_id = resolve_file_id(url=url, file_id=file_id)
    except ValueError as e:
        raise ValueError(str(e))

    # 获取元数据（size 用于阈值判断）
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
    metadata = None
    if include_metadata or max_drive_size is not None or auto_outline_threshold is not None:
        metadata = get_file_metadata(
            file_id=resolved_id,
            drive_service=drive_service,
            supports_all_drives=supports_all_drives,
        )

    drive_size = metadata.get("size") if metadata else None
    title = metadata.get("name", "") if metadata else ""

    # --- Size Gate（按文件类型应用阈值）---
    # 优先使用调用方传入的 max_drive_size，未传则按 mime_type 取默认值
    effective_max_size = max_drive_size
    if effective_max_size is None:
        effective_max_size = DEFAULT_MAX_DRIVE_SIZE_BY_TYPE.get(mime_type, DEFAULT_MAX_DRIVE_SIZE)

    # Drive size gate：仅对非原生文件（Word/PDF）生效。
    # 对原生文件（Docs/Sheets/Slides），Drive size 不可靠，跳过此 gate。
    if mime_type not in (_MIME_DOCS, _MIME_SHEETS, _MIME_SLIDES):
        if effective_max_size is not None and drive_size is not None and drive_size > effective_max_size:
            result = _error_result(
                resolved_id,
                f"文件过大 ({drive_size / 1024 / 1024:.1f} MB > {effective_max_size / 1024 / 1024:.0f} MB)，跳过获取",
                drive_size=drive_size,
            )
            result["mode"] = "skipped"
            result["skip_reason"] = f"drive_size={drive_size} exceeds max_drive_size={effective_max_size}"
            result["mime_type"] = mime_type
            result["title"] = title
            if metadata:
                result["metadata"] = metadata
            return result

    # --- Sheets 预估算（Layer 1: 快速跳过明显过大的文件）---
    if mime_type == _MIME_SHEETS and native_api_timeout:
        estimated_seconds = _estimate_sheet_read_time(resolved_id, creds)
        estimated_sheet_count = max(0, int((estimated_seconds - 0.5) / _SHEET_ESTIMATE_SECONDS_PER_SHEET))
        if estimated_sheet_count > _MAX_SHEET_COUNT_FOR_ESTIMATE:
            result = _error_result(
                resolved_id,
                f"预估读取时间过长（约 {estimated_seconds:.1f}s > {native_api_timeout}s，{estimated_sheet_count} 个 sheet），跳过获取",
                drive_size=drive_size,
            )
            result["mode"] = "skipped"
            result["skip_reason"] = f"estimated_time={estimated_seconds:.1f}s exceeds max_read_time={native_api_timeout}s"
            result["mime_type"] = mime_type
            result["title"] = title
            if metadata:
                result["metadata"] = metadata
            return result

    # 根据 mime_type 路由到对应 reader
    reader = _READER_MAP.get(mime_type)
    if reader is None:
        result = _error_result(resolved_id, f"不支持的 MIME 类型: {mime_type}", drive_size=drive_size)
        result["mime_type"] = mime_type
        result["title"] = title
        if metadata:
            result["metadata"] = metadata
        return result

    # --- Layer 2: API 调用超时兜底 ---
    # 对保护线程依然保留最大超时（防止极少数底层死锁），取两者的最大值作为线程最大寿命
    max_timeout = max(download_timeout, native_api_timeout)

    def _do_read():
        if mime_type == _MIME_DOCS:
            return reader.read_one(
                file_id=resolved_id,
                creds=creds,
                max_tabs=max_tabs,
                auto_outline_threshold=auto_outline_threshold,
                drive_size=drive_size,
                supports_all_drives=supports_all_drives,
                native_api_timeout=native_api_timeout,
            )
        elif mime_type in (_MIME_WORD, _MIME_WORD_OLD, _MIME_TXT, _MIME_CSV, _MIME_XLSX, _MIME_XLS, _MIME_XLSM, _MIME_PPTX):
            return reader.read_one(
                file_id=resolved_id,
                creds=creds,
                supports_all_drives=supports_all_drives,
                download_timeout=download_timeout,
            )
        elif mime_type == _MIME_SLIDES:
            return reader.read_one(
                file_id=resolved_id,
                creds=creds,
                supports_all_drives=supports_all_drives,
                native_api_timeout=native_api_timeout,
            )
        elif mime_type == _MIME_SHEETS:
            return reader.read_one(
                file_id=resolved_id, 
                creds=creds, 
                drive_size=drive_size,
                native_api_timeout=native_api_timeout,
            )
        else:
            return _error_result(resolved_id, f"不支持的 MIME 类型: {mime_type}", drive_size=drive_size)

    try:
        result = _run_with_timeout(_do_read, max_timeout)
        if result is None:
            # 超时
            result = _timeout_result(
                resolved_id, mime_type, title, drive_size, max_timeout
            )
    except Exception as e:
        result = _error_result(resolved_id, f"读取失败: {e}", drive_size=drive_size)
        result["mime_type"] = mime_type

    # 合并元数据
    result["title"] = result.get("title") or title
    result["drive_size"] = drive_size
    if metadata:
        result["metadata"] = metadata

    return result


def read_drive_contents_batch(
    items: list[dict],
    creds=None,
    max_read_time: Optional[int] = None,
    supports_all_drives: bool = True,
) -> list[dict]:
    """串行批量读取多个 Drive 文件。

    按 mime_type 分组后，对每个组串行调用对应 reader 的 read_batch。
    适用于需要控制 API 速率的场景。

    Args:
        items: 列表，每项为 dict，必须包含 mime_type，以及 url 或 file_id：
            [{"file_id": "...", "mime_type": "..."}, ...]
            或 [{"url": "...", "mime_type": "..."}, ...]
        creds: 已授权的 Credentials
        supports_all_drives: 是否支持共享驱动器

    Returns:
        与 items 顺序对应的 result dict 列表
    """
    if creds is None:
        raise ValueError("creds 为必填参数。")

    # 按 mime_type 分组
    groups: dict[str, list[tuple[int, str]]] = {}
    for i, item in enumerate(items):
        mime = item.get("mime_type")
        if not mime:
            continue
        url = item.get("url")
        fid = item.get("file_id")
        try:
            resolved = resolve_file_id(url=url, file_id=fid)
        except ValueError:
            continue
        groups.setdefault(mime, []).append((i, resolved))

    # 初始化结果列表
    results = [None] * len(items)

    # 按组处理
    for mime, entries in groups.items():
        reader = _READER_MAP.get(mime)
        if reader is None:
            for idx, _ in entries:
                results[idx] = _error_result("", f"不支持的 MIME 类型: {mime}")
            continue

        file_ids = [fid for _, fid in entries]
        try:
            if mime == _MIME_DOCS:
                batch_results = reader.read_batch(file_ids=file_ids, creds=creds)
            elif mime in (_MIME_WORD, _MIME_WORD_OLD, _MIME_TXT, _MIME_CSV, _MIME_XLSX, _MIME_XLS, _MIME_XLSM, _MIME_PPTX):
                batch_results = reader.read_batch(file_ids=file_ids, creds=creds, supports_all_drives=supports_all_drives)
            elif mime == _MIME_SLIDES:
                batch_results = reader.read_batch(file_ids=file_ids, creds=creds, supports_all_drives=supports_all_drives)
            else:
                batch_results = reader.read_batch(file_ids=file_ids, creds=creds)

            for (idx, fid), res in zip(entries, batch_results):
                results[idx] = res
        except Exception as e:
            for idx, fid in entries:
                results[idx] = _error_result(fid, f"批量读取失败: {e}")

    # 填充缺失的结果
    for i, r in enumerate(results):
        if r is None:
            results[i] = _error_result("", "缺少 file_id 或 url，或无法解析 file_id")

    return results


def read_drive_contents_concurrent(
    items: list[dict],
    creds=None,
    max_workers: int = 5,
    max_drive_size: Optional[int] = None,
    auto_outline_threshold: Optional[int] = None,
    download_timeout: int = 12,
    native_api_timeout: int = 12,
    supports_all_drives: bool = True,
) -> list[dict]:
    """并发批量读取多个 Drive 文件。

    使用 ThreadPoolExecutor 并发调用各个 reader。
    不同 mime_type 的文件会并行处理（Doc API 和 Drive download 同时进行）。

    Args:
        items: 列表，每项为 dict，必须包含 mime_type，以及 url 或 file_id
        creds: 已授权的 Credentials
        max_workers: 最大并发线程数
        max_drive_size: 最大文件大小限制，超过则跳过。
                         默认 None — 按文件类型自动选择阈值：
                           Docs/Slides/Sheets: 15MB
                           Word: 50MB
                           PDF: 20MB
                         传入具体值则覆盖按类型的默认值。
        auto_outline_threshold: Docs 专用，超过此大小自动降级为 outline。
        supports_all_drives: 是否支持共享驱动器

    Returns:
        与 items 顺序对应的 result dict 列表
    """
    if creds is None:
        raise ValueError("creds 为必填参数。")

    def _read_item(item: dict) -> dict:
        try:
            return read_drive_content(
                mime_type=item["mime_type"],
                url=item.get("url"),
                file_id=item.get("file_id"),
                creds=creds,
                max_drive_size=max_drive_size,
                auto_outline_threshold=auto_outline_threshold,
                download_timeout=download_timeout,
                native_api_timeout=native_api_timeout,
                supports_all_drives=supports_all_drives,
            )
        except Exception as e:
            return _error_result(
                item.get("file_id", ""),
                f"并发读取失败: {e}"
            )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(_read_item, items))

    return results


def read_drive_chunks(
    *,
    mime_type: str,
    url: Optional[str] = None,
    file_id: Optional[str] = None,
    creds=None,
    max_tabs: Optional[int] = None,
    auto_outline_threshold: Optional[int] = DEFAULT_AUTO_OUTLINE_THRESHOLD,
    max_drive_size: Optional[int] = None,
    download_timeout: int = 12,
    native_api_timeout: int = 12,
    supports_all_drives: bool = True,
    max_chunk_tokens: int = 800,
    range_names: Optional[list[str]] = None,
    query: Optional[str] = None,
    enable_excel_filter: bool = False,
    known_title: Optional[str] = None,
) -> list:
    """读取单个 Drive 文件并切分为 chunks。

    Args:
        mime_type: 文件 MIME 类型（必填）
        url: Google Drive URL（与 file_id 二选一）
        file_id: Drive 文件 ID（与 url 二选一）
        creds: 已授权的 Credentials 对象（必填）
        max_tabs: Docs 专用，最多保留 N 个 tabs
        auto_outline_threshold: Docs 专用，超过此大小自动降级为 outline 模式（字节）
        max_drive_size: 最大 Drive 文件大小限制，超过则跳过（字节）
        max_read_time: 最大读取时间（秒），超过则返回空列表
        supports_all_drives: 是否支持共享驱动器
        max_chunk_tokens: 每个 chunk 的最大 token 数

    Returns:
        List[GoogleChunk]
    """
    from google_workspace_tool.chunkers import GoogleChunk

    if creds is None:
        raise ValueError("creds 为必填参数。请在外部完成 OAuth2 授权后传入 Credentials 对象。")

    # 校验并解析 file_id
    try:
        resolved_id = resolve_file_id(url=url, file_id=file_id)
    except ValueError as e:
        raise ValueError(str(e))

    # 获取元数据（size 用于阈值判断）
    import time as _time
    _meta_start = _time.perf_counter()
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
    metadata = get_file_metadata(
        file_id=resolved_id,
        drive_service=drive_service,
        supports_all_drives=supports_all_drives,
    )
    _meta_ms = (_time.perf_counter() - _meta_start) * 1000
    drive_size = metadata.get("size") if metadata else None
    title = metadata.get("name", "") if metadata else ""
    logger.info("[TIMING] drive.metadata file_id=%s... size=%s title=%.30r ms=%.1f",
                resolved_id[:20], drive_size, title, _meta_ms)

    # Size Gate
    effective_max_size = max_drive_size
    if effective_max_size is None:
        effective_max_size = DEFAULT_MAX_DRIVE_SIZE_BY_TYPE.get(mime_type, DEFAULT_MAX_DRIVE_SIZE)

    if mime_type not in (_MIME_DOCS, _MIME_SHEETS, _MIME_SLIDES):
        if effective_max_size is not None and drive_size is not None and drive_size > effective_max_size:
            return []

    # Sheets pre-estimate
    if mime_type == _MIME_SHEETS and native_api_timeout:
        estimated_seconds = _estimate_sheet_read_time(resolved_id, creds)
        estimated_sheet_count = max(0, int((estimated_seconds - 0.5) / _SHEET_ESTIMATE_SECONDS_PER_SHEET))
        if estimated_sheet_count > _MAX_SHEET_COUNT_FOR_ESTIMATE:
            return []

    reader = _READER_MAP.get(mime_type)
    if reader is None:
        return []

    max_timeout = max(download_timeout, native_api_timeout) + 2

    # Route to reader.read_chunks()
    def _do_read_chunks():
        if mime_type == _MIME_DOCS:
            return reader.read_chunks(
                file_id=resolved_id,
                creds=creds,
                max_tabs=max_tabs,
                auto_outline_threshold=auto_outline_threshold,
                supports_all_drives=supports_all_drives,
                max_chunk_tokens=max_chunk_tokens,
            )
        elif mime_type in (_MIME_WORD, _MIME_WORD_OLD, _MIME_TXT, _MIME_CSV, _MIME_XLSX, _MIME_XLS, _MIME_XLSM, _MIME_PPTX):
            kwargs = {}
            if mime_type in (_MIME_CSV, _MIME_XLSX, _MIME_XLS, _MIME_XLSM):
                kwargs["range_names"] = range_names
                kwargs["query"] = query
                kwargs["enable_excel_filter"] = enable_excel_filter
            return reader.read_chunks(
                file_id=resolved_id,
                creds=creds,
                supports_all_drives=supports_all_drives,
                max_chunk_tokens=max_chunk_tokens,
                download_timeout=download_timeout,
                **kwargs
            )
        elif mime_type == _MIME_SLIDES:
            return reader.read_chunks(
                file_id=resolved_id,
                creds=creds,
                supports_all_drives=supports_all_drives,
                native_api_timeout=native_api_timeout,
            )
        elif mime_type == _MIME_SHEETS:
            return reader.read_chunks(
                file_id=resolved_id,
                creds=creds,
                range_names=range_names,
                query=query,
                max_chunk_tokens=max_chunk_tokens,
                native_api_timeout=native_api_timeout,
                known_title=known_title,
            )
        return []

    try:
        _reader_start = _time.perf_counter()
        result = _run_with_timeout(_do_read_chunks, max_timeout)
        _reader_ms = (_time.perf_counter() - _reader_start) * 1000
        chunk_count = len(result) if result else 0
        logger.info("[TIMING] drive.reader file_id=%s... mime=%s chunks=%d ms=%.1f",
                    resolved_id[:20], mime_type, chunk_count, _reader_ms)
        if result is None:
            return []
        return result if result else []
    except Exception as e:
        _reader_ms = (_time.perf_counter() - _reader_start) * 1000
        logger.info("[TIMING] drive.reader file_id=%s... mime=%s FAILED ms=%.1f error=%s",
                    resolved_id[:20], mime_type, _reader_ms, e)
        logger.warning("[drive_content_reader] read_chunks failed for %s (%s): %s", file_id, mime_type, e)
        return []


def read_chunks_from_bytes(
    raw_bytes: bytes,
    mime_type: str,
    title: str,
    file_url: str = "",
    source_tag: str = "google",
    max_chunk_tokens: int = 800,
) -> list:
    """从原始 bytes 读取并切分为 chunks（用于 Gmail 附件等无 Drive file_id 的场景）。

    复用各 reader 的文本提取逻辑 + markdown_to_chunks 切分，保持与 Drive 文件一致的 chunk 格式。

    Args:
        raw_bytes: 文件原始 bytes
        mime_type: 文件 MIME 类型
        title: 文件标题（用于 chunk 的 file_title）
        file_url: 文件 URL（可选）
        source_tag: chunk_id 前缀
        max_chunk_tokens: 每个 chunk 最大 token 数

    Returns:
        List[GoogleChunk]
    """
    from google_workspace_tool.chunkers.md_chunker import markdown_to_chunks

    if not raw_bytes:
        return []

    text = ""
    if mime_type in (_MIME_TXT, _MIME_CSV):
        text = raw_bytes.decode("utf-8", errors="replace")
    elif mime_type == "text/html":
        from google_workspace_tool.gmail_rag_pipeline import MessageParserTool
        text = raw_bytes.decode("utf-8", errors="replace")
        text = MessageParserTool._clean_html(text)
    elif mime_type in (_MIME_WORD, _MIME_WORD_OLD):
        from google_workspace_tool.readers.word_reader import _try_extract_word_text
        text = _try_extract_word_text(raw_bytes) or ""
    elif mime_type in (_MIME_XLSX, _MIME_XLS, _MIME_XLSM):
        from google_workspace_tool.readers.excel_reader import _try_extract_excel_text
        text = _try_extract_excel_text(raw_bytes) or ""
    elif mime_type == _MIME_PPTX:
        from google_workspace_tool.readers.pptx_reader import _try_extract_pptx_text
        text = _try_extract_pptx_text(raw_bytes) or ""
    else:
        return []

    if not text or not text.strip():
        return []

    return markdown_to_chunks(
        markdown=text,
        file_id=title,
        file_title=title,
        file_url=file_url,
        source_tag=source_tag,
        max_chunk_tokens=max_chunk_tokens,
    )
