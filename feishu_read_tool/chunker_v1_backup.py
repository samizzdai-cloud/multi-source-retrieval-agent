# tools/feishu/chunker.py
# Stage 4a: text block -> Chunk splitting

import logging
from typing import List, Dict

from .pipeline_models import Chunk, ChunkContentType, PipelineConfig

logger = logging.getLogger(__name__)

# heading block_type range: 3-11 inclusive
HEADING_TYPES = set(range(3, 12))
# text-like block types to include in chunks
# NOTE: type-19 (Callout) is handled separately in build_text_chunks (children aggregation)
TEXT_TYPES = {2, 12, 13, 14, 15, 17, 22}
# table block types (handled by table_handler, skipped here)
TABLE_TYPES = {30, 31, 32}
# Callout prefix added to aggregated children text
_CALLOUT_PREFIX = "[注意] "

# block_type -> attribute name mapping
_TYPE_TO_ATTR: Dict[int, str] = {
    2: "text", 3: "heading1", 4: "heading2", 5: "heading3",
    6: "heading4", 7: "heading5", 8: "heading6", 9: "heading7",
    10: "heading8", 11: "heading9", 12: "bullet", 13: "ordered",
    14: "quote", 15: "code", 17: "todo",
}


# ── Token estimation ────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """
    Mixed CJK/English conservative estimate.
    len(text) // 2 approximates actual token count.
    """
    return max(1, len(text) // 2)


# ── HeadingStack ─────────────────────────────────────────────────

class HeadingStack:
    """
    Tracks the current heading path while traversing document blocks.
    block_type 3=H1, 4=H2, ..., 11=H9  ->  level = block_type - 2
    """
    def __init__(self):
        self._stack: List = []   # [(level, text), ...]

    def push(self, block_type: int, text: str) -> None:
        level = block_type - 2
        self._stack = [(l, t) for l, t in self._stack if l < level]
        self._stack.append((level, text))

    def current_path(self) -> str:
        return " > ".join(t for _, t in self._stack)

    def reset(self) -> None:
        self._stack.clear()


# ── Section ──────────────────────────────────────────────────────

class Section:
    """
    Semantic segment after heading-based splitting, before Chunk conversion.
    One Section may produce one or more Chunks (sliding window when oversized).
    """
    def __init__(self, heading_path: str):
        self.heading_path = heading_path
        self.blocks: List[Dict] = []
        self.text_parts: List[str] = []

    def append(self, block: Dict, text: str) -> None:
        self.blocks.append(block)
        self.text_parts.append(text)

    @property
    def full_text(self) -> str:
        return "\n\n".join(p for p in self.text_parts if p.strip())

    @property
    def token_count(self) -> int:
        return estimate_tokens(self.full_text)

    @property
    def block_ids(self) -> List[str]:
        return [b["block_id"] for b in self.blocks]


# ── Text extraction ──────────────────────────────────────────────

def _extract_block_text(block: Dict) -> str:
    """Extract plain text from a single block's elements."""
    block_type = block.get("block_type", 0)
    attr = _TYPE_TO_ATTR.get(block_type, "text")
    obj = block.get(attr) or block.get("text")
    if not obj:
        return ""

    if isinstance(obj, dict):
        elements = obj.get("elements", [])
    else:
        elements = getattr(obj, "elements", []) or []

    parts = []
    for el in elements:
        if isinstance(el, dict):
            tr = el.get("text_run")
            if tr:
                content = tr.get("content", "") if isinstance(tr, dict) else getattr(tr, "content", "")
                parts.append(content)
            md = el.get("mention_doc")
            if md:
                title = md.get("title", "") if isinstance(md, dict) else getattr(md, "title", "")
                url   = md.get("url",   "") if isinstance(md, dict) else getattr(md, "url",   "")
                if title or url:
                    parts.append(f"{title}({url})")
        else:
            tr = getattr(el, "text_run", None)
            if tr:
                parts.append(getattr(tr, "content", ""))
            md = getattr(el, "mention_doc", None)
            if md:
                title = getattr(md, "title", "") or ""
                url   = getattr(md, "url",   "") or ""
                if title or url:
                    parts.append(f"{title}({url})")
    return "".join(parts)


# ── Core splitting functions ─────────────────────────────────────

def split_section_to_chunks(
    section: Section,
    doc_id: str,
    doc_title: str,
    doc_url: str,
    chunk_counter: List[int],
    max_chunk_tokens: int,
    overlap_tokens: int,
) -> List[Chunk]:
    """
    Convert a single Section into one or more Chunks.

    - If token_count <= max_chunk_tokens: one Chunk directly.
    - Otherwise: sliding window on paragraph boundaries (split by double newline),
      with overlap_tokens characters of overlap between windows.
    """
    full_text = section.full_text
    if not full_text.strip():
        return []

    heading = section.heading_path

    def make_chunk(text: str, block_ids: List[str]) -> Chunk:
        chunk_counter[0] += 1
        return Chunk(
            chunk_id=f"{doc_id}_{chunk_counter[0]}",
            doc_id=doc_id,
            doc_title=doc_title,
            doc_url=doc_url,
            heading_path=heading,
            content_type=ChunkContentType.TEXT,
            content=text,
            block_ids=block_ids,
            token_count=estimate_tokens(text),
            has_table=False,
        )

    if section.token_count <= max_chunk_tokens:
        return [make_chunk(full_text, section.block_ids)]

    # Sliding window on paragraphs
    paragraphs = [p for p in full_text.split("\n\n") if p.strip()]
    chunks: List[Chunk] = []
    current_paras: List[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = estimate_tokens(para)
        if current_tokens + para_tokens > max_chunk_tokens and current_paras:
            chunks.append(make_chunk("\n\n".join(current_paras), section.block_ids))
            # keep overlap: drop paragraphs from front until under overlap budget
            while current_paras and current_tokens > overlap_tokens:
                dropped = current_paras.pop(0)
                current_tokens -= estimate_tokens(dropped)
        current_paras.append(para)
        current_tokens += para_tokens

    if current_paras:
        chunks.append(make_chunk("\n\n".join(current_paras), section.block_ids))

    return chunks


def merge_short_sections(sections: List[Section], min_chunk_tokens: int) -> List[Section]:
    """
    Merge adjacent short sections (token_count < min_chunk_tokens) forward.
    Stops merging when a higher-level heading boundary is encountered.
    """
    if not sections:
        return sections

    result: List[Section] = []
    pending: Section = sections[0]

    for nxt in sections[1:]:
        if pending.token_count < min_chunk_tokens:
            # merge pending into next
            merged = Section(nxt.heading_path)
            for b, t in zip(pending.blocks, pending.text_parts):
                merged.append(b, t)
            for b, t in zip(nxt.blocks, nxt.text_parts):
                merged.append(b, t)
            pending = merged
        else:
            result.append(pending)
            pending = nxt

    result.append(pending)
    return result


# ── Main entry ───────────────────────────────────────────────────

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
    """
    Stage 4a main entry: convert text blocks to Chunks.

    Processing order:
    1. Skip type1 (Page), type30/31/32 (tables, handled by table_handler),
       and any block_id in table_block_ids, and callout children already consumed.
    2. type3-11 (heading): update heading_stack, also add text to current section.
    3. type-19 (Callout): aggregate children text → "[注意] ..." single entry.
    4. type2,12-15,17,22: add to current section.
    5. type-24/25 (分栏容器/列): silently skipped — children are in flat list and
       handled by step 4 above (no content loss, layout not preserved).
    6. Split sections by heading boundaries.
    7. merge_short_sections.
    8. split_section_to_chunks.
    """
    sections: List[Section] = []
    current_section: Section = Section(heading_stack.current_path())

    # Build lookup dict for child-block resolution (callout, future uses)
    blocks_dict: Dict[str, Dict] = {b.get("block_id"): b for b in blocks if b.get("block_id")}
    # Track children consumed by callout handler to prevent double-processing
    callout_child_ids: set = set()

    def flush_section():
        nonlocal current_section
        if current_section.blocks:
            sections.append(current_section)
        current_section = Section(heading_stack.current_path())

    for block in blocks:
        block_type = block.get("block_type")
        block_id   = block.get("block_id", "")

        # Skip page root, tables, already-consumed blocks, and callout children
        if (block_type == 1 or block_type in TABLE_TYPES
                or block_id in table_block_ids or block_id in callout_child_ids):
            continue

        if block_type in HEADING_TYPES:
            flush_section()
            text = _extract_block_text(block)
            heading_stack.push(block_type, text)
            current_section = Section(heading_stack.current_path())
            # Also add heading text as first entry in the new section
            if text.strip():
                current_section.append(block, text)

        elif block_type == 19:  # Callout — aggregate children, mark consumed
            child_parts: List[str] = []
            for cid in block.get("children", []):
                child = blocks_dict.get(cid)
                if not child:
                    continue
                t = _extract_block_text(child)
                if t.strip():
                    child_parts.append(t)
                callout_child_ids.add(cid)  # prevent double-processing in flat list
            if child_parts:
                aggregated = _CALLOUT_PREFIX + " ".join(child_parts)
                current_section.append(block, aggregated)

        elif block_type in TEXT_TYPES:
            try:
                text = _extract_block_text(block)
                if text.strip():
                    current_section.append(block, text)
            except Exception:
                logger.warning("Failed to extract text from block %s", block_id)

    flush_section()

    merged = merge_short_sections(sections, cfg.min_chunk_tokens)

    result: List[Chunk] = []
    for section in merged:
        result.extend(split_section_to_chunks(
            section=section,
            doc_id=doc_id,
            doc_title=doc_title,
            doc_url=doc_url,
            chunk_counter=chunk_counter,
            max_chunk_tokens=cfg.max_chunk_tokens,
            overlap_tokens=cfg.overlap_tokens,
        ))
    return result
