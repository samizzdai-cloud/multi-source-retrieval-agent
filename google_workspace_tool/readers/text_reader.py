"""Text (.txt, .csv) 专用读取器。

通过 Drive API 下载文件后，作为纯文本处理。
"""

from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from google_workspace_tool.utils.download_utils import download_file

_MIME_TXT = "text/plain"
_MIME_CSV = "text/csv"


def _extract_text(raw_bytes: bytes) -> Optional[str]:
    """尝试提取文本。"""
    try:
        return raw_bytes.decode('utf-8', errors='replace')
    except Exception:
        return None


def read_one(
    file_id: str,
    creds,
    supports_all_drives: bool = True,
    download_timeout: int = 15,
) -> dict:
    """读取单个文本文件。"""
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)

    try:
        raw_bytes = download_file(drive_service, file_id, supports_all_drives, timeout=download_timeout)
    except HttpError as e:
        return {
            "file_id": file_id,
            "mime_type": _MIME_TXT,
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
    mime_type = _MIME_TXT
    try:
        meta = drive_service.files().get(
            fileId=file_id, fields="name,mimeType", supportsAllDrives=supports_all_drives
        ).execute()
        title = meta.get("name", "")
        mime_type = meta.get("mimeType", _MIME_TXT)
    except HttpError:
        pass

    text = _extract_text(raw_bytes)
    if text is not None:
        return {
            "file_id": file_id,
            "mime_type": mime_type,
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
            "mime_type": mime_type,
            "title": title,
            "content": None,
            "structured_md": None,
            "content_type": "bytes",
            "raw_bytes": raw_bytes,
            "error": "无法解析文本内容",
            "mode": None,
            "drive_size": None,
            "skip_reason": None,
        }


def read_batch(
    file_ids: list[str],
    creds,
    supports_all_drives: bool = True,
) -> list[dict]:
    """串行批量读取多个文本文件。"""
    results = []
    for fid in file_ids:
        try:
            results.append(read_one(file_id=fid, creds=creds, supports_all_drives=supports_all_drives))
        except Exception as e:
            results.append({
                "file_id": fid,
                "mime_type": _MIME_TXT,
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
    """并发批量读取多个文本文件。"""
    def _read(fid):
        try:
            return read_one(file_id=fid, creds=creds, supports_all_drives=supports_all_drives)
        except Exception as e:
            return {
                "file_id": fid,
                "mime_type": _MIME_TXT,
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
) -> list:
    """读取文本并切分为 chunks。"""
    from google_workspace_tool.chunkers import markdown_to_chunks, GoogleChunk
    result = read_one(file_id=file_id, creds=creds, supports_all_drives=supports_all_drives, download_timeout=download_timeout)
    if result.get("error") or not result.get("structured_md"):
        return []
    title = result.get("title", "")
    file_url = f"https://drive.google.com/file/d/{file_id}/view"
    
    md = result["structured_md"]
        
    chunks = markdown_to_chunks(
        markdown=md,
        file_id=file_id,
        file_title=title,
        file_url=file_url,
        source_tag="google:txt",
        max_chunk_tokens=max_chunk_tokens,
        metadata={"mime_type": result.get("mime_type"), "drive_size": result.get("drive_size")},
    )
    
    # Text files usually don't have markdown headings.
    # The user requested heading_path to be set to the filename WITHOUT modifying the chunk content.
    if title:
        for c in chunks:
            if not c.heading_path:
                c.heading_path = title
                
    return chunks
