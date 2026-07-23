# tools/feishu/table_handler.py
# Stage 4b: table block handling

import json
import logging
import re
from typing import List, Dict, Optional, Tuple, Callable

from .pipeline_models import Chunk, ChunkContentType, PipelineConfig

logger = logging.getLogger(__name__)


# ── Cell value flattener ─────────────────────────────────────────

def _flatten_cell(val) -> str:
    """Convert any cell value (str, int, float, list, dict) to a plain text string.

    Handles Feishu sheet rich-text cells that arrive as a list of segment dicts:
      [{'segmentStyle': {...}, 'text': 'content', 'type': 'text'}, ...]
    Also handles bitable value formats (text/name/value keys).
    """
    if val is None:
        return ""
    if isinstance(val, bool):
        return "是" if val else "否"
    if isinstance(val, (int, float)):
        return str(int(val)) if isinstance(val, float) and val == int(val) else str(val)
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        parts = []
        for item in val:
            if isinstance(item, dict):
                # Feishu rich-text segment: {'text': '...', 'type': 'text', 'segmentStyle': ...}
                text = item.get("text") or item.get("name") or item.get("value") or item.get("title")
                if text is not None:
                    parts.append(_flatten_cell(text))
                else:
                    # Unknown dict structure — try str representation of meaningful keys
                    parts.append(str(item)[:60])
            else:
                parts.append(_flatten_cell(item))
        return ", ".join(p for p in parts if p)
    if isinstance(val, dict):
        text = val.get("text") or val.get("name") or val.get("value") or val.get("title")
        if text is not None:
            return _flatten_cell(text)
        if "link" in val:
            return val.get("text", val["link"])[:80]
        return str(val)[:80]
    return str(val)[:80]


# ── Size detection ───────────────────────────────────────────────

def get_table_dimensions(block: Dict, blocks_dict: Dict) -> Tuple[int, int]:
    """
    Get (row_count, col_count) for a type31 (native grid) table block.
    Reads from block['table_row_size'] and block['table_column_size'] set by feishu_client.

    Args:
        block:       type31 block dict
        blocks_dict: {block_id: block} full index (unused for type31 but kept for extensibility)

    Returns:
        (row_count, col_count)

    Raises:
        ValueError: if block_type is not 31
    """
    block_type = block.get("block_type")
    if block_type != 31:
        raise ValueError(f"Expected type31, got {block_type}")

    # type31: flat fields set by feishu_client
    row_size = block.get("table_row_size")
    col_size = block.get("table_column_size")

    # Also try nested table object (SDK raw form)
    if row_size is None or col_size is None:
        table_obj = block.get("table")
        if table_obj is not None:
            if isinstance(table_obj, dict):
                row_size = row_size or table_obj.get("row_size")
                col_size = col_size or table_obj.get("column_size")
            else:
                row_size = row_size or getattr(table_obj, "row_size", None)
                col_size = col_size or getattr(table_obj, "column_size", None)

    if row_size is None or col_size is None:
        # Infer from children count
        children = block.get("children", [])
        col_size = col_size or max(1, len(children))
        row_size = row_size or (len(children) // col_size) if col_size else 0
        logger.warning("Table block %s missing size fields, inferred (%s, %s)",
                       block.get("block_id"), row_size, col_size)

    return (int(row_size), int(col_size))


def is_small_table(row_count: int, col_count: int, cfg: PipelineConfig) -> bool:
    """
    Two-condition small table check:
    - Tall-narrow: row <= table_small_max_row AND col <= table_small_max_col
    - Short-wide:  row <= table_wide_max_row  AND col <= table_wide_max_col
    """
    return (
        (row_count <= cfg.table_small_max_row and col_count <= cfg.table_small_max_col) or
        (row_count <= cfg.table_wide_max_row  and col_count <= cfg.table_wide_max_col)
    )


# ── Cell text extraction ─────────────────────────────────────────

def _extract_text_from_obj(text_obj) -> str:
    """Extract plain text from a text SDK object or dict (elements[].text_run.content)."""
    if isinstance(text_obj, dict):
        elements = text_obj.get("elements", [])
    else:
        elements = getattr(text_obj, "elements", []) or []

    parts = []
    for el in elements:
        if isinstance(el, dict):
            tr = el.get("text_run")
            if tr:
                parts.append(tr.get("content", "") if isinstance(tr, dict) else getattr(tr, "content", ""))
        else:
            tr = getattr(el, "text_run", None)
            if tr:
                parts.append(getattr(tr, "content", ""))
    return "".join(parts)


def _extract_cell_text(cell_block: Dict, blocks_dict: Optional[Dict] = None) -> str:
    """Extract plain text from a type32 table cell block.

    type-32 cells have no direct 'text' attribute — their content lives in
    child type-2 text blocks.  blocks_dict is required to look them up.
    Falls back to direct 'text' attribute for defensive compatibility.
    """
    # Defensive fallback: direct text (shouldn't occur for type-32, but safe)
    text_obj = cell_block.get("text")
    if text_obj:
        return _extract_text_from_obj(text_obj)

    # Normal path: aggregate text from child type-2 blocks
    if not blocks_dict:
        return ""
    parts = []
    for child_id in cell_block.get("children", []):
        child = blocks_dict.get(child_id, {})
        child_text_obj = child.get("text")
        if child_text_obj:
            parts.append(_extract_text_from_obj(child_text_obj))
    return "\n".join(p for p in parts if p)


def _extract_table_rows(block: Dict, blocks_dict: Dict, col_size: Optional[int] = None) -> List[List[str]]:
    """Extract cell texts from type31 table children, organized as rows x cols."""
    if col_size is None:
        _, col_size = get_table_dimensions(block, blocks_dict)
    col_size = max(col_size, 1)
    children = block.get("children", [])
    cells = [_extract_cell_text(blocks_dict.get(cid, {}), blocks_dict) for cid in children]
    rows = []
    for i in range(0, len(cells), col_size):
        rows.append(cells[i:i + col_size])
    return rows


# ── Markdown rendering ───────────────────────────────────────────

def _render_markdown(rows: List[List[str]]) -> str:
    """Render 2D string array as a Markdown table."""
    if not rows:
        return ""

    def escape(cell: str) -> str:
        return cell.replace("|", "\\|").replace("\n", " ")

    header = rows[0]
    lines = []
    lines.append("| " + " | ".join(escape(c) for c in header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for row in rows[1:]:
        padded = row + [""] * max(0, len(header) - len(row))
        lines.append("| " + " | ".join(escape(c) for c in padded[:len(header)]) + " |")
    return "\n".join(lines)


# ── Header axis detection ────────────────────────────────────────

def is_header_cell(text: str) -> bool:
    """
    True if cell looks like a descriptive label rather than a data value.
    True:  pure text, no digits  OR  label patterns like Q1 / 2025年 / 第一季度
    False: pure numbers, numbers+units (120 / 120万 / 98%), empty string
    """
    if not text.strip():
        return False
    # Pure number or number+unit
    if re.fullmatch(r"[\d,，.。%％万亿千百十元$€£\s]+", text.strip()):
        return False
    return True


def detect_header_axes(table_data: List[List[str]]) -> dict:
    """
    Determine if first row and/or first column are header axes.
    Returns dict with has_row_header, has_col_header, header_structure,
    col_headers, row_headers.
    """
    if not table_data or len(table_data) < 2:
        return {
            "has_row_header": False, "has_col_header": False,
            "header_structure": "none", "col_headers": [], "row_headers": [],
        }

    THRESHOLD = 0.8

    first_row  = table_data[0]
    first_col  = [row[0] for row in table_data if row]

    def header_ratio(cells):
        if not cells:
            return 0.0
        return sum(1 for c in cells if is_header_cell(c)) / len(cells)

    has_row_header = header_ratio(first_row)  >= THRESHOLD
    has_col_header = header_ratio(first_col)  >= THRESHOLD

    if has_row_header and has_col_header:
        structure = "both"
    elif has_row_header:
        structure = "row_only"
    elif has_col_header:
        structure = "col_only"
    else:
        structure = "none"

    return {
        "has_row_header": has_row_header,
        "has_col_header": has_col_header,
        "header_structure": structure,
        "col_headers": list(first_row) if has_row_header else [],
        "row_headers": [row[0] for row in table_data if row] if has_col_header else [],
    }


# ── Large table LLM subtable extraction ──────────────────────────

def extract_subtable_with_llm(
    query: str,
    table_data: List[List[str]],
    header_info: dict,
    llm_caller: Callable[[str], str],
) -> dict:
    """
    Call LLM to select relevant rows/columns and build a JSON subtable.

    Returns a JSON-serializable subtable dict.
    Raises json.JSONDecodeError if LLM response can't be parsed.
    """
    source_size = f"{len(table_data)}x{len(table_data[0]) if table_data else 0}"
    prompt = f"""You are a data extraction assistant.
User query: "{query}"

Table size: {source_size}
Header structure: {header_info.get('header_structure')}
Column headers: {header_info.get('col_headers')}
Row headers (first 20): {header_info.get('row_headers', [])[:20]}

First 5 data rows (sample):
{json.dumps(table_data[:5], ensure_ascii=False)}

Task: Select only the columns and rows most relevant to the query.
Return a JSON object (no other text):
{{
  "table_summary": "one sentence description",
  "header_structure": "{header_info.get('header_structure')}",
  "col_headers": ["selected", "column", "names"],
  "data": [{{"col1": "val1", "col2": "val2"}}],
  "source_table_size": "{source_size}",
  "filter_note": "brief explanation of what was selected"
}}"""

    response_text = llm_caller(prompt)

    # Strip markdown code fences
    clean = response_text.strip()
    if clean.startswith("```"):
        clean = re.sub(r'^```(?:json)?\s*', '', clean)
        clean = re.sub(r'\s*```$', '', clean)

    return json.loads(clean.strip())



# ── Row Splitting Helper ─────────────────────────────────────────

def _split_rows_into_chunks(
    rows: List[List[str]],
    doc_id: str,
    doc_title: str,
    doc_url: str,
    heading_path: str,
    chunk_counter: List[int],
    consumed: set,
    header_info: dict,
    rows_per_chunk: int = 20,
    section_meta: Optional[Dict] = None,
) -> List[Chunk]:
    chunks = []
    if not rows:
        return chunks
        
    has_row_header = header_info.get("has_row_header", False)
    header_rows = 1 if has_row_header else 0
    
    if header_rows > 0 and len(rows) > 1:
        headers = rows[0]
        data_rows = rows[1:]
    else:
        headers = None
        data_rows = rows
        
    for i in range(0, len(data_rows), rows_per_chunk):
        chunk_counter[0] += 1
        chunk_id = f"{doc_id}_{chunk_counter[0]}"
        
        batch = data_rows[i:i+rows_per_chunk]
        if headers:
            batch_to_render = [headers] + batch
        else:
            batch_to_render = batch
            
        md_table = _render_markdown(batch_to_render)
        
        # Embed heading_path into content for RAG readability
        heading_line = f"## {heading_path}\n\n" if heading_path else ""
        context_header = f"**[Table from: {doc_title}]**\n"
        content_text = heading_line + context_header + md_table
        
        meta = dict(section_meta or {})
        meta.update({
            "block_ids": list(consumed),
            "row_count": len(rows),
            "rows_in_chunk": len(batch),
            "row_index_offset": i,
        })
        chunks.append(Chunk(
            chunk_id=chunk_id,
            doc_id=doc_id,
            doc_title=doc_title,
            doc_url=doc_url,
            heading_path=heading_path,
            content_type=ChunkContentType.TABLE_MD,
            content=content_text,
            block_ids=list(consumed),
            token_count=max(1, len(content_text) // 2),
            has_table=True,
            metadata=meta,
        ))
    return chunks


def _is_blank_row(row: List[str]) -> bool:
    return all((not str(c).strip()) for c in row)


def _token_set(row: List[str]) -> set:
    toks = set()
    for c in row:
        if not c:
            continue
        for t in re.findall(r"[\w一-龥]+", str(c).lower()):
            toks.add(t)
    return toks


def _row_header_ratio(row: List[str]) -> float:
    if not row:
        return 0.0
    return sum(1 for c in row if is_header_cell(str(c))) / len(row)


def header_coverage(header_row: List[str], data_rows: List[List[str]], sample_n: int = 50) -> float:
    """Compute a simple header->data coverage score (0..1).

    Heuristic: for each header cell, detect whether the column's data type
    matches the header's "nature" (text-like vs numeric/date-like). Return
    the average column consistency.
    """
    if not header_row or not data_rows:
        return 0.0
    ncols = len(header_row)
    scores = []
    rows_considered = data_rows[:sample_n]

    num_re = re.compile(r"^[\d,，.。%％万亿千百十元$€£\s+-]+$")
    date_re = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}")

    for j in range(ncols):
        header = str(header_row[j]).strip()
        if header == "":
            continue
        header_is_text = is_header_cell(header)
        col_vals = [str(r[j]).strip() for r in rows_considered if len(r) > j and str(r[j]).strip()]
        if not col_vals:
            scores.append(0.0)
            continue
        numeric_count = sum(1 for v in col_vals if num_re.fullmatch(v) or date_re.search(v))
        numeric_ratio = numeric_count / len(col_vals)
        if header_is_text:
            # header looks like label -> expect mostly non-numeric data
            scores.append(1.0 - numeric_ratio)
        else:
            # header looks numeric -> expect numeric-like data
            scores.append(numeric_ratio)

    return sum(scores) / len(scores) if scores else 0.0


def segment_rows_into_sections(rows: List[List[str]], header_threshold: float = 0.8) -> List[Dict]:
    """Split a sheet's rows into logical sections.

    Rules:
    - Split on runs of blank rows (empty row separators).
    - Within a non-empty block, split when a row looks like a repeated header
      (header ratio >= header_threshold and token overlap with first row),
      or when column count changes, or when a subtotal-like token appears.
    Returns a list of dicts: {"rows": subrows, "start": i_start, "end": i_end}.
    """
    if not rows:
        return []
    subtotal_re = re.compile(r"合计|小计|total|总计", re.I)
    n = len(rows)
    i = 0
    sections = []
    while i < n:
        # skip leading blanks
        while i < n and _is_blank_row(rows[i]):
            i += 1
        if i >= n:
            break
        start = i
        while i < n and not _is_blank_row(rows[i]):
            i += 1
        end = i  # exclusive
        block = rows[start:end]
        # further split block by repeated header / col count change / subtotal
        if not block:
            continue
        first = block[0]
        splits = [0]
        for idx in range(1, len(block)):
            r = block[idx]
            if len(r) != len(first):
                splits.append(idx)
                continue
            if _row_header_ratio(r) >= header_threshold:
                # token overlap heuristic
                a = _token_set(first)
                b = _token_set(r)
                if a and b:
                    overlap = len(a & b) / max(1, min(len(a), len(b)))
                else:
                    overlap = 0.0
                if r == first or overlap >= 0.5:
                    splits.append(idx)
                    continue
            if any(subtotal_re.search(str(cell)) for cell in r if cell):
                # end after this row
                splits.append(idx + 1)

        splits.append(len(block))
        # materialize splits into sections
        for s_idx in range(len(splits) - 1):
            a = splits[s_idx]
            b = splits[s_idx + 1]
            subrows = block[a:b]
            if subrows:
                sections.append({"rows": subrows, "start": start + a, "end": start + b})

    return sections


# ── Error chunk helper ───────────────────────────────────────────

def _make_error_chunk(
    chunk_id: str, doc_id: str, doc_title: str, doc_url: str,
    heading_path: str, content: str, consumed: set,
) -> Chunk:
    return Chunk(
        chunk_id=chunk_id, doc_id=doc_id, doc_title=doc_title,
        doc_url=doc_url, heading_path=heading_path,
        content_type=ChunkContentType.TABLE_MD,
        content=content, block_ids=list(consumed),
        token_count=1, has_table=True,
    )


# ── type30 (embedded sheet) handler ─────────────────────────────

def _embedded_sheet_failure_disposition(error: Exception) -> str:
    """A malformed/dead embed is skipped; only transport is retryable."""
    text = str(error).lower()
    markers = (
        "invalid token", "invalid spreadsheet", "invalid sheet", "invalid parameter",
        "field validation", "spreadsheet_token is required", "malformed token",
    )
    return "skip_invalid_token" if any(marker in text for marker in markers) else "transient"


def fetch_embedded_sheet_rows(
    block: Dict,
    client,
    doc_id: str,
    cfg: "PipelineConfig",
) -> Optional[List[List]]:
    """Read one type-30 child without allocating chunk IDs.

    This is deliberately separate from ``build_sheet_chunk`` so a Docx can
    fetch independent Sheets children concurrently while converting successful
    responses to chunks in stable document order.  A failed child is recorded
    as a read gap and never invalidates its enclosing Docx.
    """
    block_id = block.get("block_id", "")
    sheet_obj = block.get("sheet")
    if not sheet_obj:
        return None
    if hasattr(sheet_obj, "token"):
        raw_token = sheet_obj.token
    elif isinstance(sheet_obj, dict):
        raw_token = sheet_obj.get("token")
    else:
        raw_token = None
    if not raw_token:
        return None
    try:
        from .basic_tool.sheet_utils import parse_sheet_token, number_to_column
        from .sheet_reader import _api_call_with_backoff
        spreadsheet_token, sheet_id = parse_sheet_token(raw_token)
        range_str = f"{sheet_id}!A1:{number_to_column(19)}100"
        return _api_call_with_backoff(
            client.read_sheet_range, spreadsheet_token, range_str,
            per_call_timeout_s=3.0, max_attempts=1,
        )
    except Exception as e:
        msg = str(e)
        if len(msg) > 160:
            msg = msg[:160] + "..."
        logger.warning(
            "feishu.read_fail source=type30_sheet doc=%s block=%s token=%s kind=%s msg=%s",
            doc_id[:12], block_id[:12], str(raw_token)[:24], type(e).__name__, msg,
        )
        sink = getattr(cfg, "io_failure_sink", None)
        if isinstance(sink, list):
            sink.append({
                "source": "type30_sheet",
                "document_id": str(doc_id),
                "block_id": str(block_id),
                "spreadsheet_token": str(spreadsheet_token) if 'spreadsheet_token' in locals() else "",
                "sheet_id": str(sheet_id) if 'sheet_id' in locals() else "",
                "error_type": type(e).__name__,
                "disposition": _embedded_sheet_failure_disposition(e),
            })
        return None

def build_sheet_chunk(
    block: Dict,
    client,
    doc_id: str,
    doc_title: str,
    doc_url: str,
    heading_path: str,
    chunk_counter: List[int],
    query: str,
    llm_caller: Callable[[str], str],
    cfg: "PipelineConfig",
    rows_raw: Optional[List[List]] = None,
) -> Tuple[List[Chunk], set]:
    """Convert a single type30 (embedded Sheet) block into Chunks."""
    block_id = block.get("block_id", "")
    consumed: set = {block_id}

    sheet_obj = block.get("sheet")
    if not sheet_obj:
        return [], consumed

    if hasattr(sheet_obj, "token"):
        raw_token = sheet_obj.token
    elif isinstance(sheet_obj, dict):
        raw_token = sheet_obj.get("token")
    else:
        raw_token = None

    if not raw_token:
        return [], consumed

    try:
        from .basic_tool.sheet_utils import parse_sheet_token
        spreadsheet_token, sheet_id = parse_sheet_token(raw_token)
    except ValueError:
        # The normal read path records malformed tokens through the fetch
        # helper. A caller supplying pre-fetched rows must still not construct
        # untraceable chunks from an invalid token.
        return [], consumed

    if rows_raw is None:
        # A type-30 child is optional only relative to preserving the enclosing
        # Docx body/native-grid chunks. One bounded read exposes its gap without
        # discarding parent chunks. The Docx pipeline normally prefetches rows
        # in a dedicated Sheets pool and passes them here in stable block order.
        rows_raw = fetch_embedded_sheet_rows(block, client, doc_id, cfg)
    if rows_raw is None:
        return [], consumed

    rows: List[List[str]] = [
        [_flatten_cell(cell) for cell in row]
        for row in rows_raw
    ]

    if not rows:
        return [], consumed

    # Skip sheets where every cell is blank (visual layout / whiteboard placeholders)
    if all(_is_blank_row(row) for row in rows):
        return [], consumed

    # Strip trailing all-blank rows before processing
    while rows and _is_blank_row(rows[-1]):
        rows.pop()
    if not rows:
        return [], consumed

    # Strip all-blank columns (Feishu layout tables often have many empty trailing cols)
    if rows:
        ncols = max(len(r) for r in rows)
        # Find rightmost non-empty column index
        last_nonempty_col = -1
        for col_idx in range(ncols - 1, -1, -1):
            if any(len(r) > col_idx and r[col_idx].strip() for r in rows):
                last_nonempty_col = col_idx
                break
        if last_nonempty_col >= 0:
            rows = [r[:last_nonempty_col + 1] for r in rows]
        else:
            return [], consumed  # all columns are blank

    header_info = detect_header_axes(rows)
    chunks = _split_rows_into_chunks(
        rows, doc_id, doc_title, doc_url, heading_path,
        chunk_counter, consumed, header_info, rows_per_chunk=20,
        section_meta={
            "source_kind": "docx_embedded_sheet",
            "embedded_spreadsheet_token": spreadsheet_token,
            "embedded_sheet_id": sheet_id,
            "embedded_block_id": block_id,
        },
    )
    return chunks, consumed

# ── type31 (native grid) handler ─────────────────────────────────

def build_table_chunk(
    block: Dict,
    blocks_dict: Dict,
    doc_id: str,
    doc_title: str,
    doc_url: str,
    heading_path: str,
    chunk_counter: List[int],
    query: str,
    llm_caller: Callable[[str], str],
    cfg: "PipelineConfig",
) -> Tuple[List["Chunk"], set]:
    """Convert a single type31 (native grid) table block into Chunks.

    Decision tree:
      - Small table  → single TABLE_MD chunk (one call)
      - Large + headers → LLM JSON subtable extraction → TABLE_JSON chunk
      - Large + no headers → truncated TABLE_MD (first 20 rows)
    Returns (list_of_chunks, consumed_block_ids_set).
    """
    chunk_counter[0] += 1
    chunk_id = f"{doc_id}_{chunk_counter[0]}"
    consumed: set = {block.get("block_id", "")}
    for cid in block.get("children", []):
        consumed.add(cid)
        child = blocks_dict.get(cid, {})
        for gcid in child.get("children", []):
            consumed.add(gcid)

    try:
        row_count, col_count = get_table_dimensions(block, blocks_dict)
    except Exception:
        row_count, col_count = 0, 0

    rows = _extract_table_rows(block, blocks_dict, col_size=col_count)

    if not rows:
        return [], consumed

    # Skip tables where every cell is blank (visual layout / whiteboard placeholders)
    if all(_is_blank_row(row) for row in rows):
        return [], consumed

    # Strip all-blank columns (Feishu layout tables often have many empty trailing cols)
    if rows:
        ncols = max(len(r) for r in rows)
        last_nonempty_col = -1
        for col_idx in range(ncols - 1, -1, -1):
            if any(len(r) > col_idx and r[col_idx].strip() for r in rows):
                last_nonempty_col = col_idx
                break
        if last_nonempty_col >= 0:
            rows = [r[:last_nonempty_col + 1] for r in rows]
            col_count = last_nonempty_col + 1
        else:
            return [], consumed

    # ── Small table: direct Markdown ──────────────────────────────
    if is_small_table(row_count, col_count, cfg):
        content = _render_markdown(rows)
        return [Chunk(
            chunk_id=chunk_id, doc_id=doc_id, doc_title=doc_title, doc_url=doc_url,
            heading_path=heading_path, content_type=ChunkContentType.TABLE_MD,
            content=content, block_ids=list(consumed),
            token_count=max(1, len(content) // 2), has_table=True,
            metadata={"table_rows": row_count, "table_cols": col_count},
        )], consumed

    # ── Large table: split with header carry-forward ────────────
    # Revert the pre-incremented chunk_counter since _split_rows_into_chunks
    # will increment it for each sub-chunk.
    chunk_counter[0] -= 1

    header_info = detect_header_axes(rows)
    chunks = _split_rows_into_chunks(
        rows, doc_id, doc_title, doc_url, heading_path,
        chunk_counter, consumed, header_info, rows_per_chunk=20,
    )
    return chunks, consumed
