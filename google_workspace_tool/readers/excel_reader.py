"""Excel (.xlsx, .xls, .xlsm) 专用读取器。

通过 Drive API 或 Gmail 下载二进制内容后，用 openpyxl 等工具解析为 Markdown 表格。
"""

import io
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from google_workspace_tool.utils.download_utils import download_file

_MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MIME_XLS = "application/vnd.ms-excel"
_MIME_XLSM = "application/vnd.ms-excel.sheet.macroEnabled.12"


def _try_extract_excel_text(raw_bytes: bytes) -> Optional[str]:
    """尝试用 openpyxl 等库提取 Excel 文本并转为 Markdown 格式。"""
    try:
        import openpyxl # type: ignore
    except ImportError:
        return None
        
    try:
        # 针对 .xlsx, .xlsm
        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True)
        md_lines = []
        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            md_lines.append(f"## Sheet: {sheet_name}\n")
            
            rows = list(sheet.iter_rows(values_only=True))
            if not rows:
                md_lines.append("*(空表 — 无数据)*\n")
                continue
                
            # 过滤掉完全为空的行
            valid_rows = []
            for row in rows:
                if any(cell is not None and str(cell).strip() != "" for cell in row):
                    valid_rows.append(row)
                    
            if not valid_rows:
                md_lines.append("*(空表 — 无数据)*\n")
                continue
                
            max_cols = max(len(row) for row in valid_rows)
            
            # 表头
            first_row = [str(cell).replace('|', '\\|').replace('\n', ' ') if cell is not None else "" for cell in valid_rows[0]]
            first_row += [""] * (max_cols - len(first_row))
            md_lines.append("| " + " | ".join(first_row) + " |")
            md_lines.append("|" + "|".join(["---"] * max_cols) + "|")
            
            # 数据行
            for row in valid_rows[1:]:
                padded_row = [str(cell).replace('|', '\\|').replace('\n', ' ') if cell is not None else "" for cell in row]
                padded_row += [""] * (max_cols - len(padded_row))
                md_lines.append("| " + " | ".join(padded_row) + " |")
                
            md_lines.append("\n")
            
        return "\n".join(md_lines)
    except Exception as e:
        # 如果是 .xls 格式，openpyxl 不支持，会抛出异常
        # 尝试用 pandas/xlrd
        try:
            import pandas as pd # type: ignore
            dfs = pd.read_excel(io.BytesIO(raw_bytes), sheet_name=None, engine='xlrd')
            md_lines = []
            for sheet_name, df in dfs.items():
                md_lines.append(f"## Sheet: {sheet_name}\n")
                if df.empty:
                    md_lines.append("*(空表 — 无数据)*\n")
                else:
                    md_lines.append(df.to_markdown(index=False))
                    md_lines.append("\n")
            return "\n".join(md_lines)
        except ImportError:
            # 说明可能没装 pandas 或 xlrd
            return None
        except Exception:
            return None


def read_one(
    file_id: str,
    creds,
    supports_all_drives: bool = True,
    download_timeout: int = 15,
    known_title: Optional[str] = None,
    known_mime_type: Optional[str] = None,
) -> dict:
    """读取单个 Excel 文件。"""
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)

    try:
        raw_bytes = download_file(drive_service, file_id, supports_all_drives, timeout=download_timeout)
    except HttpError as e:
        return {
            "file_id": file_id,
            "mime_type": known_mime_type or _MIME_XLSX,
            "title": known_title or "",
            "content": None,
            "structured_md": None,
            "content_type": None,
            "raw_bytes": None,
            "error": f"下载失败: {e}",
            "mode": None,
            "drive_size": None,
            "skip_reason": None,
        }

    title = known_title or ""
    mime_type = known_mime_type or _MIME_XLSX
    if not (known_title and known_mime_type):
        try:
            meta = drive_service.files().get(
                fileId=file_id, fields="name,mimeType", supportsAllDrives=supports_all_drives
            ).execute()
            if not title:
                title = meta.get("name", "")
            if not known_mime_type:
                mime_type = meta.get("mimeType", _MIME_XLSX)
        except HttpError:
            pass

    text = _try_extract_excel_text(raw_bytes)
    if text is not None:
        return {
            "file_id": file_id,
            "mime_type": mime_type,
            "title": title,
            "content": text,
            "structured_md": text,
            "content_type": "text", # Actually "table_md" conceptually, but text is fine here
            "raw_bytes": raw_bytes,
            "error": None,
            "mode": "full",
            "drive_size": None,
            "skip_reason": None,
        }
    else:
        return {
            "file_id": file_id,
            "mime_type": mime_type,
            "title": title,
            "content": None,
            "structured_md": None,
            "content_type": "bytes",
            "raw_bytes": raw_bytes,
            "error": "无法解析 Excel 内容（可能由于缺失 openpyxl/xlrd 库，或文件损坏）",
            "mode": None,
            "drive_size": None,
            "skip_reason": None,
        }


def read_batch(
    file_ids: list[str],
    creds,
    supports_all_drives: bool = True,
) -> list[dict]:
    """串行批量读取多个 Excel 文档。"""
    results = []
    for fid in file_ids:
        try:
            results.append(read_one(file_id=fid, creds=creds, supports_all_drives=supports_all_drives))
        except Exception as e:
            results.append({
                "file_id": fid,
                "mime_type": _MIME_XLSX,
                "error": str(e),
                "content": None,
                "structured_md": None,
            })
    return results


def read_concurrent(
    file_ids: list[str],
    creds,
    max_workers: int = 5,
    supports_all_drives: bool = True,
) -> list[dict]:
    """并发批量读取多个 Excel 文档。"""
    def _read(fid):
        try:
            return read_one(file_id=fid, creds=creds, supports_all_drives=supports_all_drives)
        except Exception as e:
            return {
                "file_id": fid,
                "mime_type": _MIME_XLSX,
                "error": str(e),
                "content": None,
                "structured_md": None,
            }

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(_read, file_ids))
    return results


def read_chunks(
    file_id: str,
    creds,
    supports_all_drives: bool = True,
    max_chunk_tokens: int = 800,
    download_timeout: int = 15,
    range_names: Optional[list[str]] = None,
    query: Optional[str] = None,
    enable_excel_filter: bool = False,
    known_title: Optional[str] = None,
) -> list:
    """读取 Excel 文档并切分为 chunks。"""
    from google_workspace_tool.chunkers import GoogleChunk
    from google_workspace_tool.chunkers.md_chunker import estimate_tokens
    from google_workspace_tool.utils.table_utils import split_sections, process_section_to_markdown
    import io

    result = read_one(file_id=file_id, creds=creds, supports_all_drives=supports_all_drives, download_timeout=download_timeout, known_title=known_title)
    raw_bytes = result.get("raw_bytes")
    if result.get("error") or not raw_bytes:
        return []
        
    title = result.get("title", "")
    file_url = f"https://drive.google.com/file/d/{file_id}/view"
    mime_type = result.get("mime_type", _MIME_XLSX)
    drive_size = result.get("drive_size")
    all_chunks = []
    
    try:
        import openpyxl # type: ignore
        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True)
        sheet_names = wb.sheetnames
        
        # 1. 结构级筛选
        # Excel/CSV are selected at the sheet-family level upstream. Once the
        # file is downloaded, process all internal worksheets so subquery-level
        # Downstream preview evaluation receives the actual table chunks.
        selected_sheets = sheet_names
        if range_names:
            selected_sheets = [s for s in sheet_names if s in range_names]
            
        # 2. 读取选中 sheets 并执行行级过滤
        for sheet_name in selected_sheets:
            sheet = wb[sheet_name]
            rows = list(sheet.iter_rows(values_only=True))
            if not rows:
                continue
                
            sections = split_sections(rows)
            for sec_idx, (start_idx, end_idx) in enumerate(sections):
                section_rows = rows[start_idx:end_idx + 1]
                md, is_bm25_filtered, effective_row_count, cov_score = process_section_to_markdown(
                    section_rows=section_rows,
                    query=query,
                    merges=None, # openpyxl 取 merges 比较复杂，暂不处理
                    section_start_global=start_idx,
                )
                
                if not md.strip():
                    continue
                    
                heading = f"Excel: {title} > {sheet_name}"
                
                chunk = GoogleChunk(
                    file_id=file_id,
                    chunk_id=f"google:excel:{file_id}:{sheet_name}:s{sec_idx}",
                    file_title=title,
                    file_url=file_url,
                    content=md,
                    content_type="table_md",
                    heading_path=heading,
                    token_count=estimate_tokens(md),
                    has_table=True,
                    metadata={
                        "mime_type": mime_type,
                        "drive_size": drive_size,
                        "sheet_title": sheet_name,
                        "section_idx": sec_idx,
                        "row_start": start_idx,
                        "row_end": end_idx,
                        "table_rows": effective_row_count,
                        "header_coverage": round(cov_score, 3),
                        "is_bm25_filtered": is_bm25_filtered,
                    }
                )
                all_chunks.append(chunk)
                
    except Exception as e:
        # Fallback to older .xls format using pandas
        try:
            import pandas as pd # type: ignore
            dfs = pd.read_excel(io.BytesIO(raw_bytes), sheet_name=None, engine='xlrd')
            selected_sheets = list(dfs.keys())
            if range_names:
                selected_sheets = [s for s in selected_sheets if s in range_names]
            # pandas 不易实现 BM25 行过滤，直接转 markdown chunk
            from google_workspace_tool.chunkers import markdown_to_chunks
            for sheet_name in selected_sheets:
                df = dfs[sheet_name]
                if df.empty:
                    continue
                md = df.to_markdown(index=False)
                heading = f"Excel: {title} > {sheet_name}"
                md = f"# {heading}\n\n{md}"
                chunks = markdown_to_chunks(
                    markdown=md,
                    file_id=file_id,
                    file_title=title,
                    file_url=file_url,
                    source_tag="google:excel",
                    max_chunk_tokens=max_chunk_tokens,
                    metadata={"mime_type": mime_type, "drive_size": drive_size, "sheet_title": sheet_name},
                )
                all_chunks.extend(chunks)
        except Exception:
            pass
            
    return all_chunks
