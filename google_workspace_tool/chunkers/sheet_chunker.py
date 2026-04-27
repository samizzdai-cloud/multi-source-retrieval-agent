"""Google Sheet 专用 Chunk 切分器。

借鉴 feishu_read_tool/table_handler.py 的 header 检测 + 按行切分策略，
适配 Google Sheets API 返回的 2D 数组格式。

策略：
- 小表（≤20行 且 ≤10列）：整个 Sheet 作为一个 TABLE_MD chunk
- 宽表（≤10行 且 ≤20列）：同上
- 大表：按行切分，header carry-forward（每 chunk 20行数据）
- 多 Sheet：每个 Sheet 独立处理
"""

from __future__ import annotations

import re
from typing import List, Dict, Optional

from .md_chunker import GoogleChunk, ChunkContentType, estimate_tokens


# ── Header detection ────────────────────────────────────────────

def is_header_cell(text: str) -> bool:
    """True if cell looks like a descriptive label rather than a data value.
    True: pure text, no digits  OR  label patterns like Q1 / 2025年 / 第一季度
    False: pure numbers, numbers+units (120 / 120万 / 98%), empty string
    """
    if not text or not text.strip():
        return False
    # Pure number or number+unit
    if re.fullmatch(r"[\d,，.。%％万亿千百十元$€£\s]+", text.strip()):
        return False
    return True


def detect_header_axes(rows: List[List[str]]) -> dict:
    """Determine if first row and/or first column are header axes.
    Returns dict with has_row_header, has_col_header, header_structure,
    col_headers, row_headers.
    """
    if not rows or len(rows) < 2:
        return {
            "has_row_header": False,
            "has_col_header": False,
            "header_structure": "none",
            "col_headers": [],
            "row_headers": [],
        }

    THRESHOLD = 0.8

    first_row = rows[0]
    first_col = [row[0] for row in rows if row]

    def header_ratio(cells):
        if not cells:
            return 0.0
        return sum(1 for c in cells if is_header_cell(c)) / len(cells)

    has_row_header = header_ratio(first_row) >= THRESHOLD
    has_col_header = header_ratio(first_col) >= THRESHOLD

    if has_row_header and has_col_header:
        structure = "both"
    elif has_row_header:
        structure = "row_only"
    elif has_col_header:
        structure = "col_only"
    else:
        structure = "none"

    return {
        "has_row_header": has_row_header,
        "has_col_header": has_col_header,
        "header_structure": structure,
        "col_headers": list(first_row) if has_row_header else [],
        "row_headers": [row[0] for row in rows if row] if has_col_header else [],
    }


# ── Markdown rendering ──────────────────────────────────────────

def _values_to_markdown(rows: List[List[str]]) -> str:
    """Render 2D string array as a Markdown table."""
    if not rows:
        return ""

    max_cols = max(len(row) for row in rows) if rows else 0

    def escape(cell: str) -> str:
        return str(cell).replace("|", "\\|").replace("\n", " ")

    lines = []
    header = rows[0]
    lines.append("| " + " | ".join(escape(c) for c in header) + " |")
    lines.append("| " + " | ".join("---" for _ in range(max_cols)) + " |")

    for row in rows[1:]:
        padded = list(row) + [""] * max(0, max_cols - len(row))
        lines.append("| " + " | ".join(escape(c) for c in padded[:max_cols]) + " |")

    return "\n".join(lines)


# ── Row splitting ───────────────────────────────────────────────

def _is_blank_row(row: List[str]) -> bool:
    return all(not str(c).strip() for c in row)


def _strip_trailing_blank_rows(rows: List[List[str]]) -> List[List[str]]:
    while rows and _is_blank_row(rows[-1]):
        rows.pop()
    return rows


def _strip_trailing_blank_cols(rows: List[List[str]]) -> List[List[str]]:
    if not rows:
        return rows
    ncols = max(len(r) for r in rows)
    last_nonempty_col = -1
    for col_idx in range(ncols - 1, -1, -1):
        if any(len(r) > col_idx and str(r[col_idx]).strip() for r in rows):
            last_nonempty_col = col_idx
            break
    if last_nonempty_col >= 0:
        return [r[:last_nonempty_col + 1] for r in rows]
    return []


def split_sheet_to_chunks(
    rows: List[List[str]],
    sheet_title: str,
    file_id: str,
    file_title: str,
    file_url: str,
    rows_per_chunk: int = 20,
    metadata: Optional[Dict] = None,
) -> List[GoogleChunk]:
    """将 Sheet 的 2D 数组切分为 GoogleChunk 列表。

    Args:
        rows: 2D 字符串数组（来自 Sheets API values）
        sheet_title: Sheet 名称
        file_id: Spreadsheet ID
        file_title: 文件标题
        file_url: 文件 URL
        rows_per_chunk: 每 chunk 的数据行数（不含 header）
        metadata: 附加元数据

    Returns:
        List[GoogleChunk]
    """
    if not rows:
        return []

    # Strip trailing blanks
    rows = _strip_trailing_blank_rows(list(rows))
    rows = _strip_trailing_blank_cols(rows)
    if not rows:
        return []

    # Skip all-blank sheets
    if all(_is_blank_row(row) for row in rows):
        return []

    header_info = detect_header_axes(rows)
    has_row_header = header_info.get("has_row_header", False)

    if has_row_header and len(rows) > 1:
        headers = rows[0]
        data_rows = rows[1:]
    else:
        headers = None
        data_rows = rows

    # Small table threshold: ≤20 rows and ≤10 cols (or ≤10 rows and ≤20 cols)
    nrows = len(rows)
    ncols = max(len(r) for r in rows) if rows else 0
    is_small = (
        (nrows <= 20 and ncols <= 10) or
        (nrows <= 10 and ncols <= 20)
    )

    # If small, single chunk
    if is_small:
        md_table = _values_to_markdown(rows)
        heading_path = sheet_title
        content = f"## Sheet: {sheet_title}\n\n{md_table}" if heading_path else md_table
        chunk_id = f"google:sheet:{file_id}:{sheet_title}:0"
        return [GoogleChunk(
            chunk_id=chunk_id,
            file_id=file_id,
            file_title=file_title,
            file_url=file_url,
            heading_path=heading_path,
            content_type=ChunkContentType.TABLE_MD,
            content=content,
            token_count=estimate_tokens(content),
            has_table=True,
            metadata={**(metadata or {}), "sheet_name": sheet_title, "row_count": nrows, "col_count": ncols},
        )]

    # Large table: split by rows with header carry-forward
    chunks: List[GoogleChunk] = []
    base_meta = dict(metadata or {})
    base_meta.update({
        "sheet_name": sheet_title,
        "row_count": nrows,
        "col_count": ncols,
        "header_structure": header_info.get("header_structure", "none"),
    })

    for i in range(0, len(data_rows), rows_per_chunk):
        batch = data_rows[i:i + rows_per_chunk]
        if headers:
            batch_to_render = [headers] + batch
        else:
            batch_to_render = batch

        md_table = _values_to_markdown(batch_to_render)
        heading_path = sheet_title
        content = f"## Sheet: {sheet_title}\n\n{md_table}" if heading_path else md_table
        chunk_idx = i // rows_per_chunk
        chunk_id = f"google:sheet:{file_id}:{sheet_title}:{chunk_idx}"

        chunks.append(GoogleChunk(
            chunk_id=chunk_id,
            file_id=file_id,
            file_title=file_title,
            file_url=file_url,
            heading_path=heading_path,
            content_type=ChunkContentType.TABLE_MD,
            content=content,
            token_count=estimate_tokens(content),
            has_table=True,
            metadata={
                **base_meta,
                "rows_in_chunk": len(batch),
                "row_index_offset": i + (1 if has_row_header else 0),
            },
        ))

    return chunks
