"""Google Workspace 检索工具 — Drive 文件全文搜索 + 分块读取。

职责：
  1. 使用 Drive API `fullText contains` 搜索文件
  2. 按 MIME 类型过滤，仅处理支持 chunking 的文件类型
  3. 调用各 reader 的 `read_chunks()` 将文件切分为 GoogleChunk
  4. 将 GoogleChunk 映射为 UnifiedChunk 返回给 RetrievalAgent

支持的文件类型：
  - Google Docs (application/vnd.google-apps.document)
  - Google Sheets (application/vnd.google-apps.spreadsheet)
  - Google Slides (application/vnd.google-apps.presentation)
  - Word (.docx / .doc)
  - PDF
"""

import asyncio
import logging
import os
from typing import List, Optional

from tools.core.models import UnifiedChunk

logger = logging.getLogger(__name__)

# Google OAuth2 scopes (Drive read-only + Gmail read-only for future expansion)
_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]

# Supported MIME types for chunking
_SUPPORTED_MIMETYPES = {
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.spreadsheet",
    "application/vnd.google-apps.presentation",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "text/plain",
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/vnd.ms-excel.sheet.macroEnabled.12",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def _get_credentials():
    """Lazy import & fetch Google credentials."""
    from google_workspace_tool.auth_utils import get_credentials

    return get_credentials(scopes=_SCOPES)


def _build_search_q(query: str) -> str:
    """Build Drive search query with MIME type filter.

    Example:
        fullText contains 'budget' and (mimeType='application/vnd.google-apps.document' or ...)
    """
    mime_clauses = [f"mimeType='{mt}'" for mt in _SUPPORTED_MIMETYPES]
    mime_filter = " or ".join(mime_clauses)
    escaped = query.replace("'", "\\'")
    return f"fullText contains '{escaped}' and ({mime_filter})"


def _google_chunk_to_unified(gc, query: str = "") -> UnifiedChunk:
    """GoogleChunk → UnifiedChunk."""
    meta = gc.metadata or {}
    meta.update(
        {
            "file_id": gc.file_id,
            "chunk_id": gc.chunk_id,
            "content_type": gc.content_type,
            "has_table": gc.has_table,
            "token_count": gc.token_count,
        }
    )
    # 简单的相关性评分：query 在 chunk 中出现则给基础分
    score = 0.5
    if query and query.lower() in (gc.content or "").lower():
        score = 0.7

    return UnifiedChunk(
        text=gc.content or "",
        source_name=gc.file_title or "",
        source_url=gc.file_url or "",
        source_tool="google",
        heading_path=gc.heading_path or "",
        score=score,
        metadata=meta,
    )


def _select_sheet_ranges(
    file_id: str,
    file_name: str,
    query: str,
    creds,
) -> Optional[List[str]]:
    """使用 rerank 筛选出最相关的 sheets。"""
    try:
        from google_workspace_tool.readers.sheet_reader import get_spreadsheet_info
        from tools.core.rerank import select_search_candidates, build_focus_sub_query_question
    except ImportError:
        return None

    info = get_spreadsheet_info(file_id, creds)
    sheets = info.get("sheets", [])
    if not sheets:
        return None

    candidates = []
    for s in sheets:
        grid = s.get("gridProperties", {})
        summary = f"Rows: {grid.get('rowCount', 0)} | Cols: {grid.get('columnCount', 0)}"
        candidates.append({
            "id": str(s.get("sheetId")),
            "title": f"{file_name} / {s.get('title')}",
            "summary": summary,
            "_sheet_title": s.get("title")
        })

    if len(candidates) <= 2:
        return [c["_sheet_title"] for c in candidates]

    question = build_focus_sub_query_question(query, query)
    rerank_result = select_search_candidates(
        query=question,
        candidates=candidates,
        top_n=2,
        score_threshold=0.18,
        min_keep=1,
        instruct_template="qa"
    )

    if rerank_result.get("status") == "success":
        selected = list(rerank_result.get("selected_candidates") or [])
        return [c["_sheet_title"] for c in selected]
    else:
        return [c["_sheet_title"] for c in candidates[:2]]


def _select_drive_candidates(query: str, files: List[dict], top_n: int = 5) -> List[dict]:
    """使用 rerank 筛选出最相关的 Drive 文件候选。"""
    try:
        from tools.core.rerank import select_search_candidates, build_focus_sub_query_question
    except ImportError:
        return files[:top_n]

    candidates = []
    for f in files:
        candidates.append({
            "id": f.get("id"),
            "title": f.get("name", ""),
            "summary": f.get("description") or f.get("mimeType", ""),
            "_file": f
        })

    if len(candidates) <= top_n:
        return [c["_file"] for c in candidates]

    question = build_focus_sub_query_question(query, query)
    rerank_result = select_search_candidates(
        query=question,
        candidates=candidates,
        top_n=top_n,
        score_threshold=0.1,  # 较低阈值，因为仅靠标题判断
        min_keep=1,
        instruct_template="qa"
    )

    if rerank_result.get("status") == "success":
        selected = list(rerank_result.get("selected_candidates") or [])
        return [c["_file"] for c in selected]
    else:
        return [c["_file"] for c in candidates[:top_n]]


def _sync_google_retrieve(
    query: str,
    max_results: int = 20,
    max_chunk_tokens: int = 800,
    download_timeout: int = 12,
    native_api_timeout: int = 12,
    enable_sheet_filter: bool = False,
    enable_excel_filter: bool = False,
) -> List[UnifiedChunk]:
    """同步版本的 google_retrieve（内部使用）。"""
    from google_workspace_tool.drive_search_tool import search_drive
    from google_workspace_tool.drive_content_reader import read_drive_chunks
    from concurrent.futures import ThreadPoolExecutor, as_completed

    try:
        creds = _get_credentials()
    except FileNotFoundError as e:
        logger.warning("google_retrieve: credentials not available: %s", e)
        return []

    q = _build_search_q(query)
    logger.info("google_retrieve: query=%r, drive_q=%.80r", query, q)

    try:
        # 初次召回更多文件
        files = search_drive(
            q=q,
            page_size=min(max_results * 2, 100),
            max_pages=1,
            supports_all_drives=True,
            include_items_from_all_drives=True,
            creds=creds,
        )
    except Exception as e:
        logger.warning("google_retrieve: Drive search failed: %s", e)
        return []

    if not files:
        logger.info("google_retrieve: no files found for query=%r", query)
        return []

    # 过滤不支持的 MIME
    files = [f for f in files if f.get("mimeType") in _SUPPORTED_MIMETYPES and f.get("id")]
    
    # 文件候选重排：利用 Rerank 选出最相关的 Top N (e.g. 5) 避免全部下载
    files = _select_drive_candidates(query, files, top_n=5)
    logger.info("google_retrieve: selected %d files after candidate rerank...", len(files))

    def _process_file(f):
        file_id = f.get("id")
        mime_type = f.get("mimeType")

        range_names = None
        if mime_type == "application/vnd.google-apps.spreadsheet" and enable_sheet_filter:
            range_names = _select_sheet_ranges(file_id, f.get("name", ""), query, creds)
        elif mime_type in (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
            "application/vnd.ms-excel.sheet.macroEnabled.12",
            "text/csv"
        ) and enable_excel_filter:
            pass

        try:
            gc_list = read_drive_chunks(
                file_id=file_id,
                mime_type=mime_type,
                creds=creds,
                max_chunk_tokens=max_chunk_tokens,
                download_timeout=download_timeout,
                native_api_timeout=native_api_timeout,
                supports_all_drives=True,
                range_names=range_names,
                query=query,
            )
            return [_google_chunk_to_unified(gc, query) for gc in gc_list]
        except Exception as e:
            logger.warning("google_retrieve: failed to read chunks for file %s: %s", file_id, e)
            return []

    all_chunks: List[UnifiedChunk] = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_file = {executor.submit(_process_file, f): f for f in files}
        for future in as_completed(future_to_file):
            chunks = future.result()
            all_chunks.extend(chunks)

    logger.info(
        "google_retrieve: query=%r, files=%d, total_chunks=%d",
        query,
        len(files),
        len(all_chunks),
    )
    return all_chunks


async def google_retrieve(
    query: str,
    max_results: int = 10,
    max_chunk_tokens: int = 800,
    download_timeout: int = 12,
    native_api_timeout: int = 12,
    enable_gmail: bool = False,
    enable_gmail_attachments: bool = False,
    enable_sheet_filter: bool = False,
    enable_excel_filter: bool = False,
    **kwargs,
) -> List[UnifiedChunk]:
    """Google Workspace 检索入口。

    Args:
        query: 检索文本（会转义后用于 Drive fullText contains）
        max_results: 最多搜索并处理的文件数（默认 10）
        max_chunk_tokens: 每个 chunk 的最大 token 数
        download_timeout: 非原生文件下载流超时打断时间（秒）
        native_api_timeout: 原生文件 API 请求等待时间（秒）
        enable_gmail: 是否启用 Gmail 检索
        enable_gmail_attachments: 是否在 Gmail 中检索提取附件

    Returns:
        List[UnifiedChunk]
    """
    drive_chunks = await asyncio.to_thread(
        _sync_google_retrieve,
        query,
        max_results,
        max_chunk_tokens,
        download_timeout,
        native_api_timeout,
        enable_sheet_filter,
        enable_excel_filter,
    )

    gmail_unified_chunks = []
    if enable_gmail:
        try:
            creds = _get_credentials()
            from google_workspace_tool.gmail_rag_pipeline import run_gmail_chunk_pipeline
            gmail_gc_list = await asyncio.to_thread(
                run_gmail_chunk_pipeline,
                query=query,
                creds=creds,
                max_threads=max_results,
                enable_attachments=enable_gmail_attachments
            )
            for gc in gmail_gc_list:
                gmail_unified_chunks.append(_google_chunk_to_unified(gc, query))
        except Exception as e:
            logger.warning("google_retrieve: Gmail search failed: %s", e)

    # 简单合并结果，后续可调整排序策略
    return drive_chunks + gmail_unified_chunks
