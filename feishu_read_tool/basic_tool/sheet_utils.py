"""
read_sheet_data.py — 表格结构读取工具模块

提供飞书 Sheet 坐标计算、Token 解析、范围构造等工具函数。
后期扩展方向：表头识别、结构化信息提取、空行/空列裁剪等复杂表格读取逻辑。
不依赖飞书 SDK，所有函数为无副作用的纯函数。
"""


import re


def number_to_column(n: int) -> str:
    """将列索引（0-based）转换为 Excel 列名

    Args:
        n: 列索引（从 0 开始），0 -> A, 25 -> Z, 26 -> AA

    Returns:
        Excel 列名字符串

    Examples:
        >>> number_to_column(0)
        'A'
        >>> number_to_column(25)
        'Z'
        >>> number_to_column(26)
        'AA'
    """
    result = ""
    n += 1
    while n > 0:
        n -= 1
        result = chr(ord('A') + n % 26) + result
        n //= 26
    return result


def parse_sheet_token(token: str) -> tuple[str, str]:
    """解析 Sheet Token

    Args:
        token: Sheet Token，格式为 {spreadsheet_token}_{sheet_id}
               示例: "PZ64sJ5FRh45cJtNbEBcyJdDnQg_fgvTJC"

    Returns:
        (spreadsheet_token, sheet_id) 元组

    Raises:
        ValueError: Token 格式不符合预期

    Examples:
        >>> parse_sheet_token("PZ64sJ5FRh45cJtNbEBcyJdDnQg_fgvTJC")
        ('PZ64sJ5FRh45cJtNbEBcyJdDnQg', 'fgvTJC')
    """
    # Block payloads occasionally carry copy/paste whitespace or invisible
    # separators around an otherwise valid ``spreadsheet_sheet`` token.  Tokens
    # themselves are identifier-only, so removing Unicode whitespace here is
    # a local normalization step, not an extra validity API call.
    normalized = re.sub(r"[\s\u00a0\u200b\ufeff]+", "", str(token or ""))
    parts = normalized.split('_')
    if (len(parts) != 2 or not parts[0] or not parts[1]
            or not re.fullmatch(r"[A-Za-z0-9]+", parts[0])
            or not re.fullmatch(r"[A-Za-z0-9]+", parts[1])):
        raise ValueError("Invalid sheet token format")
    return parts[0], parts[1]


def build_range_notation(sheet_id: str, row_count: int, column_count: int) -> str:
    """构造 Excel 风格的范围表示法

    Args:
        sheet_id: 工作表 ID
        row_count: 行数
        column_count: 列数

    Returns:
        范围字符串，格式为 {sheet_id}!A1:{end_col}{end_row}

    Examples:
        >>> build_range_notation("fgvTJC", 10, 4)
        'fgvTJC!A1:D10'
    """
    end_col = number_to_column(column_count - 1)
    return f"{sheet_id}!A1:{end_col}{row_count}"


def _extract_cell_text(text_block: dict) -> str:
    """从单个 type2 Text block 中提取纯文本内容"""
    text_obj = text_block.get('text')
    if text_obj is None:
        return ""
    if hasattr(text_obj, 'elements'):
        elements = text_obj.elements or []
    elif isinstance(text_obj, dict):
        elements = text_obj.get('elements') or []
    else:
        return ""
    cell_text = ""
    for elem in elements:
        if hasattr(elem, 'text_run') and elem.text_run:
            cell_text += elem.text_run.content or ""
        elif isinstance(elem, dict):
            tr = elem.get('text_run')
            if tr:
                cell_text += (tr.get('content') or "") if isinstance(tr, dict) else (tr.content or "")
    return cell_text


def parse_grid_to_table(block: dict, blocks_dict: dict) -> list:
    """将 type31 Grid Block 转换为二维表格（行优先）

    飞书 Grid 的存储结构是「行优先的列布局」：
        block_type=31 (Grid)  grid.column_size = C
          children: [type32_r0c0, type32_r0c1, ..., type32_r0c(C-1),
                     type32_r1c0, type32_r1c1, ..., type32_r1c(C-1), ...]

    即每 C 个连续 type32 构成一行。每个 type32 内部可有多个 type2 段落
    （多段落合并为 " / " 分隔的单元格文本，忽略纯空段落）。

    若 column_size 缺失，回退到将所有 type32 展为单行。

    Args:
        block: type31 Block 数据字典（应含 grid.column_size）
        blocks_dict: {block_id: block} 全量索引

    Returns:
        List[List[str]] 行优先二维表格，空单元格以 "" 补位
    """
    col_ids = block.get('children') or []
    if not col_ids:
        return []

    def get_col_text(col_id: str) -> str:
        col_block = blocks_dict.get(col_id)
        if col_block is None or col_block.get('block_type') != 32:
            return ""
        parts = []
        for cell_id in (col_block.get('children') or []):
            text_block = blocks_dict.get(cell_id)
            if text_block is not None:
                t = _extract_cell_text(text_block)
                if t:  # 忽略纯空段落
                    parts.append(t)
        return " / ".join(parts)

    # 确定每行列数 column_size
    column_size = block.get('table_column_size')
    if not column_size or column_size <= 0:
        # 回退：所有 type32 展为单行
        column_size = len(col_ids)

    # 按 column_size 分组，每组是一行
    rows = []
    for i in range(0, len(col_ids), column_size):
        chunk = col_ids[i:i + column_size]
        row = [get_col_text(cid) for cid in chunk]
        while len(row) < column_size:
            row.append("")
        rows.append(row)
    return rows
