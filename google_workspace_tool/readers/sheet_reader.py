"""Google Sheets 专用读取器。

提供单文件读取、批量串行读取、批量并发读取三种模式。
支持读取整个 Spreadsheet（所有工作表）、指定 Range、批量读取多个 Ranges。
输出支持 Markdown 表格、CSV、JSON、原始值数组等多种格式。

核心 API：
- spreadsheets().get()          — 获取 Spreadsheet 元数据（标题、Sheet 列表等）
- spreadsheets().values().get()  — 读取单个 Range
- spreadsheets().values().batchGet() — 批量读取多个 Ranges

认证：
由调用方传入已授权的 Credentials 对象，本模块不自行处理 OAuth2 流程。
"""

from __future__ import annotations

import csv
import io
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

_MIME_SHEETS = "application/vnd.google-apps.spreadsheet"

# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _build_service(creds) -> Any:
    """构建 Sheets API v4 service 对象。"""
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _get_spreadsheet_metadata(service, spreadsheet_id: str) -> dict:
    """获取 Spreadsheet 元数据（标题、Sheet 列表等）。"""
    try:
        result = (
            service.spreadsheets()
            .get(spreadsheetId=spreadsheet_id, fields="properties.title,sheets(properties.sheetId,properties.title)")
            .execute()
        )
        return result
    except HttpError:
        return {}


def _get_values(service, spreadsheet_id: str, range_name: str, **kwargs) -> dict:
    """读取单个 Range 的数据。"""
    params = {
        "spreadsheetId": spreadsheet_id,
        "range": range_name,
    }
    # 支持可选参数：valueRenderOption, dateTimeRenderOption, majorDimension
    for key in ("valueRenderOption", "dateTimeRenderOption", "majorDimension"):
        if key in kwargs:
            params[key] = kwargs[key]

    result = service.spreadsheets().values().get(**params).execute()
    return result


def _batch_get_values(service, spreadsheet_id: str, range_names: list[str], **kwargs) -> dict:
    """批量读取多个 Ranges 的数据。"""
    params = {
        "spreadsheetId": spreadsheet_id,
        "ranges": range_names,
    }
    for key in ("valueRenderOption", "dateTimeRenderOption", "majorDimension"):
        if key in kwargs:
            params[key] = kwargs[key]

    result = service.spreadsheets().values().batchGet(**params).execute()
    return result


def _values_to_markdown(rows: list[list[Any]]) -> str:
    """将二维数组转换为 Markdown 表格。"""
    if not rows:
        return ""

    # 统一列数
    max_cols = max(len(row) for row in rows) if rows else 0
    padded = []
    for row in rows:
        padded_row = list(row) + [""] * (max_cols - len(row))
        # 转义 Markdown 表格中的特殊字符
        escaped = [str(cell).replace("|", "\\|").replace("\n", " ") for cell in padded_row]
        padded.append(escaped)

    lines = []
    # 表头
    lines.append("| " + " | ".join(padded[0]) + " |")
    # 分隔符
    lines.append("| " + " | ".join(["---"] * max_cols) + " |")
    # 数据行
    for row in padded[1:]:
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


def _values_to_csv(rows: list[list[Any]]) -> str:
    """将二维数组转换为 CSV 字符串。"""
    if not rows:
        return ""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    for row in rows:
        writer.writerow(row)
    return output.getvalue()


def _values_to_plain(rows: list[list[Any]], delimiter: str = "\t") -> str:
    """将二维数组转换为纯文本（默认制表符分隔）。"""
    if not rows:
        return ""
    lines = []
    for row in rows:
        lines.append(delimiter.join(str(cell) for cell in row))
    return "\n".join(lines)


def _extract_all_sheets(service, spreadsheet_id: str, metadata: dict, **kwargs) -> dict:
    """读取 Spreadsheet 中所有可见 Sheet 的数据。

    Returns:
        dict: {sheet_title: {"range": ..., "values": [[...], ...], "row_count": N, "col_count": M}, ...}
    """
    sheets = metadata.get("sheets", [])
    if not sheets:
        # 元数据获取失败，不尝试 range=''（已知对某些文件返回 404）
        return {
            "_error": {
                "error": "无法获取 Spreadsheet 元数据，无法读取 sheet 列表",
                "values": [],
                "row_count": 0,
                "col_count": 0,
            }
        }

    all_data = {}
    for sheet in sheets:
        title = sheet.get("properties", {}).get("title", "")
        if not title:
            continue
        # 读取整个 Sheet（不指定具体范围）
        range_name = f"'{title}'" if " " in title or "'" in title else title
        try:
            result = _get_values(service, spreadsheet_id, range_name, **kwargs)
            values = result.get("values", [])
            all_data[title] = {
                "range": result.get("range", ""),
                "values": values,
                "row_count": len(values),
                "col_count": max(len(row) for row in values) if values else 0,
            }
        except HttpError as e:
            all_data[title] = {"error": str(e), "values": [], "row_count": 0, "col_count": 0}

    return all_data


def _build_structured_md(
    title: str,
    sheets_data: dict,
    include_md_table: bool = True,
) -> str:
    """构建结构化 Markdown 输出。"""
    lines = []
    if title:
        lines.append(f"# {title}")
        lines.append("")

    for sheet_title, data in sheets_data.items():
        lines.append(f"## Sheet: {sheet_title}")
        lines.append("")

        if "error" in data:
            lines.append(f"**Error:** {data['error']}")
            lines.append("")
            continue

        values = data.get("values", [])
        if not values:
            lines.append("*(空表 — 无数据)*")
            lines.append("")
            continue

        if include_md_table:
            md_table = _values_to_markdown(values)
            lines.append(md_table)
            lines.append("")

        lines.append(f"*行数: {data.get('row_count', 0)}, 列数: {data.get('col_count', 0)}*")
        lines.append("")

    return "\n".join(lines)


def _build_result_dict(
    file_id: str,
    title: str,
    sheets_data: dict,
    metadata: dict,
    output_format: str = "md",
    error: str | None = None,
    mode: str = "full",
    drive_size: Optional[int] = None,
) -> dict:
    """构造统一格式的返回字典。"""
    if error:
        return {
            "file_id": file_id,
            "mime_type": _MIME_SHEETS,
            "title": title or "",
            "content": None,
            "structured_md": None,
            "content_type": None,
            "raw_bytes": None,
            "error": error,
            "mode": mode,
            "drive_size": drive_size,
            "skip_reason": None,
            "sheets": {},
            "metadata": metadata,
        }

    # 构建不同格式的 content
    content = None
    if output_format == "md":
        content = _build_structured_md(title, sheets_data, include_md_table=True)
    elif output_format == "csv":
        # 多个 sheet 时，用分隔线拼接
        parts = []
        for sheet_title, data in sheets_data.items():
            parts.append(f"# Sheet: {sheet_title}")
            parts.append(_values_to_csv(data.get("values", [])))
        content = "\n".join(parts)
    elif output_format == "json":
        content = json.dumps(sheets_data, ensure_ascii=False, indent=2)
    elif output_format == "plain":
        parts = []
        for sheet_title, data in sheets_data.items():
            parts.append(f"Sheet: {sheet_title}")
            parts.append(_values_to_plain(data.get("values", [])))
        content = "\n\n".join(parts)
    elif output_format == "raw":
        content = sheets_data
    else:
        content = _build_structured_md(title, sheets_data, include_md_table=True)

    # structured_md 始终为 Markdown 格式
    structured_md = _build_structured_md(title, sheets_data, include_md_table=True)

    return {
        "file_id": file_id,
        "mime_type": _MIME_SHEETS,
        "title": title or "",
        "content": content,
        "structured_md": structured_md,
        "content_type": output_format if output_format != "raw" else "dict",
        "raw_bytes": None,
        "error": None,
        "mode": mode,
        "drive_size": drive_size,
        "skip_reason": None,
        "sheets": sheets_data,
        "metadata": metadata,
    }


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def read_one(
    file_id: str,
    creds,
    range_name: Optional[str] = None,
    range_names: Optional[list[str]] = None,
    output_format: str = "md",
    value_render_option: Optional[str] = None,
    date_time_render_option: Optional[str] = None,
    major_dimension: Optional[str] = None,
    drive_size: Optional[int] = None,
) -> dict:
    """读取单个 Google Spreadsheet。

    Args:
        file_id: Spreadsheet ID（从 URL 中的 /d/SPREADSHEET_ID/ 获取）
        creds: 已授权的 Credentials 对象
        range_name: 要读取的单个 A1 范围，例如 "Sheet1!A1:D10"。
                    为 None 时读取所有 Sheet。
        range_names: 要批量读取的多个 A1 范围列表，例如 ["Sheet1!A1:C3", "Sheet2!A1:C3"]。
                     与 range_name 互斥，优先使用 range_names。
        output_format: 输出格式，可选 "md"(默认), "csv", "json", "plain", "raw"
        value_render_option: 值渲染选项，可选 "FORMATTED_VALUE", "UNFORMATTED_VALUE", "FORMULA"
        date_time_render_option: 日期时间渲染选项，可选 "SERIAL_NUMBER", "FORMATTED_STRING"
        major_dimension: 主维度，可选 "ROWS", "COLUMNS"

    Returns:
        统一格式的 dict，包含 structured_md, content, sheets 等字段

    Examples:
        >>> # 读取整个 Spreadsheet
        >>> result = read_one(file_id="1xxx", creds=creds)
        >>>
        >>> # 读取指定 Range
        >>> result = read_one(file_id="1xxx", creds=creds, range_name="Sheet1!A1:C10")
        >>>
        >>> # 批量读取多个 Ranges
        >>> result = read_one(file_id="1xxx", creds=creds,
        ...                   range_names=["Sheet1!A1:C3", "Sheet2!A1:C3"])
    """
    service = _build_service(creds)
    kwargs = {}
    if value_render_option:
        kwargs["valueRenderOption"] = value_render_option
    if date_time_render_option:
        kwargs["dateTimeRenderOption"] = date_time_render_option
    if major_dimension:
        kwargs["majorDimension"] = major_dimension

    # 获取元数据
    metadata = _get_spreadsheet_metadata(service, file_id)
    title = metadata.get("properties", {}).get("title", "")

    try:
        if range_names:
            # --- 模式 1: 批量读取多个 Ranges ---
            result = _batch_get_values(service, file_id, range_names, **kwargs)
            value_ranges = result.get("valueRanges", [])
            sheets_data = {}
            for vr in value_ranges:
                rng = vr.get("range", "")
                values = vr.get("values", [])
                # range 格式通常为 "Sheet1!A1:C3" 或 "'My Sheet'!A1:C3"
                sheet_title = rng.split("!")[0].strip("'") if "!" in rng else rng
                sheets_data[sheet_title] = {
                    "range": rng,
                    "values": values,
                    "row_count": len(values),
                    "col_count": max(len(row) for row in values) if values else 0,
                }
            return _build_result_dict(file_id, title, sheets_data, metadata, output_format, drive_size=drive_size)

        elif range_name:
            # --- 模式 2: 读取单个 Range ---
            result = _get_values(service, file_id, range_name, **kwargs)
            values = result.get("values", [])
            rng = result.get("range", range_name)
            sheet_title = rng.split("!")[0].strip("'") if "!" in rng else rng
            sheets_data = {
                sheet_title: {
                    "range": rng,
                    "values": values,
                    "row_count": len(values),
                    "col_count": max(len(row) for row in values) if values else 0,
                }
            }
            return _build_result_dict(file_id, title, sheets_data, metadata, output_format, drive_size=drive_size)

        else:
            # --- 模式 3: 读取所有 Sheet ---
            sheets_data = _extract_all_sheets(service, file_id, metadata, **kwargs)
            return _build_result_dict(file_id, title, sheets_data, metadata, output_format, drive_size=drive_size)

    except HttpError as e:
        return _build_result_dict(
            file_id=file_id,
            title=title,
            sheets_data={},
            metadata=metadata,
            error=f"Sheets API 错误: {e}",
        )
    except Exception as e:
        return _build_result_dict(
            file_id=file_id,
            title=title,
            sheets_data={},
            metadata=metadata,
            error=f"读取失败: {e}",
        )


def read_batch(
    file_ids: list[str],
    creds,
    range_name: Optional[str] = None,
    range_names: Optional[list[str]] = None,
    output_format: str = "md",
    value_render_option: Optional[str] = None,
    date_time_render_option: Optional[str] = None,
    major_dimension: Optional[str] = None,
    drive_size: Optional[int] = None,
) -> list[dict]:
    """串行批量读取多个 Google Spreadsheet。

    按顺序一个一个读取，适用于需要控制 API 速率的场景。
    所有参数与 read_one 相同，作用于每个 Spreadsheet。

    Args:
        file_ids: Spreadsheet ID 列表
        drive_size: Drive 文件大小（字节），用于结果回填
        其他参数同 read_one

    Returns:
        与 file_ids 顺序对应的 result dict 列表
    """
    results = []
    for fid in file_ids:
        try:
            result = read_one(
                file_id=fid,
                creds=creds,
                range_name=range_name,
                range_names=range_names,
                output_format=output_format,
                value_render_option=value_render_option,
                date_time_render_option=date_time_render_option,
                major_dimension=major_dimension,
                drive_size=drive_size,
            )
            results.append(result)
        except Exception as e:
            results.append(
                _build_result_dict(
                    file_id=fid,
                    title="",
                    sheets_data={},
                    metadata={},
                    error=f"批量读取失败: {e}",
                    drive_size=drive_size,
                )
            )
    return results


def read_concurrent(
    file_ids: list[str],
    creds,
    range_name: Optional[str] = None,
    range_names: Optional[list[str]] = None,
    output_format: str = "md",
    value_render_option: Optional[str] = None,
    date_time_render_option: Optional[str] = None,
    major_dimension: Optional[str] = None,
    max_workers: int = 5,
    drive_size: Optional[int] = None,
) -> list[dict]:
    """并发批量读取多个 Google Spreadsheet。

    使用 ThreadPoolExecutor 并行调用 Sheets API，适合读取大量 Spreadsheet。

    Args:
        file_ids: Spreadsheet ID 列表
        max_workers: 最大并发线程数，默认 5
        drive_size: Drive 文件大小（字节），用于结果回填
        其他参数同 read_one

    Returns:
        与 file_ids 顺序对应的 result dict 列表

    Note:
        Google Sheets API 有配额限制（默认每秒 100 次请求）。
        如果读取的文件数量很大，建议适当降低 max_workers 或改用 read_batch。
    """

    def _read(fid: str) -> dict:
        try:
            return read_one(
                file_id=fid,
                creds=creds,
                range_name=range_name,
                range_names=range_names,
                output_format=output_format,
                value_render_option=value_render_option,
                date_time_render_option=date_time_render_option,
                major_dimension=major_dimension,
                drive_size=drive_size,
            )
        except Exception as e:
            return _build_result_dict(
                file_id=fid,
                title="",
                sheets_data={},
                metadata={},
                error=f"并发读取失败: {e}",
                drive_size=drive_size,
            )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(_read, file_ids))

    return results


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def read_ranges(
    file_id: str,
    creds,
    range_names: list[str],
    output_format: str = "md",
    **kwargs,
) -> dict:
    """便捷函数：批量读取同一个 Spreadsheet 的多个 Ranges。

    等价于 read_one(file_id, creds, range_names=range_names, ...)
    """
    return read_one(
        file_id=file_id,
        creds=creds,
        range_names=range_names,
        output_format=output_format,
        **kwargs,
    )


def get_spreadsheet_info(
    file_id: str,
    creds,
    fields: Optional[str] = None,
) -> dict:
    """获取 Spreadsheet 的元数据信息（标题、Sheet 列表、行列数等）。

    Args:
        file_id: Spreadsheet ID
        creds: 已授权的 Credentials 对象
        fields: 指定返回字段，默认返回全部常用字段。
                精简字段示例: "properties.title,sheets.properties.title"
                完整字段示例: "properties(title,locale,timeZone),sheets.properties(title,gridProperties)"

    Returns:
        dict 包含 title, sheets 列表, locale, timeZone 等信息
    """
    service = _build_service(creds)
    try:
        if fields:
            metadata = service.spreadsheets().get(
                spreadsheetId=file_id, fields=fields
            ).execute()
        else:
            metadata = service.spreadsheets().get(spreadsheetId=file_id).execute()
        props = metadata.get("properties", {})
        sheets = metadata.get("sheets", [])
        return {
            "file_id": file_id,
            "title": props.get("title", ""),
            "locale": props.get("locale", ""),
            "timeZone": props.get("timeZone", ""),
            "sheet_count": len(sheets),
            "sheets": [
                {
                    "sheetId": s.get("properties", {}).get("sheetId"),
                    "title": s.get("properties", {}).get("title"),
                    "index": s.get("properties", {}).get("index"),
                    "sheetType": s.get("properties", {}).get("sheetType"),
                    "gridProperties": s.get("properties", {}).get("gridProperties", {}),
                }
                for s in sheets
            ],
            "error": None,
        }
    except HttpError as e:
        return {
            "file_id": file_id,
            "title": "",
            "locale": "",
            "timeZone": "",
            "sheet_count": 0,
            "sheets": [],
            "error": f"Sheets API 错误: {e}",
        }


def read_chunks(
    file_id: str,
    creds,
    range_name: Optional[str] = None,
    range_names: Optional[list[str]] = None,
    value_render_option: Optional[str] = None,
    date_time_render_option: Optional[str] = None,
    major_dimension: Optional[str] = None,
    rows_per_chunk: int = 20,
) -> list:
    """读取 Google Spreadsheet 并切分为 chunks。

    Returns:
        List[GoogleChunk]
    """
    from chunkers import split_sheet_to_chunks
    result = read_one(
        file_id=file_id,
        creds=creds,
        range_name=range_name,
        range_names=range_names,
        output_format="raw",
        value_render_option=value_render_option,
        date_time_render_option=date_time_render_option,
        major_dimension=major_dimension,
    )
    if result.get("error") or not result.get("sheets"):
        return []
    title = result.get("title", "")
    file_url = f"https://docs.google.com/spreadsheets/d/{file_id}/edit"
    all_chunks = []
    for sheet_title, data in result["sheets"].items():
        if isinstance(data, dict) and "error" in data:
            continue
        values = data.get("values", []) if isinstance(data, dict) else []
        if not values:
            continue
        chunks = split_sheet_to_chunks(
            rows=values,
            sheet_title=sheet_title,
            file_id=file_id,
            file_title=title,
            file_url=file_url,
            rows_per_chunk=rows_per_chunk,
            metadata={"mime_type": _MIME_SHEETS, "drive_size": result.get("drive_size")},
        )
        all_chunks.extend(chunks)
    return all_chunks
