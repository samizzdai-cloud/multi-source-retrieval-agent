"""
mcp_fetch_doc.py — 飞书 MCP fetch-doc 工具（测试用）

通过 MCP fetch-doc tool 获取飞书文档的完整 Markdown 内容。
与 Block API (list_document_blocks) 的区别：
  - fetch-doc 返回完整 Markdown 文本（一次调用）
  - Block API 返回结构化 block 列表（用于 RAG chunking）
  - fetch-doc 无并发频率限制（实测 30 并发全部 200）
  - Block API 有严格 5/s 限制

MCP tool schema（来自 tools/list）：
  {
    "name": "fetch-doc",
    "inputSchema": {
      "required": ["doc_id"],
      "properties": {
        "doc_id":  {"type": "string",  "description": "文档 ID 或文档 URL（支持自动解析），不能传文章标题"},
        "limit":   {"type": "integer", "description": "返回的最大字符数（可选，默认不限制，返回完整文档）。仅在用户明确要求分页时使用。"},
        "offset":  {"type": "integer", "description": "字符偏移量（可选，默认0）。用于大文档分页获取。"}
      }
    }
  }

响应结构（content[0].text 是 JSON，不是纯文本）：
  成功: {
    "doc_id":   str,   # 文档 ID
    "length":   int,   # 文档完整字符数（含未返回部分）
    "log_id":   str,   # 飞书日志 ID
    "markdown": str    # 实际返回的 Markdown 内容
  }
  失败: {"error": "[VALIDATION:XXXX] 错误描述"}

  ⚠️  重要：不传 limit 时 MCP 默认只返回部分内容（约 10k chars），
      非完整文档。获取全文需传 limit 或使用 fetch_doc_paginated()。

  total_length 字段（来自响应的 length）表示文档完整长度，
  可用于判断是否需要翻页。

并发性能（实测）：
  单次: 98-block 文档 ~500ms / 675-block ~940ms / 1089-block ~1220ms
  30 并发: 全部 200，wall time ~2800ms（短文档）
  无频率限制（不同于 Block API 的 5/s 硬限制）

使用场景：
  - 快速获取文档全文（无需关心 block 结构）
  - 需要绕开 Block API 5/s 限制时
  - 大批量文档并发获取时
"""

import json
import os
from typing import List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

FEISHU_MCP_ENDPOINT = os.getenv("FEISHU_MCP_ENDPOINT", "https://mcp.feishu.cn/mcp")
FEISHU_UAT = os.getenv("FEISHU_UAT", "")


def fetch_doc_markdown(
    doc_id: str,
    uat: str = "",
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    timeout: int = 60,
) -> dict:
    """通过 MCP fetch-doc 获取飞书文档的 Markdown 内容。

    ── INPUT SCHEMA ────────────────────────────────────────────────────────────
    必填：
      doc_id (str): 文档 ID 或文档完整 URL（MCP 支持自动解析）。
                    例：'WPrSdlm7LokNMTx0wBfcBiZnn0a'
                    或：'https://<tenant>.feishu.cn/<redacted>'
                    注意：不能传文章标题。

    选填：
      uat (str):    用户访问令牌，不传时 fallback 到 env FEISHU_UAT。
      limit (int):  返回的最大字符数。默认不传（返回完整文档）。
                    仅在用户明确要求分页时设置。
      offset (int): 字符偏移量，默认 0。配合 limit 实现大文档分页。

    ── OUTPUT SCHEMA ───────────────────────────────────────────────────────────
    成功时返回：
    {
        "status": "success",
        "content": str,     # 文档 Markdown 正文
        "char_count": int,  # 实际返回字符数
        "has_more": bool,   # 是否还有更多内容（offset+limit < total）
        "doc_id": str       # 请求的 doc_id（原样返回，方便调用方记录）
    }

    失败时返回：
    {
        "status": "error",
        "message": str,     # 错误描述
        "doc_id": str
    }
    ────────────────────────────────────────────────────────────────────────────

    Raises:
        无 — 所有错误以 {"status": "error"} 返回，不抛异常。
    """
    token = uat or FEISHU_UAT
    if not token:
        return {"status": "error", "message": "未配置飞书 UAT (FEISHU_UAT)", "doc_id": doc_id}

    headers = {
        "Content-Type": "application/json",
        "X-Lark-MCP-UAT": token,
        "X-Lark-MCP-Allowed-Tools": "fetch-doc",
    }

    arguments: dict = {"doc_id": doc_id}
    if limit is not None:
        arguments["limit"] = limit
    if offset is not None:
        arguments["offset"] = offset

    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "id": 1,
        "params": {
            "name": "fetch-doc",
            "arguments": arguments,
        },
    }

    try:
        response = requests.post(FEISHU_MCP_ENDPOINT, headers=headers, json=payload, timeout=timeout)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        return {"status": "error", "message": f"MCP 请求失败: {e}", "doc_id": doc_id}

    try:
        rjson = response.json()
    except ValueError:
        return {"status": "error", "message": "MCP 响应非 JSON", "doc_id": doc_id}

    # jsonrpc error
    if "error" in rjson:
        return {"status": "error", "message": str(rjson["error"]), "doc_id": doc_id}

    content_list = rjson.get("result", {}).get("content", [])
    if not content_list:
        return {"status": "error", "message": "MCP 响应 content 为空", "doc_id": doc_id}

    text = content_list[0].get("text", "") if content_list else ""

    # MCP fetch-doc 返回的 text 是 JSON，结构：
    #   成功: {"doc_id": str, "length": int, "log_id": str, "markdown": str}
    #   失败: {"error": "[VALIDATION:XXXX] ..."}
    try:
        text_obj = json.loads(text)
    except ValueError:
        # 极端情况：纯文本非 JSON（不应发生，保守处理）
        return {
            "status": "success",
            "content": text,
            "char_count": len(text),
            "has_more": False,
            "doc_id": doc_id,
        }

    if "error" in text_obj:
        return {"status": "error", "message": text_obj["error"], "doc_id": doc_id}

    markdown = text_obj.get("markdown", "")
    total_length = text_obj.get("length", len(markdown))   # 文档总字符数（含未返回部分）
    returned_chars = len(markdown)
    has_more = (limit is not None) and (offset or 0) + returned_chars < total_length

    return {
        "status": "success",
        "content": markdown,
        "char_count": returned_chars,
        "total_length": total_length,   # 文档完整字符数
        "has_more": has_more,
        "log_id": text_obj.get("log_id", ""),
        "doc_id": doc_id,
    }


def fetch_doc_paginated(
    doc_id: str,
    uat: str = "",
    page_chars: int = 50_000,
    max_pages: int = 5,
) -> dict:
    """分页获取超大文档（内置翻页逻辑）。

    Args:
        doc_id:      文档 ID 或 URL
        uat:         用户访问令牌
        page_chars:  每页字符数，默认 50000
        max_pages:   最多翻页次数，默认 5（最多获取 250k 字符）

    Returns:
        {
            "status": "success",
            "content": str,      # 拼接后的完整内容
            "char_count": int,
            "pages_fetched": int,
            "doc_id": str
        }
    """
    parts = []
    offset = 0
    for page_num in range(max_pages):
        result = fetch_doc_markdown(doc_id=doc_id, uat=uat, limit=page_chars, offset=offset)
        if result["status"] != "success":
            if page_num == 0:
                return result   # 第一页就失败 → 直接返回错误
            break               # 后续页失败 → 用已取到的内容
        parts.append(result["content"])
        if not result["has_more"]:
            break
        offset += page_chars

    return {
        "status": "success",
        "content": "".join(parts),
        "char_count": sum(len(p) for p in parts),
        "pages_fetched": len(parts),
        "doc_id": doc_id,
    }


# ══════════════════════════════════════════════════════════════════════════════
# MCP Markdown → Chunks 解析器
# ══════════════════════════════════════════════════════════════════════════════

import re as _re

# 飞书方言标签清洗 regex（编译一次，复用）
_FEISHU_TAG_PATTERNS = [
    _re.compile(r'<text[^>]*>(.*?)</text>', _re.DOTALL),        # <text bgcolor="...">内容</text>
    _re.compile(r'\{[^}]*\}'),                                   # {folded="true"} {align="center"}
    _re.compile(r'<image[^>]*/?>'),                              # <image token="..." />
    _re.compile(r'<!--.*?-->', _re.DOTALL),                      # <!-- Unsupported block type: N -->
    _re.compile(r'<mention-doc[^>]*>(.*?)</mention-doc>'),       # <mention-doc>title</mention-doc>
]
_STRIKETHROUGH_RE = _re.compile(r'~~(.*?)~~')
_SHEET_TOKEN_RE = _re.compile(r'<sheet\s+token="([^"]+)"[^>]*/>')
_CALLOUT_RE = _re.compile(r'<callout[^>]*>\n?(.*?)\n?</callout>', _re.DOTALL)
_LARK_TABLE_RE = _re.compile(r'<lark-table[^>]*>(.*?)</lark-table>', _re.DOTALL)
_LARK_TD_RE = _re.compile(r'<lark-td>(.*?)</lark-td>', _re.DOTALL)
_LARK_TR_RE = _re.compile(r'<lark-tr>(.*?)</lark-tr>', _re.DOTALL)
_ADDONS_RE = _re.compile(r'<add-ons[^>]*record="([^"]*)"[^>]*/>', _re.DOTALL)


def _clean_feishu_tags(text: str) -> str:
    """Strip 飞书方言标签，保留纯文本。"""
    # <text bgcolor="...">内容</text> → 内容
    text = _FEISHU_TAG_PATTERNS[0].sub(r'\1', text)
    # {folded="true"} 等属性注释
    text = _FEISHU_TAG_PATTERNS[1].sub('', text)
    # <image /> → 移除
    text = _FEISHU_TAG_PATTERNS[2].sub('', text)
    # <!-- --> → 移除
    text = _FEISHU_TAG_PATTERNS[3].sub('', text)
    # <mention-doc>title</mention-doc> → title
    text = _FEISHU_TAG_PATTERNS[4].sub(r'\1', text)
    return text.strip()


def _parse_lark_table(table_html: str) -> str:
    """将 <lark-table> 方言解析为标准 Markdown 表格。"""
    rows = _LARK_TR_RE.findall(table_html)
    if not rows:
        return ""

    md_rows = []
    for row_html in rows:
        cells = _LARK_TD_RE.findall(row_html)
        cleaned = [_clean_feishu_tags(c).replace('|', '\\|').replace('\n', ' ').strip()
                    for c in cells]
        md_rows.append(cleaned)

    if not md_rows:
        return ""

    lines = []
    header = md_rows[0]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for row in md_rows[1:]:
        padded = row + [""] * max(0, len(header) - len(row))
        lines.append("| " + " | ".join(padded[:len(header)]) + " |")
    return "\n".join(lines)


def parse_mcp_markdown_to_chunks(
    markdown: str,
    doc_id: str,
    doc_title: str = "",
    doc_url: str = "",
    max_chunk_tokens: int = 800,
) -> List:
    """将 MCP fetch-doc 返回的飞书 Markdown 方言解析为 Chunk 列表。

    处理规则：
      # / ## / ### → heading（pre-chunk 分界 + heading_path）
      - / 1. → 列表聚合 list{} / seq{}
      <callout> → [callout]:{...}
      <lark-table> → Markdown 表格（non-splittable）
      <sheet token="..."> → [sheet embed token=XXX]
      <add-ons record="{mermaid}"> → [流程图] mermaid
      ~~删除线~~ → 跳过
      普通文本 → splittable text

    策略：heading-first pre-chunking + bin-packing（与 chunker.py v2 同逻辑）。

    Returns:
        List[Chunk] — 与 Block API chunker 输出相同的 Chunk 类型。
    """
    from feishu_read_tool.pipeline_models import Chunk, ChunkContentType

    if not markdown or not markdown.strip():
        return []

    def estimate_tokens(text: str) -> int:
        return max(1, len(text) // 2)

    # ── Phase 1: 预处理特殊标签 ─────────────────────────────────────────────

    # 删除线内容移除
    markdown = _STRIKETHROUGH_RE.sub('', markdown)

    # 提取 <add-ons> mermaid 并替换为占位符
    mermaid_blocks = []
    def _replace_addons(m):
        record_str = m.group(1).replace('&quot;', '"').replace('&#34;', '"')
        try:
            import json as _json
            record = _json.loads(record_str)
            data = record.get('data', '')
            if data:
                data = data.replace('\xa0', ' ')
                idx = len(mermaid_blocks)
                mermaid_blocks.append(data)
                return f'__MERMAID_{idx}__'
        except Exception:
            pass
        return ''
    markdown = _ADDONS_RE.sub(_replace_addons, markdown)

    # 提取 <lark-table> 并替换为占位符
    table_blocks = []
    def _replace_table(m):
        md_table = _parse_lark_table(m.group(1))
        if md_table:
            idx = len(table_blocks)
            table_blocks.append(md_table)
            return f'__TABLE_{idx}__'
        return ''
    markdown = _LARK_TABLE_RE.sub(_replace_table, markdown)

    # 提取 <callout> 并替换为占位符
    callout_blocks = []
    def _replace_callout(m):
        content = _clean_feishu_tags(m.group(1)).strip()
        if content:
            idx = len(callout_blocks)
            callout_blocks.append(content)
            return f'__CALLOUT_{idx}__'
        return ''
    markdown = _CALLOUT_RE.sub(_replace_callout, markdown)

    # 提取 <sheet token> 并替换
    def _replace_sheet(m):
        return f'[sheet embed token={m.group(1)}]'
    markdown = _SHEET_TOKEN_RE.sub(_replace_sheet, markdown)

    # 清洗剩余飞书标签
    markdown = _clean_feishu_tags(markdown)

    # ── Phase 2: 按 heading 分组 ────────────────────────────────────────────

    lines = markdown.split('\n')

    # 确定 split_level（最高级 heading）
    h1_exists = any(l.startswith('# ') and not l.startswith('## ') for l in lines)
    h2_exists = any(l.startswith('## ') and not l.startswith('### ') for l in lines)
    split_level = 1 if h1_exists else (2 if h2_exists else None)

    # 按 split_level 分 pre-chunks
    pre_chunks = []  # [(heading_path, [lines])]
    current_heading = ""
    current_lines = []
    heading_stack = []  # [(level, text)]

    for line in lines:
        # 检测 heading
        hm = _re.match(r'^(#{1,9})\s+(.+)', line)
        if hm:
            level = len(hm.group(1))
            heading_text = hm.group(2).strip()
            # 更新 heading stack
            heading_stack = [(l, t) for l, t in heading_stack if l < level]
            heading_stack.append((level, heading_text))
            new_path = " > ".join(t for _, t in heading_stack)

            if split_level and level <= split_level:
                # split boundary
                if current_lines:
                    pre_chunks.append((current_heading, current_lines))
                current_heading = new_path
                current_lines = [line]  # heading 行本身也进去
            else:
                current_lines.append(line)
                current_heading = new_path
        else:
            current_lines.append(line)

    if current_lines:
        pre_chunks.append((current_heading, current_lines))

    # ── Phase 3: 每个 pre-chunk 内渲染 + bin-pack ─────────────────────────────

    all_chunks = []
    chunk_counter = [0]

    for heading_path, pc_lines in pre_chunks:
        segments = []  # [(content, tokens, splittable)]

        # 列表 run 累积器
        bullet_items = []
        ordered_items = []
        ordered_seq = 0

        def flush_bullets():
            nonlocal bullet_items
            if bullet_items:
                content = "list{" + "; ".join(f"- {item}" for item in bullet_items) + "}"
                segments.append((content, estimate_tokens(content), False))
                bullet_items = []

        def flush_ordered():
            nonlocal ordered_items, ordered_seq
            if ordered_items:
                content = "seq{" + "; ".join(f"{i}. {t}" for i, t in ordered_items) + "}"
                segments.append((content, estimate_tokens(content), False))
                ordered_items = []
                ordered_seq = 0

        for line in pc_lines:
            stripped = line.strip()
            if not stripped:
                continue

            # 占位符还原: __MERMAID_0__, __TABLE_0__, __CALLOUT_0__
            placeholder_match = _re.match(r'^__(MERMAID|TABLE|CALLOUT)_(\d+)__$', stripped)
            if placeholder_match:
                flush_bullets(); flush_ordered()
                ph_type = placeholder_match.group(1)
                ph_idx = int(placeholder_match.group(2))
                if ph_type == "MERMAID" and ph_idx < len(mermaid_blocks):
                    content = f"[流程图]\n```mermaid\n{mermaid_blocks[ph_idx]}\n```"
                    segments.append((content, estimate_tokens(content), False))
                elif ph_type == "TABLE" and ph_idx < len(table_blocks):
                    segments.append((table_blocks[ph_idx], estimate_tokens(table_blocks[ph_idx]), False))
                elif ph_type == "CALLOUT" and ph_idx < len(callout_blocks):
                    content = f"[callout]:{{{callout_blocks[ph_idx]}}}"
                    segments.append((content, estimate_tokens(content), False))
                continue

            # heading 行
            if _re.match(r'^#{1,9}\s+', stripped):
                flush_bullets(); flush_ordered()
                segments.append((stripped, estimate_tokens(stripped), False))
                continue

            # bullet list
            if _re.match(r'^[-*]\s+', stripped):
                flush_ordered()
                item = _re.sub(r'^[-*]\s+', '', stripped)
                bullet_items.append(item)
                continue

            # ordered list
            om = _re.match(r'^(\d+)\.\s+', stripped)
            if om:
                flush_bullets()
                ordered_seq += 1
                item = _re.sub(r'^\d+\.\s+', '', stripped)
                ordered_items.append((ordered_seq, item))
                continue

            # 普通文本
            flush_bullets(); flush_ordered()
            segments.append((stripped, estimate_tokens(stripped), True))

        flush_bullets(); flush_ordered()

        # bin-pack segments → chunks
        current_parts = []
        current_tokens = 0

        def flush_chunk():
            nonlocal current_parts, current_tokens
            if not current_parts:
                return
            content = "\n\n".join(current_parts)
            chunk_counter[0] += 1
            all_chunks.append(Chunk(
                chunk_id=f"{doc_id}_{chunk_counter[0]}",
                doc_id=doc_id,
                doc_title=doc_title,
                doc_url=doc_url,
                heading_path=heading_path,
                content_type=ChunkContentType.TEXT,
                content=content,
                block_ids=[],
                token_count=estimate_tokens(content),
                has_table=False,
            ))
            current_parts, current_tokens = [], 0

        for content, tokens, splittable in segments:
            if current_tokens + tokens <= max_chunk_tokens:
                current_parts.append(content)
                current_tokens += tokens
            elif not splittable:
                flush_chunk()
                if tokens <= max_chunk_tokens:
                    current_parts.append(content)
                    current_tokens = tokens
                else:
                    # 超大 non-splittable → 单独成 chunk
                    chunk_counter[0] += 1
                    all_chunks.append(Chunk(
                        chunk_id=f"{doc_id}_{chunk_counter[0]}",
                        doc_id=doc_id, doc_title=doc_title, doc_url=doc_url,
                        heading_path=heading_path, content_type=ChunkContentType.TEXT,
                        content=content, block_ids=[], token_count=tokens, has_table=False,
                    ))
            else:
                # splittable text → fill + overflow
                current_parts.append(content)
                current_tokens += tokens
                if current_tokens > max_chunk_tokens:
                    flush_chunk()

        flush_chunk()

    return all_chunks
