"""PDF 专用读取器。

通过 Drive API 下载后，用 PyPDF2 提取文本。
"""

import io
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from utils.download_utils import download_file

_MIME_PDF = "application/pdf"


def _try_extract_pdf_text(raw_bytes: bytes) -> Optional[str]:
    """尝试用 PyPDF2 提取 PDF 文本。"""
    try:
        import PyPDF2  # type: ignore
    except ImportError:
        return None
    try:
        reader = PyPDF2.PdfReader(io.BytesIO(raw_bytes))
        texts = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                texts.append(page_text)
        return "\n".join(texts)
    except Exception:
        return None


def read_one(file_id: str, creds, supports_all_drives: bool = True) -> dict:
    """读取单个 PDF 文件。"""
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)

    try:
        raw_bytes = download_file(drive_service, file_id, supports_all_drives)
    except HttpError as e:
        return {
            "file_id": file_id,
            "mime_type": _MIME_PDF,
            "title": "",
            "content": None,
            "structured_md": None,
            "content_type": None,
            "raw_bytes": None,
            "error": f"下载失败: {e}",
            "mode": None,
            "drive_size": None,
            "skip_reason": None,
        }

    title = ""
    try:
        meta = drive_service.files().get(
            fileId=file_id, fields="name", supportsAllDrives=supports_all_drives
        ).execute()
        title = meta.get("name", "")
    except HttpError:
        pass

    text = _try_extract_pdf_text(raw_bytes)
    if text is not None:
        return {
            "file_id": file_id,
            "mime_type": _MIME_PDF,
            "title": title,
            "content": text,
            "structured_md": text,
            "content_type": "text",
            "raw_bytes": raw_bytes,
            "error": None,
            "mode": "full",
            "drive_size": None,
            "skip_reason": None,
        }
    else:
        return {
            "file_id": file_id,
            "mime_type": _MIME_PDF,
            "title": title,
            "content": None,
            "structured_md": None,
            "content_type": "bytes",
            "raw_bytes": raw_bytes,
            "error": "无法解析 PDF 内容（PyPDF2 未安装或解析失败）",
            "mode": None,
            "drive_size": None,
            "skip_reason": None,
        }


def read_batch(file_ids: list[str], creds, supports_all_drives: bool = True) -> list[dict]:
    """串行批量读取多个 PDF。"""
    results = []
    for fid in file_ids:
        try:
            results.append(read_one(file_id=fid, creds=creds, supports_all_drives=supports_all_drives))
        except Exception as e:
            results.append({
                "file_id": fid,
                "mime_type": _MIME_PDF,
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
    """并发批量读取多个 PDF。"""
    def _read(fid):
        try:
            return read_one(file_id=fid, creds=creds, supports_all_drives=supports_all_drives)
        except Exception as e:
            return {
                "file_id": fid,
                "mime_type": _MIME_PDF,
                "error": str(e),
                "content": None,
                "structured_md": None,
            }

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(_read, file_ids))
    return results
