"""
feishu_url_tool.py — 飞书 URL 解析与生成工具

支持的文件类型：
  bitable   — 多维表格（/base/）
  doc       — 云文档（/docx/ 或 /doc/）
  sheet     — 电子表格（/sheets/）
  wiki_doc  — 知识库文档（/wiki/，无 ?table=）
  wiki_bitable — 知识库多维表格（/wiki/，有 ?table=）
"""

import re
from typing import Optional
from urllib.parse import urlparse, parse_qs

# ── 内部正则 ──────────────────────────────────────────────────────

_BASE_RE    = re.compile(r'/base/([A-Za-z0-9]+)')
_DOC_RE     = re.compile(r'/docx?/([A-Za-z0-9]+)')
_SHEET_RE   = re.compile(r'/sheets/([A-Za-z0-9]+)')
_WIKI_RE    = re.compile(r'/wiki/([A-Za-z0-9]+)')


# ── 解析函数 ──────────────────────────────────────────────────────

def parse_bitable_url(url: str) -> dict:
    """解析多维表格 URL，提取 bitable_id / table_id / view_id。

    支持形态：
      /base/{bitable_id}
      /wiki/{wiki_token}?table={table_id}&view={view_id}
      /docx/{doc_id}?table=...   （文档内嵌 bitable）
      /sheets/{token}?table=...  （表格内嵌 bitable）

    Returns:
        {"bitable_id": str, "table_id": str|None, "view_id": str|None}

    Raises:
        ValueError: URL 不匹配任何已知 bitable 形态
    """
    qs = parse_qs(urlparse(url).query)
    table_id = qs.get("table", [None])[0]
    view_id  = qs.get("view",  [None])[0]

    for pattern in (_BASE_RE, _WIKI_RE, _DOC_RE, _SHEET_RE):
        m = pattern.search(url)
        if m:
            return {"bitable_id": m.group(1), "table_id": table_id, "view_id": view_id}

    raise ValueError(f"无法从 URL 中解析 bitable_id: {url}")


def parse_doc_url(url: str) -> dict:
    """解析云文档 URL，提取 doc_id 和文档类型。

    支持形态：
      /docx/{doc_id}
      /doc/{doc_id}
      /wiki/{wiki_token}（无 ?table=，视为 wiki 文档）

    Returns:
        {"doc_id": str, "doc_type": "docx"|"doc"|"wiki_doc"}

    Raises:
        ValueError: URL 不匹配任何已知文档形态
    """
    m = _DOC_RE.search(url)
    if m:
        doc_type = "docx" if "/docx/" in url else "doc"
        return {"doc_id": m.group(1), "doc_type": doc_type}

    m = _WIKI_RE.search(url)
    if m:
        return {"doc_id": m.group(1), "doc_type": "wiki_doc"}

    raise ValueError(f"无法从 URL 中解析 doc_id: {url}")


def parse_sheet_url(url: str) -> dict:
    """解析电子表格 URL，提取 spreadsheet_token 和 sheet_id。

    支持形态：
      /sheets/{spreadsheet_token}?sheet={sheet_id}

    Returns:
        {"spreadsheet_token": str, "sheet_id": str|None}

    Raises:
        ValueError: URL 不匹配 sheet 形态
    """
    m = _SHEET_RE.search(url)
    if not m:
        raise ValueError(f"无法从 URL 中解析 spreadsheet_token: {url}")

    qs = parse_qs(urlparse(url).query)
    sheet_id = qs.get("sheet", [None])[0]
    return {"spreadsheet_token": m.group(1), "sheet_id": sheet_id}


def parse_wiki_url(url: str) -> dict:
    """解析知识库 URL，提取 wiki_token（不做 API 解引用）。

    Returns:
        {"wiki_token": str}

    Raises:
        ValueError: URL 不匹配 wiki 形态
    """
    m = _WIKI_RE.search(url)
    if not m:
        raise ValueError(f"无法从 URL 中解析 wiki_token: {url}")
    return {"wiki_token": m.group(1)}


# ── 统一入口 ──────────────────────────────────────────────────────

def parse_feishu_url(url: str) -> dict:
    """自动识别飞书 URL 类型并解析，返回统一结构。

    识别规则：
      /base/                    → bitable
      /sheets/                  → sheet
      /docx/ 或 /doc/           → doc
      /wiki/ + ?table=          → wiki_bitable
      /wiki/ 无 ?table=         → wiki_doc

    Returns:
        {"type": str, "result": dict}

    Raises:
        ValueError: 无法识别的 URL 格式
    """
    if "/base/" in url:
        return {"type": "bitable", "result": parse_bitable_url(url)}

    if "/sheets/" in url:
        return {"type": "sheet", "result": parse_sheet_url(url)}

    if "/docx/" in url or "/doc/" in url:
        return {"type": "doc", "result": parse_doc_url(url)}

    if "/wiki/" in url:
        qs = parse_qs(urlparse(url).query)
        if "table" in qs:
            return {"type": "wiki_bitable", "result": parse_bitable_url(url)}
        return {"type": "wiki_doc", "result": parse_doc_url(url)}

    raise ValueError(f"无法识别的飞书 URL 格式: {url}")


# ── 生成函数 ──────────────────────────────────────────────────────

def build_bitable_url(bitable_id: str, host: str = "open.feishu.cn") -> str:
    """生成多维表格 URL（只到 bitable_id）。

    Returns:
        "https://{host}/base/{bitable_id}"
    """
    return f"https://{host}/base/{bitable_id}"


def build_sheet_url(
    spreadsheet_token: str,
    sheet_id: Optional[str] = None,
    host: str = "open.feishu.cn",
) -> str:
    """生成电子表格 URL。

    Returns:
        "https://{host}/sheets/{token}" 或带 ?sheet={sheet_id}
    """
    url = f"https://{host}/sheets/{spreadsheet_token}"
    if sheet_id:
        url += f"?sheet={sheet_id}"
    return url
