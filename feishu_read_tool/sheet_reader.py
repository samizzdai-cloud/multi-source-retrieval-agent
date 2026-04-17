"""Sheet Reader v2 — Query-independent chunking.

New design:
  1. Read ALL rows per tab (exact range from metadata row_count/col_count)
  2. Fill merge cells
  3. Detect header
  4. Split into 40-row chunks with header carry-forward
  5. Each chunk prefixed with tab name
  6. Return raw chunks (score=0) — scoring done externally

Replaces v1 which had query-dependent logic (find_cells, BM25 row filter) inside the reader.
"""

import logging
import re
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

from .client import FeishuClient
from .url_tool import parse_feishu_url
from .basic_tool.sheet_utils import number_to_column
from tools.models import UnifiedChunk

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

MAX_TABS_PER_SHEET = 5
MAX_READ_ROWS = 500         # API safety cap
MAX_READ_COLS = 26          # A-Z
CHUNK_MAX_ROWS = 40         # Rows per chunk (excluding header)


# ── API backoff wrapper ───────────────────────────────────────────────────────

def _api_call_with_backoff(func, *f_args, max_attempts: int = 5, **f_kwargs):
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return func(*f_args, **f_kwargs)
        except Exception as e:
            last_exc = e
            s = str(e)
            if "99991400" in s or "rate limit" in s.lower() or "429" in s:
                backoff = min(10.0, (2 ** attempt) + random.random())
                logger.warning("sheet_reader: rate-limited (attempt %d), backoff %.1fs", attempt + 1, backoff)
                time.sleep(backoff)
                continue
            raise
    raise last_exc


# ── Row utilities ─────────────────────────────────────────────────────────────

def _is_blank_row(row) -> bool:
    if not row:
        return True
    return all(c is None or str(c).strip() == "" for c in row)


def _fill_count(row) -> int:
    if not row:
        return 0
    return sum(1 for c in row if c is not None and str(c).strip())


def _filled_cols(row) -> set:
    if not row:
        return set()
    return {j for j, c in enumerate(row) if c is not None and str(c).strip()}


def _coverage_ratio(header_cols: set, data_cols: set) -> float:
    if not data_cols:
        return 0.0
    return len(header_cols & data_cols) / len(data_cols)


# ── Merge cell fill ──────────────────────────────────────────────────────────

def _fill_merge_cells(rows: List[list], merges: List[dict], row_offset: int = 0):
    """Fill merged cell ranges with top-left value (in-place)."""
    for m in merges:
        sr = m["start_row_index"] - row_offset
        er = m["end_row_index"] - row_offset
        sc = m["start_column_index"]
        ec = m["end_column_index"]
        if sr < 0 or sr >= len(rows):
            continue
        val = rows[sr][sc] if sc < len(rows[sr]) else None
        if val is None:
            continue
        for r in range(max(sr, 0), min(er + 1, len(rows))):
            for c in range(sc, ec + 1):
                if r == sr and c == sc:
                    continue
                while len(rows[r]) <= c:
                    rows[r].append(None)
                if rows[r][c] is None or str(rows[r][c]).strip() == "":
                    rows[r][c] = val


# ── Header detection ─────────────────────────────────────────────────────────

def _detect_header(rows: List[list], merges: List[dict] = None) -> Optional[int]:
    """Find the header row index (0-based).

    Rules:
    1. Skip fill=1 rows (labels)
    2. Skip merge non-first rows
    3. First row with fill >= 3 = candidate
    4. Confirm: coverage >= 0.6 with subsequent 5 data rows
    """
    merge_non_first = set()
    if merges:
        for m in merges:
            for r in range(m["start_row_index"] + 1, m["end_row_index"] + 1):
                merge_non_first.add(r)

    for i, row in enumerate(rows):
        if i in merge_non_first:
            continue
        fc = _fill_count(row)
        if fc < 3:
            continue
        header_cols = _filled_cols(row)
        subsequent = [r for r in rows[i + 1:i + 8] if _fill_count(r) > 0][:5]
        if not subsequent:
            return i
        avg_cov = sum(_coverage_ratio(header_cols, _filled_cols(r)) for r in subsequent) / len(subsequent)
        if avg_cov >= 0.6:
            return i
    return None


# ── Markdown rendering ───────────────────────────────────────────────────────

def _render_markdown_table(header_row: list, data_rows: List[list], max_cols: int = 26) -> str:
    """Render header + data rows as Markdown table."""
    def _cell(val):
        if val is None:
            return ""
        return str(val).replace("|", "\\|").replace("\n", " ").strip()

    ncols = min(len(header_row), max_cols)
    header = [_cell(header_row[j]) if j < len(header_row) else "" for j in range(ncols)]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in range(ncols)) + " |",
    ]
    for row in data_rows:
        cells = [_cell(row[j]) if row and j < len(row) else "" for j in range(ncols)]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _rows_to_text(rows: List[list]) -> str:
    """Render rows without header as pipe-separated text."""
    lines = []
    for row in rows:
        text = " | ".join(str(c).strip() for c in row if c is not None and str(c).strip())
        if text:
            lines.append(text)
    return "\n".join(lines)


# ── Chunk splitting ──────────────────────────────────────────────────────────

def _split_to_table_chunks(
    rows: List[list],
    header_idx: Optional[int],
    source_name: str,
    source_url: str,
    tab_name: str,
    merges: List[dict] = None,
    max_rows: int = CHUNK_MAX_ROWS,
) -> List[UnifiedChunk]:
    """Split rows into chunks of max_rows with header carry-forward.

    Each chunk:
    - Prefixed with "## Sheet: {source_name} > {tab_name}"
    - Header row repeated at top
    - Up to max_rows data rows
    - Rendered as Markdown table
    """
    if not rows:
        return []

    # Separate description, header, data
    if header_idx is not None:
        desc_rows = rows[:header_idx]
        header_row = rows[header_idx]
        data_rows = rows[header_idx + 1:]
    else:
        desc_rows = []
        header_row = None
        data_rows = rows

    # Strip trailing blank data rows
    while data_rows and _is_blank_row(data_rows[-1]):
        data_rows.pop()

    # Strip trailing blank columns
    max_col = 0
    all_rows = ([header_row] if header_row else []) + data_rows
    for row in all_rows:
        if not row:
            continue
        for j in range(len(row) - 1, -1, -1):
            if row[j] is not None and str(row[j]).strip():
                max_col = max(max_col, j + 1)
                break
    if max_col == 0:
        return []

    # Description text
    desc_text = ""
    for row in desc_rows:
        t = " | ".join(str(c).strip() for c in row if c is not None and str(c).strip())
        if t:
            desc_text += t + "\n"

    heading = f"Sheet: {source_name} > {tab_name}"
    chunks = []

    if not data_rows:
        # No data — just description or header
        content = f"## {heading}\n\n"
        if desc_text:
            content += desc_text + "\n"
        if header_row:
            content += _render_markdown_table(header_row[:max_col], [], max_col)
        if content.strip():
            chunks.append(UnifiedChunk(
                text=content, source_name=source_name, source_url=source_url,
                source_tool="feishu", heading_path=heading, score=0.0,
                metadata={"doc_type": "SHEET", "tab": tab_name, "table_rows": 0},
            ))
        return chunks

    # Split data rows into chunks of max_rows
    for i in range(0, len(data_rows), max_rows):
        batch = data_rows[i:i + max_rows]
        content = f"## {heading}\n\n"
        if desc_text and i == 0:
            content += desc_text + "\n"
        if header_row:
            content += _render_markdown_table(header_row[:max_col], [r[:max_col] for r in batch], max_col)
        else:
            content += _rows_to_text(batch)

        chunks.append(UnifiedChunk(
            text=content, source_name=source_name, source_url=source_url,
            source_tool="feishu", heading_path=heading, score=0.0,
            metadata={
                "doc_type": "SHEET",
                "tab": tab_name,
                "table_rows": len(batch),
                "row_offset": i,
                "total_data_rows": len(data_rows),
            },
        ))

    return chunks


# ── Per-tab reader ───────────────────────────────────────────────────────────

def _read_single_tab(
    client: FeishuClient,
    spreadsheet_token: str,
    tab: dict,
    sheet_title: str,
    sheet_url: str,
) -> List[UnifiedChunk]:
    """Read a single tab, produce raw chunks (score=0, no query logic)."""
    sheet_id = tab["sheet_id"]
    tab_title = tab.get("title", "")
    row_count = min(tab.get("row_count", 200), MAX_READ_ROWS)
    col_count = min(tab.get("column_count", 20), MAX_READ_COLS)
    merges = tab.get("merges", [])

    if row_count <= 0 or col_count <= 0:
        return []

    # Read exact range from metadata
    range_str = f"{sheet_id}!A1:{number_to_column(col_count - 1)}{row_count}"
    try:
        rows = _api_call_with_backoff(client.read_sheet_range, spreadsheet_token, range_str)
    except Exception as e:
        logger.warning("sheet_reader: read_sheet_range failed for tab %s: %s", tab_title, e)
        return []

    if not rows:
        return []

    # Trim trailing blank rows
    while rows and _is_blank_row(rows[-1]):
        rows.pop()
    if not rows:
        return []

    # Fill merge cells
    _fill_merge_cells(rows, merges)

    # Detect header
    header_idx = _detect_header(rows, merges)

    # Split to chunks
    return _split_to_table_chunks(
        rows, header_idx,
        source_name=sheet_title, source_url=sheet_url, tab_name=tab_title,
        merges=merges, max_rows=CHUNK_MAX_ROWS,
    )


# ── Per-sheet reader ─────────────────────────────────────────────────────────

def _read_single_sheet(
    client: FeishuClient,
    candidate: dict,
    keywords: List[str],
) -> List[UnifiedChunk]:
    """Read a single spreadsheet. Keywords used only for tab title filtering."""
    spreadsheet_token = candidate.get("id", "")
    sheet_title = candidate.get("title", "")
    sheet_url = candidate.get("url", "")

    # Wiki resolution
    is_wiki = (candidate.get("_source") == "wiki" or
               (sheet_url and "/wiki/" in sheet_url and "/sheets/" not in sheet_url))
    if is_wiki:
        wiki_token = candidate.get("node_id") or candidate.get("id") or ""
        if not wiki_token and sheet_url:
            m = re.search(r'/wiki/([^/?#]+)', sheet_url)
            if m:
                wiki_token = m.group(1)
        if wiki_token:
            try:
                node_info = _api_call_with_backoff(client.get_wiki_node_info, wiki_token)
                obj_token = node_info.get("obj_token", "")
                obj_type = node_info.get("obj_type", "").lower()
                if obj_token and obj_type in ("sheet", "sheets"):
                    spreadsheet_token = obj_token
                elif obj_token and obj_type not in ("sheet", "sheets"):
                    return []
            except Exception:
                pass

    if not spreadsheet_token:
        return []

    # Get tab metadata
    try:
        tabs = _api_call_with_backoff(client.query_all_sheets_metadata, spreadsheet_token)
    except Exception:
        return []

    visible_tabs = [t for t in tabs if not t.get("hidden") and t.get("resource_type") == "sheet"]
    if not visible_tabs:
        return []

    # Tab filtering by keyword match on title (query-light, only for tab selection)
    if keywords:
        matched = [t for t in visible_tabs
                   if any(kw.lower() in t.get("title", "").lower() for kw in keywords)]
        candidate_tabs = matched if matched else visible_tabs
    else:
        candidate_tabs = visible_tabs
    candidate_tabs = candidate_tabs[:MAX_TABS_PER_SHEET]

    # Parallel tab read
    chunks = []
    with ThreadPoolExecutor(max_workers=min(len(candidate_tabs), 5)) as executor:
        futures = {
            executor.submit(_read_single_tab, client, spreadsheet_token, tab, sheet_title, sheet_url): tab
            for tab in candidate_tabs
        }
        for future in as_completed(futures):
            try:
                chunks.extend(future.result())
            except Exception as e:
                logger.warning("sheet_reader: tab failed: %s", e)

    return chunks


# ── Entry point ──────────────────────────────────────────────────────────────

def extract_keywords(query: str) -> List[str]:
    """Extract keywords from query for tab title filtering."""
    cn = re.findall(r'[\u4e00-\u9fff]{2,}', query)
    en = re.findall(r'[a-zA-Z]{2,}', query)
    return list(dict.fromkeys(cn + en))


def read_sheets(
    query: str,
    candidates: List[dict],
    uat: str,
) -> List[UnifiedChunk]:
    """Read sheet candidates → raw UnifiedChunks (score=0).

    Chunks are query-independent (no BM25 scoring inside).
    Scoring is done externally by _two_stage_score_chunks().
    """
    client = FeishuClient(user_access_token=uat)
    keywords = extract_keywords(query)

    all_chunks: List[UnifiedChunk] = []
    with ThreadPoolExecutor(max_workers=min(len(candidates), 3)) as executor:
        futures = {
            executor.submit(_read_single_sheet, client, cand, keywords): cand
            for cand in candidates
        }
        for future in as_completed(futures):
            try:
                all_chunks.extend(future.result())
            except Exception as e:
                logger.warning("sheet_reader: failed: %s", e)

    logger.info("sheet_reader: %d raw chunks from %d candidates", len(all_chunks), len(candidates))
    return all_chunks
