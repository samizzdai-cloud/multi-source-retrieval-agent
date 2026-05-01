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
import re
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


def _escape_drive_q_term(term: str) -> str:
    """转义 Drive search q 语法中的非法字符。

    Drive API `fullText contains` 对以下字符敏感，会导致 400 Bad Request:
    - 反斜杠 \\ 本身
    - 未闭合的引号 / 尖括号 / 管道符
    - 控制字符
    策略：先移除危险字符（保留单引号），再移除原始反斜杠，最后转义单引号。
    """
    # 先移除尖括号、管道符、控制字符
    term = re.sub(r'[<>|\x00-\x1f]', '', term)
    # 再移除原始反斜杠（必须在转义单引号之前，否则会把转义用的反斜杠也清掉）
    term = term.replace('\\', '')
    # 最后转义单引号
    term = term.replace("'", "\\'")
    return term.strip()


def _build_search_q(
    query: str, 
    keyword_groups: Optional[List[List[str]]] = None, 
    enable_fulltext: bool = True,
    time_filter: Optional[str] = None,
    author_filter: Optional[str] = None,
    mime_types: Optional[set] = None,
    max_groups: Optional[int] = None,
) -> str:
    """Build Drive search query with MIME type filter, keyword groups, and advanced filters.

    If keyword_groups is provided, it builds a grouped query:
    e.g. ((fullText contains 'A' or fullText contains 'A_syn') and (fullText contains 'B')) and (mimeType=...)
    
    Args:
        mime_types: 指定搜索的 MIME type 集合，默认使用全部支持的类型
        max_groups: 最多使用多少个关键词组（用于 doc/slide 宽召回时限制组数）
    """
    if mime_types is None:
        mime_types = _SUPPORTED_MIMETYPES
    mime_clauses = [f"mimeType='{mt}'" for mt in mime_types]
    mime_filter = " or ".join(mime_clauses)
    search_field = "fullText" if enable_fulltext else "name"
    
    # 限制关键词组数量
    if keyword_groups and max_groups:
        keyword_groups = keyword_groups[:max_groups]
    
    base_query_filter = ""
    if keyword_groups:
        group_clauses = []
        for group in keyword_groups:
            if not group:
                continue
            # 限制每组同义词数量，避免过宽 OR 导致语义漂移和 q 语法过长
            limited_group = group[:3]
            # 组内使用 OR
            term_clauses = []
            for term in limited_group:
                escaped = _escape_drive_q_term(term)
                if escaped:
                    term_clauses.append(f"{search_field} contains '{escaped}'")
            if term_clauses:
                group_clauses.append("(" + " or ".join(term_clauses) + ")")
        
        # 组间使用 AND
        if group_clauses:
            base_query_filter = "(" + " and ".join(group_clauses) + ")"
            
    if not base_query_filter:
        # Fallback 到传统的完整字符串匹配
        escaped = _escape_drive_q_term(query)
        if escaped:
            base_query_filter = f"({search_field} contains '{escaped}')"
        else:
            base_query_filter = f"({mime_filter})"
            filters = [base_query_filter]
            # 注入高级 Time 过滤
            if time_filter:
                time_filter = time_filter.strip()
                match = re.match(r"^([><=]+)\s*(.*)$", time_filter)
                if match:
                    op, date_str = match.groups()
                    if "T" not in date_str:
                        date_str = f"{date_str}T00:00:00"
                    filters.append(f"(modifiedTime {op} '{date_str}' or createdTime {op} '{date_str}')")
                else:
                    date_str = time_filter
                    if "T" not in date_str:
                        date_str = f"{date_str}T00:00:00"
                    filters.append(f"(modifiedTime > '{date_str}' or createdTime > '{date_str}')")
            if author_filter and author_filter.lower() == "me":
                filters.append("('me' in owners or 'me' in writers)")
            return " and ".join(filters)

    filters = [base_query_filter, f"({mime_filter})"]
    
    # 注入高级 Time 过滤（比如 >2025-09-01）
    if time_filter:
        time_filter = time_filter.strip()
        # 简单解析支持的操作符：>, <, >=, <=, =
        import re
        match = re.match(r"^([><=]+)\s*(.*)$", time_filter)
        if match:
            op, date_str = match.groups()
            # 确保变成 ISO 时间戳，追加 T00:00:00
            if "T" not in date_str:
                date_str = f"{date_str}T00:00:00"
            filters.append(f"(modifiedTime {op} '{date_str}' or createdTime {op} '{date_str}')")
        else:
            # 如果没有操作符，默认使用 > (大于等于当天)
            date_str = time_filter
            if "T" not in date_str:
                date_str = f"{date_str}T00:00:00"
            filters.append(f"(modifiedTime > '{date_str}' or createdTime > '{date_str}')")

    # 注入 Owner / Author 过滤
    if author_filter and author_filter.lower() == "me":
        filters.append("('me' in owners or 'me' in writers)")

    return " and ".join(filters)


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
    # BM25 + 关键词扩展评分（与 Feishu 对齐）
    score = 0.5
    if query:
        try:
            from tools.core.chunk_scorer import bm25_score
            from tools.core.kw_generalization import expand_keywords
            import re

            raw_kw = set(re.findall(r'[\w\u4e00-\u9fff]{2,}', query.lower()))
            expanded_q = " ".join(expand_keywords(raw_kw))
            score_text = f"{gc.file_title or ''} {gc.heading_path or ''} {gc.content or ''}"[:800]
            raw_bm25 = bm25_score(expanded_q, score_text)
            score = min(1.0, raw_bm25 / 5.0)
        except Exception:
            pass

    # 根据 chunk_id 前缀推断更细粒度的 source_tool，供 eval 区分来源
    chunk_id = gc.chunk_id or ""
    if chunk_id.startswith("google:doc:"):
        source_tool = "google:doc"
    elif chunk_id.startswith("google:sheet:"):
        source_tool = "google:sheet"
    elif chunk_id.startswith("google:slide:"):
        source_tool = "google:slide"
    elif chunk_id.startswith("google:word:"):
        source_tool = "google:word"
    elif chunk_id.startswith("google:excel:"):
        source_tool = "google:excel"
    elif chunk_id.startswith("google:pptx:"):
        source_tool = "google:pptx"
    elif chunk_id.startswith("google:txt:") or chunk_id.startswith("google:csv:"):
        source_tool = "google:txt"
    elif chunk_id.startswith("google:gmail:body:"):
        source_tool = "google:gmail:body"
    elif chunk_id.startswith("google:gmail:att:"):
        source_tool = "google:gmail:att"
    else:
        source_tool = "google"

    return UnifiedChunk(
        text=gc.content or "",
        source_name=gc.file_title or "",
        source_url=gc.file_url or "",
        source_tool=source_tool,
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
        # 构建丰富的摘要用于 LLM 排序
        desc = f.get("description") or f.get("mimeType", "")
        owners = ", ".join([o.get("displayName", "") for o in f.get("owners", []) if o.get("displayName")])
        modified_time = f.get("modifiedTime") or f.get("createdTime", "Unknown")
        viewed_time = f.get("viewedByMeTime", "Not viewed")
        
        summary_parts = [
            f"Type: {f.get('mimeType', 'Unknown')}",
            f"Desc: {desc}",
        ]
        if owners:
            summary_parts.append(f"Owners: {owners}")
        summary_parts.append(f"Modified: {modified_time}")
        summary_parts.append(f"Last Viewed: {viewed_time}")
        
        rich_summary = " | ".join(summary_parts)

        candidates.append({
            "id": f.get("id"),
            "title": f.get("name", ""),
            "summary": rich_summary,
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
    keyword_groups: Optional[List[List[str]]] = None,
    time_filter: Optional[str] = None,
    author_filter: Optional[str] = None,
    max_results: int = 20,
    max_chunk_tokens: int = 800,
    download_timeout: int = 12,
    native_api_timeout: int = 12,
    enable_sheet_filter: bool = False,
    enable_excel_filter: bool = False,
    enable_fulltext_search: bool = True,
    order_by_mode: Optional[str] = None,
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

    q = _build_search_q(
        query, 
        keyword_groups=keyword_groups, 
        enable_fulltext=enable_fulltext_search,
        time_filter=time_filter,
        author_filter=author_filter,
    )
    
    # 仅在非全文搜索时允许自定义排序
    order_by = order_by_mode if not enable_fulltext_search else None
    
    logger.info("google_retrieve: query=%r, drive_q=%.80r, order_by=%r", query, q, order_by)

    try:
        # 初次召回更多文件
        files = search_drive(
            q=q,
            page_size=min(max_results * 2, 100),
            max_pages=1,
            order_by=order_by,
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
    keyword_groups: Optional[List[List[str]]] = None,
    time_filter: Optional[str] = None,
    author_filter: Optional[str] = None,
    max_results: int = 10,
    max_chunk_tokens: int = 800,
    download_timeout: int = 12,
    native_api_timeout: int = 12,
    enable_gmail: bool = False,
    enable_gmail_attachments: bool = False,
    enable_sheet_filter: bool = False,
    enable_excel_filter: bool = False,
    enable_fulltext_search: bool = True,
    order_by_mode: Optional[str] = None,
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
        enable_fulltext_search: 是否启用 Drive 全文检索 (True: fullText contains, False: name contains)
        order_by_mode: 非全文检索时的排序方式 (e.g. "modifiedTime desc", "viewedByMeTime desc")

    Returns:
        List[UnifiedChunk]
    """
    drive_chunks = await asyncio.to_thread(
        _sync_google_retrieve,
        query,
        keyword_groups,
        time_filter,
        author_filter,
        max_results,
        max_chunk_tokens,
        download_timeout,
        native_api_timeout,
        enable_sheet_filter,
        enable_excel_filter,
        enable_fulltext_search,
        order_by_mode,
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
