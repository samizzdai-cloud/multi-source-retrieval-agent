"""通用 Markdown → Chunk 切分器。

借鉴 feishu_read_tool/basic_tool/mcp_fetch_doc.py 的 parse_mcp_markdown_to_chunks 思路，
去掉飞书方言标签处理，保留 heading-first pre-chunking + 列表聚合 + bin-packing 核心逻辑。

支持的 Markdown 元素：
- Heading (# / ## / ### ...)
- Bullet list (- / *)
- Ordered list (1. / 2.)
- Code block (```)
- Markdown table (| ... |)
- Plain text
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple


class ChunkContentType(Enum):
    TEXT = "text"
    TABLE_MD = "table_md"
    TABLE_JSON = "table_json"


@dataclass
class GoogleChunk:
    """Google Workspace 内部 chunk 表示。"""
    chunk_id: str
    file_id: str
    file_title: str
    file_url: str
    heading_path: str
    content_type: ChunkContentType
    content: str
    token_count: int
    has_table: bool
    metadata: Dict = field(default_factory=dict)


# ── Token estimation ────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Mixed CJK/English conservative estimate."""
    return max(1, len(text) // 2)


# ── Segment: minimal render unit ────────────────────────────────

@dataclass
class _Segment:
    content: str
    token_count: int
    splittable: bool       # True → plain text, can be split; False → atomic


# ── Step 0 + 1: Pre-chunk by heading ────────────────────────────

def _determine_split_level(lines: List[str]) -> Optional[int]:
    """Find the highest heading level present (prefer H1=1, then H2=2)."""
    h1_exists = any(re.match(r'^#\s', l) for l in lines)
    if h1_exists:
        return 1
    h2_exists = any(re.match(r'^##\s', l) for l in lines)
    if h2_exists:
        return 2
    return None


def _pre_chunk_by_heading(lines: List[str], split_level: Optional[int]) -> List[Tuple[str, List[str]]]:
    """Split lines into pre-chunks at split_level heading boundaries.
    Returns list of (heading_path, lines_in_prechunk).
    """
    if split_level is None:
        return [("", lines)]

    pre_chunks: List[Tuple[str, List[str]]] = []
    current_lines: List[str] = []
    current_heading = ""
    heading_stack: List[Tuple[int, str]] = []

    for line in lines:
        stripped = line.strip()
        hm = re.match(r'^(#{1,9})\s+(.+)', stripped)
        if hm:
            level = len(hm.group(1))
            heading_text = hm.group(2).strip()
            # Update heading stack: keep only higher-level headings
            heading_stack = [(l, t) for l, t in heading_stack if l < level]
            heading_stack.append((level, heading_text))
            new_path = " > ".join(t for _, t in heading_stack)

            if level <= split_level:
                # Split boundary
                if current_lines:
                    pre_chunks.append((current_heading, current_lines))
                    current_lines = []
                current_heading = new_path
                current_lines.append(line)
            else:
                current_lines.append(line)
                current_heading = new_path
        else:
            current_lines.append(line)

    if current_lines:
        pre_chunks.append((current_heading, current_lines))

    return pre_chunks


# ── Step 2: Render pre-chunk lines → Segments ───────────────────

def _is_table_line(line: str) -> bool:
    return line.strip().startswith("|")


def _is_code_fence(line: str) -> bool:
    return line.strip().startswith("```")


def _render_pre_chunk_to_segments(pc_lines: List[str]) -> List[_Segment]:
    """Convert pre-chunk lines into formatted Segments."""
    segments: List[_Segment] = []

    # List run accumulators
    bullet_items: List[str] = []
    ordered_items: List[Tuple[int, str]] = []
    ordered_seq: int = 0

    def flush_bullets():
        nonlocal bullet_items
        if bullet_items:
            content = "list{" + "; ".join(item for item in bullet_items) + "}"
            segments.append(_Segment(content, estimate_tokens(content), False))
            bullet_items = []

    def flush_ordered():
        nonlocal ordered_items, ordered_seq
        if ordered_items:
            content = "seq{" + "; ".join(f"{i}. {t}" for i, t in ordered_items) + "}"
            segments.append(_Segment(content, estimate_tokens(content), False))
            ordered_items, ordered_seq = [], 0

    i = 0
    while i < len(pc_lines):
        line = pc_lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # ── Code block ──
        if _is_code_fence(stripped):
            flush_bullets(); flush_ordered()
            code_lines: List[str] = [line]
            i += 1
            while i < len(pc_lines):
                code_lines.append(pc_lines[i])
                if _is_code_fence(pc_lines[i]):
                    i += 1
                    break
                i += 1
            content = "\n".join(code_lines)
            segments.append(_Segment(content, estimate_tokens(content), False))
            continue

        # ── Markdown table ──
        if _is_table_line(stripped):
            flush_bullets(); flush_ordered()
            table_lines: List[str] = [line]
            i += 1
            while i < len(pc_lines) and _is_table_line(pc_lines[i]):
                table_lines.append(pc_lines[i])
                i += 1
            content = "\n".join(table_lines)
            segments.append(_Segment(content, estimate_tokens(content), False))
            continue

        # ── Heading ──
        if re.match(r'^#{1,9}\s+', stripped):
            flush_bullets(); flush_ordered()
            segments.append(_Segment(stripped, estimate_tokens(stripped), False))
            i += 1
            continue

        # ── Bullet list ──
        # 匹配任意前导空格，保留缩进层级
        bm = re.match(r'^(\s*)[-*]\s+', line)
        if bm:
            flush_ordered()
            indent = bm.group(1)
            item = re.sub(r'^\s*[-*]\s+', '', line).strip()
            # 保留前面的缩进空格，并且统一用 `-`
            bullet_items.append(f"{indent}- {item}")
            i += 1
            continue

        # ── Ordered list ──
        om = re.match(r'^(\s*)(\d+)\.\s+', line)
        if om:
            flush_bullets()
            indent = om.group(1)
            seq_num = int(om.group(2))
            
            # 如果是无缩进的 1. 开头，重置序号
            if not indent and seq_num == 1:
                ordered_seq = 1
            else:
                ordered_seq += 1
                
            item = re.sub(r'^\s*\d+\.\s+', '', line).strip()
            # 保留前面的缩进空格
            ordered_items.append((ordered_seq, f"{indent}{item}"))
            i += 1
            continue

        # ── Plain text ──
        flush_bullets(); flush_ordered()
        segments.append(_Segment(stripped, estimate_tokens(stripped), True))
        i += 1

    flush_bullets(); flush_ordered()
    return segments


# ── Step 3: Bin-pack segments into Chunks ───────────────────────

def _split_text_by_budget(text: str, first_budget: int, full_budget: int) -> List[str]:
    """Split a long text into pieces: first piece ≤ first_budget tokens,
    subsequent pieces ≤ full_budget tokens. Prefers sentence boundaries."""
    if estimate_tokens(text) <= first_budget:
        return [text]

    # Split into sentences (Chinese period / English period / newline)
    sentences = re.split(r'(?<=[。！？.!?\n])\s*', text)
    sentences = [s for s in sentences if s.strip()]

    if not sentences:
        return [text]

    pieces: List[str] = []
    current: List[str] = []
    budget = first_budget
    current_tok = 0

    for sent in sentences:
        sent_tok = estimate_tokens(sent)
        if current_tok + sent_tok > budget and current:
            pieces.append("".join(current))
            current = []
            current_tok = 0
            budget = full_budget
        current.append(sent)
        current_tok += sent_tok

    if current:
        pieces.append("".join(current))

    return pieces if pieces else [text]


def _bin_pack_segments(
    segments: List[_Segment],
    heading_path: str,
    chunk_counter: List[int],
    max_chunk_tokens: int,
) -> List[Tuple[str, int, bool]]:
    """Greedily pack segments into chunk contents.
    Returns list of (content, token_count, has_table).
    """
    if not segments:
        return []

    chunks_contents: List[Tuple[str, int, bool]] = []
    current_parts: List[str] = []
    current_tokens: int = 0
    current_has_table: bool = False

    def flush_chunk():
        nonlocal current_parts, current_tokens, current_has_table
        if not current_parts:
            return
        body = "\n\n".join(current_parts)
        
        # 智能补充 heading：如果当前 chunk 正文不以标题开头，且存在 heading_path，则补齐上下文
        if heading_path and not re.match(r'^#{1,9}\s+', current_parts[0].strip()):
            content = f"## {heading_path}\n\n{body}"
        else:
            content = body
            
        chunks_contents.append((content, estimate_tokens(content), current_has_table))
        current_parts, current_tokens, current_has_table = [], 0, False

    for seg in segments:
        # Case 1: fits in current chunk
        if current_tokens + seg.token_count <= max_chunk_tokens:
            current_parts.append(seg.content)
            current_tokens += seg.token_count
            if not seg.splittable and _is_table_line(seg.content):
                current_has_table = True
            continue

        # Case 2: doesn't fit + non-splittable → flush current, put seg alone
        if not seg.splittable:
            flush_chunk()
            
            # Check if this atomic segment is an oversized table
            if _is_table_line(seg.content) and seg.token_count > max_chunk_tokens:
                # Large table splitting logic
                table_lines = seg.content.split('\n')
                if len(table_lines) > 3: # header + separator + at least one data row
                    header = table_lines[0]
                    separator = table_lines[1]
                    data_rows = table_lines[2:]
                    
                    rows_per_batch = max(5, int(max_chunk_tokens / max(1, estimate_tokens(header + separator)) * 0.8)) # heuristic
                    
                    for idx in range(0, len(data_rows), rows_per_batch):
                        batch_rows = data_rows[idx:idx+rows_per_batch]
                        batch_content = "\n".join([header, separator] + batch_rows)
                        
                        # 智能补充 heading
                        if heading_path:
                            final_content = f"## {heading_path}\n\n{batch_content}"
                        else:
                            final_content = batch_content
                            
                        chunks_contents.append((final_content, estimate_tokens(final_content), True))
                else:
                    # Not splittable even though it's a table (e.g. single giant row)
                    chunks_contents.append((seg.content, seg.token_count, True))
            else:
                if seg.token_count <= max_chunk_tokens:
                    current_parts.append(seg.content)
                    current_tokens = seg.token_count
                    if _is_table_line(seg.content):
                        current_has_table = True
                else:
                    # Oversized atomic non-table segment (e.g. giant code block)
                    chunks_contents.append((seg.content, seg.token_count, _is_table_line(seg.content)))
            continue

        # Case 3: doesn't fit + splittable (plain text)
        remaining_budget = max_chunk_tokens - current_tokens
        pieces = _split_text_by_budget(seg.content, remaining_budget, max_chunk_tokens)
        for j, piece in enumerate(pieces):
            if j == 0 and current_parts:
                current_parts.append(piece)
                current_tokens += estimate_tokens(piece)
                flush_chunk()
            else:
                if current_parts:
                    flush_chunk()
                current_parts.append(piece)
                current_tokens = estimate_tokens(piece)

    flush_chunk()
    return chunks_contents


# ── Main entry ──────────────────────────────────────────────────

def markdown_to_chunks(
    markdown: str,
    file_id: str,
    file_title: str,
    file_url: str,
    source_tag: str = "google",
    max_chunk_tokens: int = 800,
    metadata: Optional[Dict] = None,
) -> List[GoogleChunk]:
    """将 Markdown 文本切分为 GoogleChunk 列表。

    Args:
        markdown: 标准 Markdown 文本
        file_id: 文件 ID
        file_title: 文件标题
        file_url: 文件 URL
        source_tag: chunk_id 前缀，如 "google:doc" / "google:word"
        max_chunk_tokens: 每个 chunk 的最大 token 数
        metadata: 附加元数据

    Returns:
        List[GoogleChunk]
    """
    if not markdown or not markdown.strip():
        return []

    lines = markdown.split('\n')
    split_level = _determine_split_level(lines)
    pre_chunks = _pre_chunk_by_heading(lines, split_level)

    all_chunks: List[GoogleChunk] = []
    chunk_counter = [0]
    base_meta = dict(metadata or {})

    for heading_path, pc_lines in pre_chunks:
        segments = _render_pre_chunk_to_segments(pc_lines)
        chunk_contents = _bin_pack_segments(
            segments, heading_path, chunk_counter, max_chunk_tokens
        )
        for content, tok_count, has_table in chunk_contents:
            chunk_counter[0] += 1
            chunk_id = f"{source_tag}:{file_id}:{chunk_counter[0]}"
            all_chunks.append(GoogleChunk(
                chunk_id=chunk_id,
                file_id=file_id,
                file_title=file_title,
                file_url=file_url,
                heading_path=heading_path,
                content_type=ChunkContentType.TABLE_MD if has_table else ChunkContentType.TEXT,
                content=content,
                token_count=tok_count,
                has_table=has_table,
                metadata=dict(base_meta),
            ))

    return all_chunks
