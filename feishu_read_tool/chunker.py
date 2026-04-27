# feishu_read_tool/chunker.py  — v2
# Heading-first pre-chunking + semantic-unit bin-packing
#
# Strategy:
#   Step 0: Determine split_level (highest heading present: H1 → H2 → None)
#   Step 1: Pre-chunk by split_level headings
#   Step 2: Inside each pre-chunk — render blocks to formatted segments,
#           accumulate list runs, then bin-pack into Chunks
#
# Non-splittable units (code / list / quote / callout / todo) are NEVER
# cut mid-way.  Only plain text (type-2) can be split across chunks.

import logging
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

from .pipeline_models import Chunk, ChunkContentType, PipelineConfig

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────

HEADING_TYPES = set(range(3, 12))       # type 3..11 = H1..H9
TEXT_TYPES = {2, 12, 13, 14, 15, 17, 22}
TABLE_TYPES = {30, 31, 32}

# block_type → rich-text attribute name
_TYPE_TO_ATTR: Dict[int, str] = {
    2: "text", 3: "heading1", 4: "heading2", 5: "heading3",
    6: "heading4", 7: "heading5", 8: "heading6", 9: "heading7",
    10: "heading8", 11: "heading9", 12: "bullet", 13: "ordered",
    14: "quote", 15: "code", 17: "todo",
}

# Feishu code language int → short name (subset of 75)
_CODE_LANG_MAP: Dict[int, str] = {
    1: "plaintext", 7: "bash", 8: "csharp", 9: "cpp", 10: "c",
    12: "css", 15: "dart", 18: "dockerfile", 22: "go", 24: "html",
    28: "json", 29: "java", 30: "javascript", 32: "kotlin", 33: "latex",
    36: "lua", 37: "matlab", 38: "makefile", 39: "markdown", 43: "php",
    44: "perl", 46: "powershell", 49: "python", 50: "r", 52: "ruby",
    53: "rust", 56: "sql", 57: "scala", 60: "shell", 61: "swift",
    63: "typescript", 66: "xml", 67: "yaml", 75: "toml",
}


# ── Token estimation ────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Mixed CJK/English conservative estimate."""
    return max(1, len(text) // 2)


# ── HeadingStack (backward compatible) ──────────────────────────

class HeadingStack:
    """Tracks current heading path while traversing blocks."""
    def __init__(self):
        self._stack: List[Tuple[int, str]] = []

    def push(self, block_type: int, text: str) -> None:
        level = block_type - 2
        self._stack = [(l, t) for l, t in self._stack if l < level]
        self._stack.append((level, text))

    def current_path(self) -> str:
        return " > ".join(t for _, t in self._stack)

    def reset(self) -> None:
        self._stack.clear()


# ── Segment: minimal render unit ────────────────────────────────

@dataclass
class _Segment:
    """A formatted text segment produced from one or more blocks."""
    content: str
    token_count: int
    splittable: bool       # True → plain text, can be split; False → atomic
    block_ids: List[str]


# ── Raw text extraction ─────────────────────────────────────────

def _extract_block_text(block: Dict) -> str:
    """Extract plain text from a block's elements (no formatting)."""
    block_type = block.get("block_type", 0)
    attr = _TYPE_TO_ATTR.get(block_type, "text")
    obj = block.get(attr) or block.get("text")
    if not obj:
        return ""

    if isinstance(obj, dict):
        elements = obj.get("elements", [])
    else:
        elements = getattr(obj, "elements", []) or []

    parts: List[str] = []
    for el in elements:
        if isinstance(el, dict):
            tr = el.get("text_run")
            if tr:
                # Skip strikethrough text: semantically deleted, not useful for RAG
                style = tr.get("text_element_style") if isinstance(tr, dict) else getattr(tr, "text_element_style", None)
                is_strike = False
                if isinstance(style, dict):
                    is_strike = bool(style.get("strikethrough"))
                elif style is not None:
                    is_strike = bool(getattr(style, "strikethrough", False))
                if not is_strike:
                    parts.append(tr.get("content", "") if isinstance(tr, dict)
                                 else getattr(tr, "content", ""))

            # Inline equation: KaTeX content → $formula$
            eq = el.get("equation")
            if eq:
                eq_content = eq.get("content", "") if isinstance(eq, dict) else getattr(eq, "content", "")
                if eq_content.strip():
                    parts.append(f"${eq_content}$")

            md = el.get("mention_doc")
            if md:
                title = md.get("title", "") if isinstance(md, dict) else getattr(md, "title", "")
                url   = md.get("url",   "") if isinstance(md, dict) else getattr(md, "url",   "")
                if title or url:
                    parts.append(f"{title}({url})")
        else:
            tr = getattr(el, "text_run", None)
            if tr:
                style = getattr(tr, "text_element_style", None)
                is_strike = bool(getattr(style, "strikethrough", False)) if style is not None else False
                if not is_strike:
                    parts.append(getattr(tr, "content", ""))

            eq = getattr(el, "equation", None)
            if eq:
                eq_content = getattr(eq, "content", "")
                if eq_content and eq_content.strip():
                    parts.append(f"${eq_content}$")

            md = getattr(el, "mention_doc", None)
            if md:
                title = getattr(md, "title", "") or ""
                url   = getattr(md, "url",   "") or ""
                if title or url:
                    parts.append(f"{title}({url})")
    return "".join(parts)


# ── Formatted rendering per block type ──────────────────────────

def _render_heading(block: Dict) -> str:
    """type 3-11 → # / ## / ### ..."""
    level = block.get("block_type", 3) - 2  # H1=1, H2=2, ...
    text = _extract_block_text(block)
    return "#" * level + " " + text if text.strip() else ""


def _render_code(block: Dict) -> str:
    """type-15 → [code lang=X]:{content}"""
    text = _extract_block_text(block)
    lang_int = block.get("code_language", 1)
    lang_name = _CODE_LANG_MAP.get(lang_int, "plaintext")
    return f"[code lang={lang_name}]:{{{text}}}" if text.strip() else ""


def _render_quote(block: Dict) -> str:
    """type-14 → [引用]:{content}"""
    text = _extract_block_text(block)
    return f"[引用]:{{{text}}}" if text.strip() else ""


def _render_todo(block: Dict) -> str:
    """type-17 → -todo[] / -todo[x]"""
    text = _extract_block_text(block)
    done = block.get("todo_done", False)
    mark = "x" if done else ""
    return f"-todo[{mark}] {text}" if text.strip() else ""


def _render_callout_children(block: Dict, blocks_dict: Dict[str, Dict]) -> Tuple[str, List[str]]:
    """type-19 → [callout]:{aggregated children text}. Returns (content, consumed_ids)."""
    child_parts: List[str] = []
    consumed_ids: List[str] = []
    for cid in block.get("children", []):
        child = blocks_dict.get(cid)
        if not child:
            continue
        t = _extract_block_text(child)
        if t.strip():
            child_parts.append(t)
        consumed_ids.append(cid)
    if not child_parts:
        return "", consumed_ids
    return f"[callout]:{{{' '.join(child_parts)}}}", consumed_ids


# ── List run accumulator helpers ────────────────────────────────

def _flush_bullets(items: List[str], block_ids: List[str]) -> Optional[_Segment]:
    """Flush accumulated bullet items → list{- A; - B; ...}"""
    if not items:
        return None
    content = "list{" + "; ".join(f"- {item}" for item in items) + "}"
    return _Segment(content=content, token_count=estimate_tokens(content),
                    splittable=False, block_ids=list(block_ids))


def _flush_ordered(items: List[Tuple[int, str]], block_ids: List[str]) -> Optional[_Segment]:
    """Flush accumulated ordered items → seq{1. A; 2. B; ...}"""
    if not items:
        return None
    content = "seq{" + "; ".join(f"{i}. {text}" for i, text in items) + "}"
    return _Segment(content=content, token_count=estimate_tokens(content),
                    splittable=False, block_ids=list(block_ids))


def _flush_todos(items: List[str], block_ids: List[str]) -> Optional[_Segment]:
    """Flush accumulated todo items as a group."""
    if not items:
        return None
    content = "; ".join(items)
    return _Segment(content=content, token_count=estimate_tokens(content),
                    splittable=False, block_ids=list(block_ids))


# ── Step 0: Determine split level ───────────────────────────────

def _determine_split_level(blocks: List[Dict]) -> Optional[int]:
    """Find the highest heading type present (prefer H1=3, then H2=4).
    Returns block_type int or None if no headings."""
    has_h1 = any(b.get("block_type") == 3 for b in blocks)
    if has_h1:
        return 3
    has_h2 = any(b.get("block_type") == 4 for b in blocks)
    if has_h2:
        return 4
    return None


# ── Step 1: Pre-chunk by top-level heading ──────────────────────

@dataclass
class _PreChunk:
    heading_path: str
    blocks: List[Dict]


def _pre_chunk_by_heading(
    blocks: List[Dict],
    split_level: Optional[int],
    heading_stack: HeadingStack,
    table_block_ids: set,
) -> List[_PreChunk]:
    """Split flat block list into pre-chunks at split_level heading boundaries.
    Blocks consumed by pass-1 (tables/sheets/boards/bitables) are excluded."""

    # Pre-filter: remove pass-1 consumed blocks and page root
    filtered: List[Dict] = []
    for b in blocks:
        bt = b.get("block_type")
        bid = b.get("block_id", "")
        if bt == 1 or bt in TABLE_TYPES or bid in table_block_ids:
            continue
        filtered.append(b)

    if split_level is None:
        # No headings → entire doc is one pre-chunk
        return [_PreChunk(heading_path="", blocks=filtered)]

    pre_chunks: List[_PreChunk] = []
    current_blocks: List[Dict] = []
    current_path = ""

    for block in filtered:
        bt = block.get("block_type")
        if bt == split_level:
            # Flush current pre-chunk
            if current_blocks:
                pre_chunks.append(_PreChunk(heading_path=current_path, blocks=current_blocks))
                current_blocks = []
            text = _extract_block_text(block)
            heading_stack.push(bt, text)
            current_path = heading_stack.current_path()
            # The heading block itself goes into the new pre-chunk
            current_blocks.append(block)
        else:
            # Update heading_stack for sub-headings (tracking, not splitting)
            if bt in HEADING_TYPES:
                text = _extract_block_text(block)
                heading_stack.push(bt, text)
                current_path = heading_stack.current_path()
            current_blocks.append(block)

    if current_blocks:
        pre_chunks.append(_PreChunk(heading_path=current_path, blocks=current_blocks))

    return pre_chunks


# ── Step 2: Render blocks → Segments ────────────────────────────

def _render_pre_chunk_to_segments(
    pre_chunk: _PreChunk,
    blocks_dict: Dict[str, Dict],
    callout_child_ids: set,
) -> List[_Segment]:
    """Convert a pre-chunk's blocks into a list of formatted Segments.
    Handles list-run accumulation and callout children consumption."""

    segments: List[_Segment] = []

    # List run accumulators
    bullet_items: List[str] = []
    bullet_bids: List[str] = []
    ordered_items: List[Tuple[int, str]] = []
    ordered_seq: int = 0
    ordered_bids: List[str] = []
    todo_items: List[str] = []
    todo_bids: List[str] = []

    def flush_all_lists():
        nonlocal bullet_items, bullet_bids, ordered_items, ordered_seq, ordered_bids
        nonlocal todo_items, todo_bids
        seg = _flush_bullets(bullet_items, bullet_bids)
        if seg:
            segments.append(seg)
        bullet_items, bullet_bids = [], []

        seg = _flush_ordered(ordered_items, ordered_bids)
        if seg:
            segments.append(seg)
        ordered_items, ordered_seq, ordered_bids = [], 0, []

        seg = _flush_todos(todo_items, todo_bids)
        if seg:
            segments.append(seg)
        todo_items, todo_bids = [], []

    for block in pre_chunk.blocks:
        bt = block.get("block_type")
        bid = block.get("block_id", "")

        # Skip callout children (already consumed by callout handler)
        if bid in callout_child_ids:
            continue

        # ── type-12 bullet ──
        if bt == 12:
            # flush non-bullet lists first
            if ordered_items:
                seg = _flush_ordered(ordered_items, ordered_bids)
                if seg: segments.append(seg)
                ordered_items, ordered_seq, ordered_bids = [], 0, []
            if todo_items:
                seg = _flush_todos(todo_items, todo_bids)
                if seg: segments.append(seg)
                todo_items, todo_bids = [], []
            text = _extract_block_text(block)
            if text.strip():
                bullet_items.append(text.strip())
                bullet_bids.append(bid)
            continue

        # ── type-13 ordered ──
        if bt == 13:
            if bullet_items:
                seg = _flush_bullets(bullet_items, bullet_bids)
                if seg: segments.append(seg)
                bullet_items, bullet_bids = [], []
            if todo_items:
                seg = _flush_todos(todo_items, todo_bids)
                if seg: segments.append(seg)
                todo_items, todo_bids = [], []
            text = _extract_block_text(block)
            if text.strip():
                ordered_seq += 1
                ordered_items.append((ordered_seq, text.strip()))
                ordered_bids.append(bid)
            continue

        # ── type-17 todo ──
        if bt == 17:
            if bullet_items:
                seg = _flush_bullets(bullet_items, bullet_bids)
                if seg: segments.append(seg)
                bullet_items, bullet_bids = [], []
            if ordered_items:
                seg = _flush_ordered(ordered_items, ordered_bids)
                if seg: segments.append(seg)
                ordered_items, ordered_seq, ordered_bids = [], 0, []
            text = _render_todo(block)
            if text.strip():
                todo_items.append(text)
                todo_bids.append(bid)
            continue

        # ── Any other type: flush pending lists first ──
        flush_all_lists()

        if bt in HEADING_TYPES:
            # Sub-headings inside pre-chunk → render as markdown heading
            rendered = _render_heading(block)
            if rendered:
                segments.append(_Segment(
                    content=rendered, token_count=estimate_tokens(rendered),
                    splittable=False, block_ids=[bid],
                ))

        elif bt == 15:  # code
            rendered = _render_code(block)
            if rendered:
                segments.append(_Segment(
                    content=rendered, token_count=estimate_tokens(rendered),
                    splittable=False, block_ids=[bid],
                ))

        elif bt == 14:  # quote
            rendered = _render_quote(block)
            if rendered:
                segments.append(_Segment(
                    content=rendered, token_count=estimate_tokens(rendered),
                    splittable=False, block_ids=[bid],
                ))

        elif bt == 19:  # callout
            content, consumed = _render_callout_children(block, blocks_dict)
            for cid in consumed:
                callout_child_ids.add(cid)
            if content:
                segments.append(_Segment(
                    content=content, token_count=estimate_tokens(content),
                    splittable=False, block_ids=[bid],
                ))

        elif bt == 22:  # divider — skip (no semantic value for RAG)
            pass

        elif bt == 2:  # plain text
            text = _extract_block_text(block)
            if text.strip():
                segments.append(_Segment(
                    content=text.strip(), token_count=estimate_tokens(text),
                    splittable=True, block_ids=[bid],
                ))

        # else: unknown type → silently skip

    # Flush remaining list accumulators
    flush_all_lists()

    return segments


# ── Step 3: Bin-pack segments into Chunks ────────────────────────

def _bin_pack_segments(
    segments: List[_Segment],
    doc_id: str,
    doc_title: str,
    doc_url: str,
    heading_path: str,
    chunk_counter: List[int],
    max_chunk_tokens: int,
) -> List[Chunk]:
    """Greedily pack segments into Chunks respecting max_chunk_tokens.

    Rules:
      - Non-splittable segments are never cut.
      - Plain-text splittable segments can fill remaining budget then overflow.
      - Heading changes within a pre-chunk DON'T force a new chunk (they are
        sub-headings; the pre-chunk heading_path is the chunk's heading).
    """
    if not segments:
        return []

    chunks: List[Chunk] = []
    current_parts: List[str] = []
    current_bids: List[str] = []
    current_tokens: int = 0

    def flush_chunk():
        nonlocal current_parts, current_bids, current_tokens
        if not current_parts:
            return
        body = "\n\n".join(current_parts)
        # Embed heading_path into content for RAG readability
        if heading_path:
            content = f"## {heading_path}\n\n{body}"
        else:
            content = body
        chunk_counter[0] += 1
        chunks.append(Chunk(
            chunk_id=f"{doc_id}_{chunk_counter[0]}",
            doc_id=doc_id,
            doc_title=doc_title,
            doc_url=doc_url,
            heading_path=heading_path,
            content_type=ChunkContentType.TEXT,
            content=content,
            block_ids=list(current_bids),
            token_count=estimate_tokens(content),
            has_table=False,
        ))
        current_parts, current_bids, current_tokens = [], [], 0

    for seg in segments:
        # Case 1: fits in current chunk
        if current_tokens + seg.token_count <= max_chunk_tokens:
            current_parts.append(seg.content)
            current_bids.extend(seg.block_ids)
            current_tokens += seg.token_count
            continue

        # Case 2: doesn't fit + non-splittable → flush current, put seg alone
        if not seg.splittable:
            flush_chunk()
            if seg.token_count <= max_chunk_tokens:
                current_parts.append(seg.content)
                current_bids.extend(seg.block_ids)
                current_tokens = seg.token_count
            else:
                # Oversized atomic segment → emit as its own chunk (truncate if extreme)
                chunk_counter[0] += 1
                chunks.append(Chunk(
                    chunk_id=f"{doc_id}_{chunk_counter[0]}",
                    doc_id=doc_id,
                    doc_title=doc_title,
                    doc_url=doc_url,
                    heading_path=heading_path,
                    content_type=ChunkContentType.TEXT,
                    content=seg.content,
                    block_ids=list(seg.block_ids),
                    token_count=seg.token_count,
                    has_table=False,
                ))
            continue

        # Case 3: doesn't fit + splittable (plain text)
        # Fill remaining budget, then overflow to new chunks
        remaining_budget = max_chunk_tokens - current_tokens
        text = seg.content
        bid = seg.block_ids

        # Split at sentence/paragraph boundaries
        pieces = _split_text_by_budget(text, remaining_budget, max_chunk_tokens)
        for i, piece in enumerate(pieces):
            if i == 0 and current_parts:
                # First piece fills current chunk
                current_parts.append(piece)
                current_bids.extend(bid)
                current_tokens += estimate_tokens(piece)
                flush_chunk()
            else:
                # Subsequent pieces → new chunk
                if current_parts:
                    flush_chunk()
                current_parts.append(piece)
                current_bids.extend(bid)
                current_tokens = estimate_tokens(piece)

    flush_chunk()
    return chunks


def _split_text_by_budget(text: str, first_budget: int, full_budget: int) -> List[str]:
    """Split a long text into pieces: first piece ≤ first_budget tokens,
    subsequent pieces ≤ full_budget tokens. Prefers sentence boundaries."""
    if estimate_tokens(text) <= first_budget:
        return [text]

    # Split into sentences (Chinese period / English period / newline)
    import re
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
            budget = full_budget  # subsequent pieces get full budget
        current.append(sent)
        current_tok += sent_tok

    if current:
        pieces.append("".join(current))

    return pieces if pieces else [text]


# ── Main entry (backward compatible signature) ──────────────────

def build_text_chunks(
    blocks: List[Dict],
    doc_id: str,
    doc_title: str,
    doc_url: str,
    heading_stack: HeadingStack,
    chunk_counter: List[int],
    table_block_ids: set,
    cfg: PipelineConfig,
) -> List[Chunk]:
    """Stage 4a: Convert text blocks to Chunks using heading-first pre-chunking.

    Pipeline:
      Step 0: Determine split_level (H1 or H2 or None)
      Step 1: Pre-chunk by split_level heading boundaries
      Step 2: Render each pre-chunk's blocks → formatted Segments
      Step 3: Bin-pack Segments into Chunks (max_chunk_tokens budget)

    Backward compatible with docx_pipeline.py callers.
    """
    max_tokens = cfg.max_chunk_tokens

    # Build block lookup dict
    blocks_dict: Dict[str, Dict] = {
        b.get("block_id"): b for b in blocks if b.get("block_id")
    }

    # Collect all callout child IDs upfront to prevent double-processing
    callout_child_ids: set = set()
    for b in blocks:
        if b.get("block_type") == 19:
            for cid in b.get("children", []):
                callout_child_ids.add(cid)

    # Step 0: determine split level
    split_level = _determine_split_level(blocks)
    logger.debug("chunker v2: split_level=%s for doc %s", split_level, doc_id)

    # Step 1: pre-chunk by heading
    pre_chunks = _pre_chunk_by_heading(blocks, split_level, heading_stack, table_block_ids)
    logger.debug("chunker v2: %d pre-chunks for doc %s", len(pre_chunks), doc_id)

    # Step 2 + 3: render segments and bin-pack per pre-chunk
    all_chunks: List[Chunk] = []
    for pc in pre_chunks:
        segments = _render_pre_chunk_to_segments(pc, blocks_dict, callout_child_ids)
        chunks = _bin_pack_segments(
            segments=segments,
            doc_id=doc_id,
            doc_title=doc_title,
            doc_url=doc_url,
            heading_path=pc.heading_path,
            chunk_counter=chunk_counter,
            max_chunk_tokens=max_tokens,
        )
        all_chunks.extend(chunks)

    # Post-pass: merge tiny leading chunk (heading="", before first heading)
    # into the next chunk if it's under min_chunk_tokens.
    if (len(all_chunks) >= 2
            and all_chunks[0].heading_path == ""
            and all_chunks[0].token_count < cfg.min_chunk_tokens):
        lead = all_chunks[0]
        nxt = all_chunks[1]
        merged_content = lead.content + "\n\n" + nxt.content
        if estimate_tokens(merged_content) <= max_tokens * 1.2:  # allow 20% overflow
            nxt.content = merged_content
            nxt.token_count = estimate_tokens(merged_content)
            nxt.block_ids = lead.block_ids + nxt.block_ids
            all_chunks = all_chunks[1:]
            logger.debug("chunker v2: merged tiny leading chunk into chunk[0]")

    logger.info("chunker v2: %d chunks from %d pre-chunks (doc %s)",
                len(all_chunks), len(pre_chunks), doc_id)
    return all_chunks

# ── Post-merge: combine adjacent small chunks ────────────────────

def _parent_heading(heading_path: str) -> str:
    """Extract parent heading (second-to-last segment) for merge-key comparison.

    Examples:
      "五 > 5.1 > 5.1.1 整体框架" → "五 > 5.1"
      "一 > 1.0 Bench mark"        → "一"
      "附录："                       → ""
      ""                            → ""
    """
    parts = [p.strip() for p in heading_path.split(">") if p.strip()]
    if len(parts) <= 1:
        return ""
    return " > ".join(parts[:-1])


def _strip_heading_prefix(content: str) -> str:
    """Remove the leading `## heading_path\\n\\n` from chunk content if present."""
    if content.startswith("## "):
        # Find end of the heading line
        nl = content.find("\n")
        if nl >= 0:
            rest = content[nl:].lstrip("\n")
            return rest
    return content


def merge_adjacent_chunks(
    chunks: List[Chunk],
    max_chunk_tokens: int = 800,
) -> List[Chunk]:
    """Post-merge pass: combine adjacent chunks that share the same parent heading
    and fit within the token budget.

    Rules:
      1. Heading inheritance: chunks without heading_path inherit from the
         nearest preceding chunk that has one.
      2. Adjacent chunks merge if:
         - _parent_heading() is identical (second-to-last heading segment)
         - Combined token count ≤ max_chunk_tokens
      3. Merged content: keep a single heading prefix, concatenate bodies.
      4. content_type: if any constituent has TABLE_MD/TABLE_JSON → TABLE_MD.
    """
    if not chunks:
        return chunks

    # ── Step 1: heading inheritance ──────────────────────────────
    last_hp = ""
    for c in chunks:
        if c.heading_path:
            last_hp = c.heading_path
        elif last_hp:
            c.heading_path = last_hp
            # Also inject heading into content if missing
            if not c.content.startswith("## "):
                c.content = f"## {last_hp}\n\n{c.content}"
                c.token_count = estimate_tokens(c.content)

    # ── Step 2: adjacent merge ───────────────────────────────────
    merged: List[Chunk] = [chunks[0]]

    for c in chunks[1:]:
        prev = merged[-1]
        prev_parent = _parent_heading(prev.heading_path)
        curr_parent = _parent_heading(c.heading_path)

        can_merge = (
            prev_parent == curr_parent
            and prev.token_count + c.token_count <= max_chunk_tokens
        )

        if can_merge:
            # Strip heading prefix from the second chunk to avoid duplication
            body_b = _strip_heading_prefix(c.content)
            prev.content = prev.content + "\n\n" + body_b
            prev.token_count = estimate_tokens(prev.content)
            prev.block_ids = prev.block_ids + c.block_ids
            # Promote content_type if table is involved
            if c.content_type in (ChunkContentType.TABLE_MD, ChunkContentType.TABLE_JSON):
                prev.content_type = ChunkContentType.TABLE_MD
                prev.has_table = True
        else:
            merged.append(c)

    if len(merged) < len(chunks):
        logger.info("post-merge: %d → %d chunks (merged %d)",
                     len(chunks), len(merged), len(chunks) - len(merged))

    return merged