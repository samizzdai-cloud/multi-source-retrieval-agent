"""Bitable Reader v2 — Query-independent chunking.

New design:
  1. list_tables → select tables (keyword on name, max 3)
  2. Per table: list_fields → search_records (全量分页) → flatten values
  3. Split into 40-row chunks with field header carry-forward
  4. Each chunk prefixed with table name
  5. Return raw chunks (score=0) — scoring done externally

Replaces v1 which had query-dependent logic (contains filter, BM25 row filter) inside.
"""

import concurrent.futures
import logging
import re
import time
import random
from datetime import datetime, timezone
from typing import Dict, List, Optional
from urllib.parse import urlparse, parse_qs

from .basic_tool.bitable_read import list_tables, search_records, list_fields
from .url_tool import parse_feishu_url
from .client import FeishuClient
from tools.models import UnifiedChunk

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

MAX_TABLES_PER_BITABLE = 3
MAX_RECORDS = 2000          # Safety cap (search_records pages through all)
CHUNK_MAX_ROWS = 40         # Rows per chunk (excluding header)

# Text-renderable field types
_TEXT_FIELD_TYPES = {
    1, 2, 3, 4, 5, 7, 11, 13, 15, 20, 1001, 1002, 1003, 1004,
}

# ── DateTime formatting ───────────────────────────────────────────────────────

_TS_THRESHOLD_MS = 1_000_000_000_000

def _format_timestamp(val: int) -> str:
    try:
        dt = datetime.fromtimestamp(val / 1000, tz=timezone.utc)
        if dt.hour == 0 and dt.minute == 0 and dt.second == 0:
            return dt.strftime("%Y-%m-%d")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(val)


# ── Field value flattening ────────────────────────────────────────────────────

def _flatten_value(val) -> str:
    """Flatten complex bitable field values to plain text."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return "是" if val else "否"
    if isinstance(val, int):
        if val >= _TS_THRESHOLD_MS:
            return _format_timestamp(val)
        return str(val)
    if isinstance(val, float):
        if val >= _TS_THRESHOLD_MS:
            return _format_timestamp(int(val))
        return str(int(val)) if val == int(val) else f"{val:.4g}"
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        parts = []
        for v in val:
            if isinstance(v, dict):
                text = v.get("text") or v.get("name") or v.get("value")
                parts.append(_flatten_value(text) if text is not None else str(v)[:50])
            else:
                parts.append(_flatten_value(v))
        return ", ".join(p for p in parts if p)
    if isinstance(val, dict):
        text = val.get("text") or val.get("name") or val.get("value")
        if text is not None:
            return _flatten_value(text)
        if "link" in val:
            return val.get("text", val["link"])[:80]
        return str(val)[:80]
    return str(val)[:80]


# ── API backoff wrapper ───────────────────────────────────────────────────────

def _api_call_with_backoff(func, *f_args, max_attempts: int = 5, **f_kwargs):
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return func(*f_args, **f_kwargs)
        except Exception as e:
            last_exc = e
            s = str(e)
            if "99991400" in s or "rate limit" in s.lower() or "429" in s or "1254290" in s:
                backoff = min(10.0, (2 ** attempt) + random.random())
                logger.warning("bitable_reader: rate-limited (attempt %d), backoff %.1fs", attempt + 1, backoff)
                time.sleep(backoff)
                continue
            raise
    raise last_exc


# ── Markdown rendering ───────────────────────────────────────────────────────

def _render_markdown_table(field_names: List[str], rows: List[List[str]]) -> str:
    """Render field header + data rows as Markdown table."""
    def _cell(val):
        return str(val).replace("|", "\\|").replace("\n", " ").strip()

    lines = [
        "| " + " | ".join(_cell(fn) for fn in field_names) + " |",
        "| " + " | ".join("---" for _ in field_names) + " |",
    ]
    for row in rows:
        cells = [_cell(row[j]) if j < len(row) else "" for j in range(len(field_names))]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


# ── Chunk splitting ──────────────────────────────────────────────────────────

def _split_to_bitable_chunks(
    field_names: List[str],
    data_rows: List[List[str]],
    source_name: str,
    source_url: str,
    table_name: str,
    table_id: str = "",
    max_rows: int = CHUNK_MAX_ROWS,
) -> List[UnifiedChunk]:
    """Split records into chunks of max_rows with field header carry-forward."""
    if not data_rows:
        return []

    # Strip trailing blank rows
    while data_rows and all(not c.strip() for c in data_rows[-1]):
        data_rows.pop()
    if not data_rows:
        return []

    heading = f"Bitable: {source_name} > {table_name}"
    chunks = []

    for i in range(0, len(data_rows), max_rows):
        batch = data_rows[i:i + max_rows]
        md = _render_markdown_table(field_names, batch)
        content = f"## {heading}\n\n{md}"

        chunks.append(UnifiedChunk(
            text=content,
            source_name=source_name,
            source_url=source_url,
            source_tool="feishu",
            heading_path=heading,
            score=0.0,
            metadata={
                "doc_type": "BITABLE",
                "table": table_name,
                "table_id": table_id,
                "table_rows": len(batch),
                "row_offset": i,
                "total_records": len(data_rows),
                "field_names": field_names,
            },
        ))

    return chunks


# ── Per-table reader ─────────────────────────────────────────────────────────

def _read_single_table(
    bitable_id: str,
    table_meta: dict,
    uat: str,
    bitable_title: str,
    bitable_url: str,
) -> List[UnifiedChunk]:
    """Read a single bitable table, produce raw chunks (score=0)."""
    table_id = table_meta["table_id"]
    table_name = table_meta.get("name", table_id)

    # Get fields
    try:
        fields = _api_call_with_backoff(list_fields, bitable_id, table_id, uat)
    except Exception as e:
        logger.warning("bitable_reader: list_fields failed for %s/%s: %s", bitable_id, table_id, e)
        return []

    field_names = [f["field_name"] for f in fields if f.get("type") in _TEXT_FIELD_TYPES]
    if not field_names:
        return []

    # Fetch ALL records (paginated, page_size=500)
    try:
        records = _api_call_with_backoff(search_records, bitable_id, table_id, uat)
    except Exception as e:
        logger.warning("bitable_reader: search_records failed for %s/%s: %s", bitable_id, table_id, e)
        return []

    if not records:
        return []

    # Cap at MAX_RECORDS
    records = records[:MAX_RECORDS]

    # Flatten to 2D string array
    data_rows = []
    for rec in records:
        row_fields = rec.get("fields", {})
        row = [_flatten_value(row_fields.get(fn, "")) for fn in field_names]
        data_rows.append(row)

    logger.info("bitable_reader: table %s/%s → %d records, %d fields",
                bitable_id[:8], table_name, len(data_rows), len(field_names))

    # Split to chunks
    return _split_to_bitable_chunks(
        field_names, data_rows,
        source_name=bitable_title, source_url=bitable_url,
        table_name=table_name, table_id=table_id,
        max_rows=CHUNK_MAX_ROWS,
    )


# ── Per-bitable reader ───────────────────────────────────────────────────────

def _read_single_bitable(
    candidate: dict,
    keywords: List[str],
    uat: str,
) -> List[UnifiedChunk]:
    """Read a single bitable app. Keywords used only for table name selection."""
    bitable_id = candidate.get("id", "")
    bitable_title = candidate.get("title", "")
    bitable_url = candidate.get("url", "")

    # Wiki resolution
    is_wiki = (candidate.get("_source") == "wiki" or
               (bitable_url and "/wiki/" in bitable_url and "/base/" not in bitable_url))
    if is_wiki:
        wiki_token = candidate.get("node_id") or candidate.get("id") or ""
        if not wiki_token and bitable_url:
            m = re.search(r'/wiki/([^/?#]+)', bitable_url)
            if m:
                wiki_token = m.group(1)
        if wiki_token:
            try:
                client = FeishuClient(user_access_token=uat)
                node_info = _api_call_with_backoff(client.get_wiki_node_info, wiki_token)
                obj_token = node_info.get("obj_token", "")
                obj_type = node_info.get("obj_type", "").lower()
                if obj_token and obj_type == "bitable":
                    bitable_id = obj_token
                elif obj_token and obj_type != "bitable":
                    return []
            except Exception:
                pass

    if not bitable_id:
        return []

    # List tables
    try:
        tables = _api_call_with_backoff(list_tables, bitable_id, uat)
    except Exception:
        return []

    if not tables:
        return []

    # Table selection: URL-specified table_id or keyword match
    # Check if URL contains table_id
    target_table_id = ""
    if bitable_url:
        parsed = urlparse(bitable_url)
        qs = parse_qs(parsed.query)
        if "table" in qs:
            target_table_id = qs["table"][0]

    if target_table_id:
        selected = [t for t in tables if t.get("table_id") == target_table_id]
        if not selected:
            selected = tables[:1]
    elif keywords:
        matched = [t for t in tables
                   if any(kw.lower() in t.get("name", "").lower() for kw in keywords)]
        selected = matched if matched else tables
    else:
        selected = tables
    selected = selected[:MAX_TABLES_PER_BITABLE]

    # Parallel table read (ThreadPool 3)
    all_chunks = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(selected), 3)) as executor:
        futures = {
            executor.submit(
                _read_single_table, bitable_id, tbl, uat, bitable_title, bitable_url,
            ): tbl
            for tbl in selected
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                all_chunks.extend(future.result())
            except Exception as e:
                logger.warning("bitable_reader: table read failed: %s", e)

    return all_chunks


# ── Entry point ──────────────────────────────────────────────────────────────

def _extract_keywords(query: str) -> List[str]:
    cn = re.findall(r'[\u4e00-\u9fff]{2,}', query)
    en = re.findall(r'[a-zA-Z]{2,}', query)
    return list(dict.fromkeys(cn + en))


def read_bitables(
    query: str,
    candidates: List[dict],
    uat: str,
) -> List[UnifiedChunk]:
    """Read bitable candidates → raw UnifiedChunks (score=0).

    Chunks are query-independent (no contains filter or BM25 inside).
    Scoring is done externally by _two_stage_score_chunks().
    """
    keywords = _extract_keywords(query)

    all_chunks: List[UnifiedChunk] = []
    if len(candidates) == 1:
        all_chunks = _read_single_bitable(candidates[0], keywords, uat)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(candidates), 3)) as executor:
            futures = {
                executor.submit(_read_single_bitable, cand, keywords, uat): cand
                for cand in candidates
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    all_chunks.extend(future.result())
                except Exception as e:
                    logger.warning("bitable_reader: failed: %s", e)

    logger.info("bitable_reader: %d raw chunks from %d candidates", len(all_chunks), len(candidates))
    return all_chunks
