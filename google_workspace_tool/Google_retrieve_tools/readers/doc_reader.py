"""Google Docs 专用读取器。

提供单文件读取、批量串行读取、批量并发读取三种模式。
输出格式与 Word reader 统一：返回包含 structured_md 和 content 的字典。
"""

from typing import Optional
from concurrent.futures import ThreadPoolExecutor
from googleapiclient.discovery import build

from google_doc_reader.fetcher import BatchDocFetcher
from google_doc_reader.parser import DocParser, OutlineExtractor
from google_doc_reader.markdown_renderer import MarkdownRenderer
from utils.md_utils import strip_md_to_plain

_MIME_DOCS = "application/vnd.google-apps.document"


def _read_one_doc(service, file_id: str, max_tabs: Optional[int] = None) -> dict:
    """内部：通过 Docs API 读取单个文档，返回结构化 Markdown 和纯文本。"""
    fetcher = BatchDocFetcher(docs_service=service, fields=None)
    doc_json = fetcher.fetch_one(file_id)

    parser = DocParser()
    doc_content = parser.parse(doc_json, max_tabs=max_tabs)

    renderer = MarkdownRenderer()
    md = renderer.render(doc_content)
    plain_text = strip_md_to_plain(md)

    return {"text": plain_text, "structured_md": md}


def _read_one_doc_outline(service, file_id: str) -> dict:
    """内部：通过 Docs API 读取文档大纲。"""
    fetcher = BatchDocFetcher(
        docs_service=service,
        fields=BatchDocFetcher.OUTLINE_FIELDS,
    )
    doc_json = fetcher.fetch_one(file_id)

    extractor = OutlineExtractor()
    outline = extractor.extract(doc_json)

    renderer = MarkdownRenderer()
    outline_md = renderer.render_outline(outline)

    return {
        "title": outline.title,
        "outline_md": outline_md,
        "outline": outline,
    }


def read_one(
    file_id: str,
    creds,
    max_tabs: Optional[int] = None,
    auto_outline_threshold: Optional[int] = None,
    drive_size: Optional[int] = None,
    supports_all_drives: bool = True,  # 保留以保持向后兼容，但 Docs API 不支持此参数
) -> dict:
    """读取单个 Google Doc。

    Args:
        file_id: 文档 ID
        creds: 已授权的 Credentials
        max_tabs: 最多保留 N 个 tabs
        auto_outline_threshold: 超过此大小自动降级为 outline 模式（字节）
        drive_size: Drive 文件大小（用于阈值判断，不传则内部查询）
        supports_all_drives: 保留参数（Docs API 不支持 supportsAllDrives，此参数不影响行为）

    Returns:
        统一格式的 dict，包含 structured_md, content, mode 等
    """
    from googleapiclient.discovery import build

    docs_service = build("docs", "v1", credentials=creds, cache_discovery=False)

    # 自动 outline 判断
    use_outline = (
        auto_outline_threshold is not None
        and drive_size is not None
        and drive_size > auto_outline_threshold
    )

    if use_outline:
        outline_result = _read_one_doc_outline(docs_service, file_id)
        return {
            "file_id": file_id,
            "mime_type": _MIME_DOCS,
            "title": outline_result["title"],
            "content": strip_md_to_plain(outline_result["outline_md"]),
            "structured_md": outline_result["outline_md"],
            "content_type": "text",
            "raw_bytes": None,
            "error": None,
            "mode": "outline",
            "drive_size": drive_size,
            "skip_reason": None,
        }
    else:
        doc_result = _read_one_doc(docs_service, file_id, max_tabs=max_tabs)
        return {
            "file_id": file_id,
            "mime_type": _MIME_DOCS,
            "title": "",
            "content": doc_result["text"],
            "structured_md": doc_result["structured_md"],
            "content_type": "text",
            "raw_bytes": None,
            "error": None,
            "mode": "full",
            "drive_size": drive_size,
            "skip_reason": None,
        }


def read_batch(
    file_ids: list[str],
    creds,
    max_tabs: Optional[int] = None,
    auto_outline_threshold: Optional[int] = None,
    supports_all_drives: bool = True,
) -> list[dict]:
    """串行批量读取多个 Google Doc。

    按顺序一个一个读取，适用于需要控制 API 速率的场景。
    """
    results = []
    for fid in file_ids:
        try:
            result = read_one(
                file_id=fid,
                creds=creds,
                max_tabs=max_tabs,
                auto_outline_threshold=auto_outline_threshold,
                supports_all_drives=supports_all_drives,
            )
            results.append(result)
        except Exception as e:
            results.append({
                "file_id": fid,
                "mime_type": _MIME_DOCS,
                "error": str(e),
                "content": None,
                "structured_md": None,
            })
    return results


def read_concurrent(
    file_ids: list[str],
    creds,
    max_tabs: Optional[int] = None,
    auto_outline_threshold: Optional[int] = None,
    max_workers: int = 5,
    supports_all_drives: bool = True,
) -> list[dict]:
    """并发批量读取多个 Google Doc。

    使用 ThreadPoolExecutor 并行调用 Docs API。
    """
    def _read(fid):
        try:
            return read_one(
                file_id=fid,
                creds=creds,
                max_tabs=max_tabs,
                auto_outline_threshold=auto_outline_threshold,
                supports_all_drives=supports_all_drives,
            )
        except Exception as e:
            return {
                "file_id": fid,
                "mime_type": _MIME_DOCS,
                "error": str(e),
                "content": None,
                "structured_md": None,
            }

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(_read, file_ids))
    return results
