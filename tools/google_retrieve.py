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

from .models import UnifiedChunk

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
    "application/pdf",
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


def _sync_google_retrieve(
    query: str,
    max_results: int = 10,
    max_chunk_tokens: int = 800,
    max_read_time: int = 30,
) -> List[UnifiedChunk]:
    """同步版本的 google_retrieve（内部使用）。"""
    from google_workspace_tool.drive_search_tool import search_drive
    from google_workspace_tool.drive_content_reader import read_drive_chunks

    try:
        creds = _get_credentials()
    except FileNotFoundError as e:
        logger.warning("google_retrieve: credentials not available: %s", e)
        return []

    q = _build_search_q(query)
    logger.info("google_retrieve: query=%r, drive_q=%.80r", query, q)

    try:
        files = search_drive(
            q=q,
            page_size=min(max_results, 100),
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

    # 限制处理文件数量
    files = files[:max_results]
    logger.info("google_retrieve: found %d files, processing chunks...", len(files))

    all_chunks: List[UnifiedChunk] = []
    for f in files:
        file_id = f.get("id")
        mime_type = f.get("mimeType")
        if not file_id or not mime_type:
            continue
        if mime_type not in _SUPPORTED_MIMETYPES:
            continue

        try:
            gc_list = read_drive_chunks(
                file_id=file_id,
                mime_type=mime_type,
                creds=creds,
                max_chunk_tokens=max_chunk_tokens,
                max_read_time=max_read_time,
                supports_all_drives=True,
            )
        except Exception as e:
            logger.warning(
                "google_retrieve: failed to read chunks for file %s: %s", file_id, e
            )
            continue

        for gc in gc_list:
            all_chunks.append(_google_chunk_to_unified(gc, query))

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
    max_read_time: int = 30,
    **kwargs,
) -> List[UnifiedChunk]:
    """Google Workspace 检索入口。

    Args:
        query: 检索文本（会转义后用于 Drive fullText contains）
        max_results: 最多搜索并处理的文件数（默认 10）
        max_chunk_tokens: 每个 chunk 的最大 token 数
        max_read_time: 单个文件读取超时（秒）

    Returns:
        List[UnifiedChunk]
    """
    return await asyncio.to_thread(
        _sync_google_retrieve,
        query,
        max_results,
        max_chunk_tokens,
        max_read_time,
    )
