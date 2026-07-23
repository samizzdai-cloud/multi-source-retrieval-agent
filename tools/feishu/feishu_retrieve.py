"""飞书多格式检索编排器 (Dispatcher)

职责：
  1. Phase A — 文档搜索（始终执行）：MCP-only → doc_pool
  2. Phase B — 表格搜索（条件启动）：Direct + Wiki table → sheet/bitable readers
  3. 合并各 reader 输出的 UnifiedChunk[] 返回给 RetrievalAgent

搜索路由：
  - 文档类(doc/docx): MCP-only → 按稳定 ID 去重 → doc_pool
  - 表格类(sheet/bitable): Direct API + Wiki table → TablePool → readers
  - 表格搜索仅在 query_type = "structured" 时启动

设计原则：
  - feishu_retrieve 只做路由和合并，不做具体的读取和评分
  - 每种 doc_type 的读取逻辑封装在独立 reader 中（高内聚、低耦合）
  - 新增 doc_type 支持时，只需注册新 reader，不改此文件核心逻辑
"""

import asyncio
import concurrent.futures
import contextvars
import functools
import logging
import os
from collections import defaultdict
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set

IO_FUTURE_REGISTRY: contextvars.ContextVar[Optional[Set[asyncio.Future]]] = contextvars.ContextVar(
    "feishu_io_future_registry", default=None,
)

async def _exec_to_thread(
    executor: Optional[concurrent.futures.Executor],
    fn: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """模块级 to_thread helper：executor=None 走默认池，否则走源专属池。

    保留 contextvars 传播以匹配 ``asyncio.to_thread`` 语义。
    """
    if executor is None:
        return await asyncio.to_thread(fn, *args, **kwargs)
    loop = asyncio.get_event_loop()
    ctx = contextvars.copy_context()
    func_call = functools.partial(ctx.run, fn, *args, **kwargs)
    future = loop.run_in_executor(executor, func_call)
    registry = IO_FUTURE_REGISTRY.get()
    if registry is not None:
        registry.add(future)
        future.add_done_callback(registry.discard)
    return await asyncio.shield(future)

from dotenv import load_dotenv

from feishu_read_tool.search import search_feishu_mcp_pure, search_feishu_tables
from feishu_read_tool.pipeline_models import ChunkContentType
from tools.core.models import UnifiedChunk
from tools.core.bucket_selector import native_candidate_budget

load_dotenv()
logger = logging.getLogger(__name__)

FEISHU_UAT = os.getenv("FEISHU_UAT", "")
TABLE_SEARCH_ROUTE_TOP_K = 3          # each Direct/Wiki table route request budget
TABLE_SEARCH_ROUTE_TOP_K_LITE = 3     # search budget is preset-independent
# Native-order table route budget.  Context is governed by preview/full
# evaluation after I/O, never by a child/candidate selector.
TABLE_NATIVE_ROUTE_TOP_K = 3                # 每个原生 API route 的 bounded-I/O cap
TABLE_NATIVE_ROUTE_TOP_K_LITE = 3           # search budget is preset-independent
TABLE_CHILD_READ_LIMIT = 3                  # one parent may expose hundreds of child tables

# Time-filter widening: planner sets time_filter from user's date intent (e.g. "1月15日"
# → ">2026-01-15"). But edit_time often doesn't reflect content time — narrow cutoff
# excludes valid docs. Widen left boundary, then narrow via date-anchor content filter.
TIME_FILTER_LEFT_BUFFER_DAYS = 90

# These Bitable applications are operational inputs to the retrieval system,
# not customer/business knowledge. They must never become answer evidence.
# This is a source-scope boundary, not a relevance score or post-search selector.
NON_EVIDENCE_BITABLE_TITLES = frozenset({"query_seed", "kb bailian"})


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


# ── 日期锚点抽取与候选预过滤 ─────────────────────────────────────────────────────
# 用户 query 中的日期(如 "2026年1月15日" / "1月15日到21日")会被 planner 蒸馏掉，
# 仅保留通用关键词("Nubia 销售")时，目标文档的 title/summary 经常含日期
# (如 "NUBIA 业务总结报告 (2026-01-15 ~ 2026-01-21)")，可以用作内容预过滤信号。

_DATE_PATTERNS = [
    re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"),  # 2026年1月15日
    re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日"),                   # 1月15日
    re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})"),                  # 2026-01-15 / 2026/1/15
    re.compile(r"(\d{1,2})[-/](\d{1,2})(?!\d)"),                       # 1-15 / 1/15
]


def _extract_date_anchors(text: str) -> List[str]:
    """从 text 中抽取所有日期表达，并展开为多种字符串变体用于 substring 匹配。

    例: "1月15日到21日" → ["1月15", "01-15", "1-15", "01/15", "1/15", "1月21", "01-21", ...]
    """
    if not text:
        return []
    out: set = set()
    for m in _DATE_PATTERNS[0].finditer(text):  # 2026年1月15日
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        out.update({
            f"{y}-{mo:02d}-{d:02d}", f"{y}/{mo:02d}/{d:02d}",
            f"{mo:02d}-{d:02d}", f"{mo}-{d}", f"{mo:02d}/{d:02d}", f"{mo}/{d}",
            f"{mo}月{d}日", f"{mo}月{d}",
        })
    for m in _DATE_PATTERNS[1].finditer(text):  # 1月15日
        mo, d = int(m.group(1)), int(m.group(2))
        out.update({
            f"{mo:02d}-{d:02d}", f"{mo}-{d}", f"{mo:02d}/{d:02d}", f"{mo}/{d}",
            f"{mo}月{d}日", f"{mo}月{d}",
        })
    for m in _DATE_PATTERNS[2].finditer(text):  # 2026-01-15
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        out.update({
            f"{y}-{mo:02d}-{d:02d}", f"{mo:02d}-{d:02d}", f"{mo}-{d}",
            f"{mo}月{d}日",
        })
    return sorted(out)


def _candidate_text_for_anchor_match(cand: dict) -> str:
    """合并 candidate 的 title + summary,用于 anchor substring 匹配。"""
    parts = [
        str(cand.get("title") or cand.get("name") or ""),
        str(cand.get("summary") or cand.get("snippet") or ""),
    ]
    return " ".join(parts).lower()


def _dedupe_candidates(*lists: List[dict]) -> List[dict]:
    """按 doc_id 去重,保留第一次出现的顺序。"""
    seen = set()
    out: List[dict] = []
    for lst in lists:
        for cand in lst:
            key = cand.get("id") or cand.get("document_id") or cand.get("doc_id") or cand.get("url") or ""
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(cand)
    return out


def _filter_candidates_by_date_anchors(
    candidates: List[dict],
    focus_text: str,
    sub_query: str,
    sub_query_id: str,
) -> List[dict]:
    """若 focus_text/sub_query 含日期表达,只保留 title/summary 含匹配日期的候选。

    无日期 → 不过滤(返回原样)
    有日期但零匹配 → 不过滤(兜底,避免破坏召回)
    有日期且至少 1 个匹配 → 只保留匹配的候选
    """
    anchors = _extract_date_anchors(f"{focus_text or ''}\n{sub_query or ''}")
    if not anchors:
        return candidates
    matched = []
    for cand in candidates:
        text = _candidate_text_for_anchor_match(cand)
        if any(a.lower() in text for a in anchors):
            matched.append(cand)
    if not matched:
        logger.debug(
            "feishu_retrieve: date_prefilter sub_query_id=%s anchors=%s raw=%d matched=0 → keep all (no-op)",
            sub_query_id or "unknown", anchors[:5], len(candidates),
        )
        return candidates
    logger.debug(
        "feishu_retrieve: date_prefilter sub_query_id=%s anchors=%s raw=%d matched=%d",
        sub_query_id or "unknown", anchors[:5], len(candidates), len(matched),
    )
    return matched


# ── 预检策略：快速判断搜索结果是否值得深度读取 ──────────────────────────────────

# ── doc_type 归类 ─────────────────────────────────────────────────────────────
_DOCX_TYPES = {"DOCX", "DOC", "docx", "doc"}
_SHEET_TYPES = {"SHEET", "sheet"}
_BITABLE_TYPES = {"BITABLE", "bitable"}


# ── ChunkResult → UnifiedChunk 转换 ──────────────────────────────────────────

def _chunk_result_to_unified(cr, source_tool: str = "feishu") -> UnifiedChunk:
    """将 docx_pipeline.ChunkResult 转换为 UnifiedChunk。"""
    content_type = cr.content_type
    if isinstance(content_type, ChunkContentType):
        content_type = content_type.value

    return UnifiedChunk(
        text=cr.chunks_content or cr.summary or "",
        source_name=cr.file_name or "",
        source_url=cr.file_url or "",
        source_tool=source_tool,
        heading_path=cr.heading_path or "",
        score=min(1.0, max(0.0, cr.relevance_score)),
        metadata={
            "content_type": content_type,
            "summary": cr.summary or "",
        },
    )


def _merge_unique_strs(*values: List[str]) -> List[str]:
    merged = []
    seen = set()
    for items in values:
        for item in items or []:
            if not item or item in seen:
                continue
            seen.add(item)
            merged.append(item)
    return merged


def _candidate_key(candidate: dict) -> str:
    return (
        candidate.get("id")
        or candidate.get("document_id")
        or candidate.get("doc_id")
        or candidate.get("url")
        or ""
    )


def _build_doc_candidate_bm25(query: str, candidate: dict) -> float:
    query_terms = set(re.findall(r'[\w\u4e00-\u9fff]{2,}', query.lower()))
    if not query_terms:
        return 0.0
    text = f"{candidate.get('title', '')} {candidate.get('summary', '')}".lower()
    return float(sum(text.count(term) for term in query_terms))


def _normalize_query_type(query_type: Optional[str]) -> str:
    if query_type in ("concept", "personal_recent"):
        return query_type
    return "structured"


def _annotate_selected_candidate(
    candidate: dict,
    *,
    focus_id: str,
    sub_query_id: str,
    score: float = 0.0,
    rank: int = 1,
) -> dict:
    out = dict(candidate)
    url = out.get("url", "")
    out["selected_focus_ids"] = _merge_unique_strs(
        list(out.get("selected_focus_ids") or []),
        [focus_id] if focus_id else [],
    )
    out["selected_sub_query_ids"] = _merge_unique_strs(
        list(out.get("selected_sub_query_ids") or []),
        [sub_query_id] if sub_query_id else [],
    )
    out["selected_feishu_urls"] = _merge_unique_strs(
        list(out.get("selected_feishu_urls") or []),
        [url] if url else [],
    )
    out["cross_query_hit_count"] = max(1, int(out.get("cross_query_hit_count", 0) or 0))
    out["best_api_rank"] = min(int(out.get("best_api_rank", rank) or rank), rank)
    out["first_seen_order"] = int(out.get("first_seen_order", rank) or rank)
    return out


def _merge_candidate_lineage(base: dict, extra: dict) -> dict:
    merged = dict(base)
    merged["selected_focus_ids"] = _merge_unique_strs(
        list(base.get("selected_focus_ids") or []),
        list(extra.get("selected_focus_ids") or []),
    )
    merged["selected_sub_query_ids"] = _merge_unique_strs(
        list(base.get("selected_sub_query_ids") or []),
        list(extra.get("selected_sub_query_ids") or []),
    )
    merged["selected_feishu_urls"] = _merge_unique_strs(
        list(base.get("selected_feishu_urls") or []),
        list(extra.get("selected_feishu_urls") or []),
    )
    merged["selected_query_types"] = sorted(
        set(base.get("selected_query_types") or []) | set(extra.get("selected_query_types") or [])
    )
    merged["cross_query_hit_count"] = int(base.get("cross_query_hit_count", 1) or 1) + int(extra.get("cross_query_hit_count", 1) or 1)
    merged["best_api_rank"] = min(int(base.get("best_api_rank", 10**9) or 10**9), int(extra.get("best_api_rank", 10**9) or 10**9))
    merged["first_seen_order"] = min(int(base.get("first_seen_order", 10**9) or 10**9), int(extra.get("first_seen_order", 10**9) or 10**9))
    return merged


def _normalize_table_candidate(candidate: dict, *, source: str) -> dict:
    normalized = dict(candidate)
    normalized["_source"] = normalized.get("_source") or source
    normalized["_sources"] = _merge_unique_strs(
        list(normalized.get("_sources") or []),
        [source],
    )
    return normalized


def _is_non_evidence_table_candidate(candidate: dict) -> bool:
    """Return true only for explicitly registered system-only table apps."""
    title = str(candidate.get("title") or candidate.get("name") or "").strip().casefold()
    return title in NON_EVIDENCE_BITABLE_TITLES


def _table_candidate_key(candidate: dict) -> str:
    doc_type = str(candidate.get("doc_type", "") or "").upper()
    token = (
        candidate.get("id")
        or candidate.get("document_id")
        or candidate.get("doc_id")
        or ""
    )
    url = candidate.get("url") or ""
    if token:
        return f"{doc_type}:{token}"
    if url:
        return f"{doc_type}:{url}"
    return ""


def _dedupe_table_candidates(
    table_result: Dict[str, Any],
    *,
    direct_limit: int = TABLE_SEARCH_ROUTE_TOP_K,
    wiki_limit: int = TABLE_SEARCH_ROUTE_TOP_K,
) -> List[dict]:
    items = list(table_result.get("items") or [])
    debug = table_result.get("_debug") or {}
    direct_count = int(debug.get("direct_count", 0) or 0)
    wiki_count = int(debug.get("wiki_table_count", 0) or 0)

    direct_items = [
        _normalize_table_candidate(item, source="direct")
        for item in items[:direct_count]
        if not _is_non_evidence_table_candidate(item)
    ][:direct_limit]
    wiki_items = [
        _normalize_table_candidate(item, source="wiki")
        for item in items[direct_count : direct_count + wiki_count]
        if not _is_non_evidence_table_candidate(item)
    ][:wiki_limit]

    merged_by_key: Dict[str, dict] = {}
    ordered_keys: List[str] = []
    for candidate in direct_items + wiki_items:
        key = _table_candidate_key(candidate)
        if not key:
            continue
        existing = merged_by_key.get(key)
        if existing is None:
            merged_by_key[key] = dict(candidate)
            ordered_keys.append(key)
            continue

        merged = dict(existing)
        merged["title"] = merged.get("title") or candidate.get("title", "")
        merged["url"] = merged.get("url") or candidate.get("url", "")
        merged["doc_type"] = merged.get("doc_type") or candidate.get("doc_type", "")
        merged["node_id"] = merged.get("node_id") or candidate.get("node_id", "")
        merged["space_id"] = merged.get("space_id") or candidate.get("space_id", "")
        merged["_source"] = "direct" if "direct" in merged.get("_sources", []) else merged.get("_source", "")
        merged["_sources"] = _merge_unique_strs(
            list(existing.get("_sources") or []),
            list(candidate.get("_sources") or []),
        )
        merged_by_key[key] = merged
    return [merged_by_key[key] for key in ordered_keys]


def _build_sheet_structure_candidates(parent: dict, children: List[dict]) -> List[dict]:
    parent_title = str(parent.get("title", "") or "").strip()
    parent_source = parent.get("_source", "")
    docs: List[dict] = []
    for child in children:
        title = str(child.get("title", "") or child.get("sheet_title", "") or "").strip()
        row_count = child.get("row_count")
        col_count = child.get("column_count")
        summary_bits = []
        if row_count is not None:
            summary_bits.append(f"Rows: {row_count}")
        if col_count is not None:
            summary_bits.append(f"Cols: {col_count}")
        docs.append(
            {
                "id": child.get("sheet_id", ""),
                "title": f"{parent_title} / {title}" if parent_title and title else (title or parent_title),
                "summary": " | ".join(summary_bits),
                "doc_type": "WORKSHEET",
                "_source": parent_source,
                "_child_title": title,
                "_parent_title": parent_title,
                "_parent_id": parent.get("id", ""),
            }
        )
    return docs


def _build_bitable_structure_candidates(parent: dict, children: List[dict]) -> List[dict]:
    parent_title = str(parent.get("title", "") or "").strip()
    parent_source = parent.get("_source", "")
    docs: List[dict] = []
    for child in children:
        title = str(child.get("name", "") or child.get("title", "") or "").strip()
        docs.append(
            {
                "id": child.get("table_id", ""),
                "title": f"{parent_title} / {title}" if parent_title and title else (title or parent_title),
                "summary": "",
                "doc_type": "BITABLE_TABLE",
                "_source": parent_source,
                "_child_title": title,
                "_parent_title": parent_title,
                "_parent_id": parent.get("id", ""),
            }
        )
    return docs


async def _fetch_table_structure(
    parent: dict,
    *,
    token: str,
    table_pool: "Optional[object]" = None,
) -> List[dict]:
    started = time.perf_counter()
    doc_type = str(parent.get("doc_type", "") or "").upper()
    parent_id = parent.get("id", "")
    if not parent_id:
        return []

    if doc_type == "SHEET":
        if table_pool is not None and hasattr(table_pool, "get_sheet_structure"):
            rows = await table_pool.get_sheet_structure(parent)
            logger.debug(
                "TIMING feishu.table_structure kind=sheet parent=%s count=%d ms=%.1f cache=pool",
                parent.get("id", "")[:16],
                len(rows),
                _elapsed_ms(started),
            )
            return rows
        from feishu_read_tool.client import FeishuClient

        client = FeishuClient(user_access_token=token)
        tabs = await asyncio.to_thread(client.query_all_sheets_metadata, parent_id)
        rows = [
            t for t in tabs
            if not t.get("hidden") and t.get("resource_type") == "sheet"
        ]
        logger.debug(
            "TIMING feishu.table_structure kind=sheet parent=%s count=%d ms=%.1f cache=miss",
            parent.get("id", "")[:16],
            len(rows),
            _elapsed_ms(started),
        )
        return rows

    if doc_type == "BITABLE":
        if table_pool is not None and hasattr(table_pool, "get_bitable_structure"):
            rows = await table_pool.get_bitable_structure(parent)
            logger.debug(
                "TIMING feishu.table_structure kind=bitable parent=%s count=%d ms=%.1f cache=pool",
                parent.get("id", "")[:16],
                len(rows),
                _elapsed_ms(started),
            )
            return rows
        from feishu_read_tool.basic_tool.bitable_read import list_tables

        rows = await asyncio.to_thread(list_tables, parent_id, token)
        logger.debug(
            "TIMING feishu.table_structure kind=bitable parent=%s count=%d ms=%.1f cache=miss",
            parent.get("id", "")[:16],
            len(rows),
            _elapsed_ms(started),
        )
        return rows

    return []


def _default_table_parent_score(rank: int) -> float:
    return max(0.0, 1.0 - (rank - 1) * 0.03)


def _fallback_select_doc_candidates(
    candidates: List[dict],
    *,
    query: str,
    query_type: str,
    focus_id: str,
    sub_query_id: str,
) -> List[dict]:
    query_type = _normalize_query_type(query_type)
    top_k = native_candidate_budget(query_type)
    ranked_with_score = sorted(
        [(_build_doc_candidate_bm25(query, c), c) for c in candidates],
        key=lambda pair: pair[0],
        reverse=True,
    )[:top_k]
    selected = []
    for rank, (local_score, candidate) in enumerate(ranked_with_score, start=1):
        selected.append(
            _annotate_selected_candidate(
                candidate,
                focus_id=focus_id,
                sub_query_id=sub_query_id,
                score=min(1.0, float(local_score)),
                rank=rank,
            )
        )
    return selected


def _select_doc_candidates_for_sub_query(
    candidates: List[dict],
    *,
    sub_query: str,
    focus_id: str,
    focus_text: str,
    sub_query_id: str,
    query_type: str,
) -> List[dict]:
    if not candidates:
        return []

    # Date-anchor content pre-filter: when user query has date intent, narrow
    # candidates to those whose title/summary contains the date before reading.
    # No-op if no date detected or zero candidates match (preserves recall).
    candidates = _filter_candidates_by_date_anchors(
        candidates, focus_text=focus_text, sub_query=sub_query, sub_query_id=sub_query_id,
    )
    if not candidates:
        return []

    # Preserve API order.  The only selection at this stage is the existing
    # bounded-I/O cap, never a local or model relevance score.
    top_k = native_candidate_budget(_normalize_query_type(query_type))

    if len(candidates) == 1:
        return [
            _annotate_selected_candidate(
                candidates[0],
                focus_id=focus_id,
                sub_query_id=sub_query_id,
                rank=1,
            )
        ]

    # 候选 ≤ top_k → 全要，按搜索 API 原始顺序保留。
    if len(candidates) <= top_k:
        return [
            _annotate_selected_candidate(
                cand, focus_id=focus_id, sub_query_id=sub_query_id,
                rank=rank_idx + 1,
            )
            for rank_idx, cand in enumerate(candidates)
        ]

    return [
        _annotate_selected_candidate(
            candidate, focus_id=focus_id, sub_query_id=sub_query_id, rank=rank,
        )
        for rank, candidate in enumerate(candidates[:top_k], start=1)
    ]


async def search_and_select_feishu_doc_candidates(
    query: str,
    *,
    token: str,
    time_filter: Optional[str] = None,
    page_size: int = 4,
    query_type: str = "structured",
    focus_id: str = "",
    focus_text: str = "",
    sub_query_id: str = "",
    owner_ids: Optional[List[str]] = None,
    executor: Optional[concurrent.futures.Executor] = None,
) -> List[dict]:
    """执行 Feishu doc 搜索并返回当前 sub_query 选中的候选文档。

    搜索策略：严格执行原始 API query。零候选 fallback 由 SourceAgent
    单独调度 profile-derived alias/expression query，不能在这里改写原 query。

    ``executor``: 源专属线程池；None 时走 asyncio 默认（向后兼容）。
    """
    started = time.perf_counter()
    query_type = _normalize_query_type(query_type)

    try:
        # Document recall has one authoritative route.  Wiki/Direct remain a
        # structured-table concern; mixing Wiki documents here duplicated I/O
        # and made route-level benchmark attribution ambiguous.
        result = await _exec_to_thread(
            executor, search_feishu_mcp_pure,
            query=query, count=page_size, uat=token, time_filter=time_filter,
            owner_ids=owner_ids,
        )
        doc_candidates = list(result.get("items", []) or [])
        for candidate in doc_candidates:
            candidate["search_route"] = "mcp_doc"
    except Exception as exc:
        logger.warning("feishu_retrieve primary doc search failed: %s", exc)
        doc_candidates = []

    logger.info(
        "feishu_retrieve: native_primary sub_query_id=%s candidates=%d",
        sub_query_id or "unknown", len(doc_candidates),
    )
    if not doc_candidates:
        logger.info(
            "TIMING feishu.doc_candidate_select sub_query_id=%s raw=0 selected=0 ms=%.1f",
            sub_query_id or "unknown", _elapsed_ms(started),
        )
        return []

    # Time post-filtering for non-MCP doc candidates.
    if time_filter:
        cutoff_timestamp = 0
        if time_filter.startswith(">"):
            date_str = time_filter[1:].strip()
            try:
                import datetime
                dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                cutoff_timestamp = dt.timestamp()
                # Widen left boundary: edit_time often doesn't reflect content time.
                # E.g. user asks for "2026-01-15 weekly report" → planner sets
                # ">2026-01-15", but the actual doc might have been edited earlier.
                # Subtract buffer; content-level date matching happens downstream.
                cutoff_timestamp -= TIME_FILTER_LEFT_BUFFER_DAYS * 86400
                logger.debug(
                    "feishu_retrieve: time_filter widened sub_query_id=%s original=%s effective_cutoff=%s (buffer=%dd)",
                    sub_query_id or "unknown", time_filter,
                    datetime.datetime.fromtimestamp(cutoff_timestamp).strftime("%Y-%m-%d"),
                    TIME_FILTER_LEFT_BUFFER_DAYS,
                )
            except ValueError:
                pass

        if cutoff_timestamp > 0:
            # 提取所有需要验证时间的非 MCP token
            tokens_to_check = []
            for cand in doc_candidates:
                if cand.get("_source") != "mcp":
                    tok = cand.get("id") or cand.get("document_id") or cand.get("doc_id") or ""
                    if tok:
                        tokens_to_check.append(tok)
            
            meta_map = {}
            if tokens_to_check:
                from feishu_read_tool.client import FeishuClient
                client = FeishuClient(user_access_token=token)
                meta_map = client.get_files_metadata(tokens_to_check)
                
            filtered_cands = []
            for cand in doc_candidates:
                if cand.get("_source") == "mcp":
                    filtered_cands.append(cand)
                else:
                    tok = cand.get("id") or cand.get("document_id") or cand.get("doc_id") or ""
                    meta = meta_map.get(tok, {})
                    edit_time_str = meta.get("edit_time", "0")
                    try:
                        edit_time = float(edit_time_str)
                    except (ValueError, TypeError):
                        edit_time = 0.0
                    
                    if edit_time >= cutoff_timestamp:
                        filtered_cands.append(cand)
                    else:
                        logger.debug("feishu_retrieve: filtered out doc candidate %s (edit_time: %s < %s)",
                                    tok[:12], edit_time, cutoff_timestamp)
                        
            doc_candidates = filtered_cands

    if not doc_candidates:
        logger.info(
            "TIMING feishu.doc_candidate_select sub_query_id=%s raw=0 selected=0 ms=%.1f",
            sub_query_id or "unknown", _elapsed_ms(started),
        )
        return []

    selected = _select_doc_candidates_for_sub_query(
        doc_candidates,
        sub_query=query,
        focus_id=focus_id,
        focus_text=focus_text or query,
        sub_query_id=sub_query_id,
        query_type=query_type,
    )
    for candidate in selected:
        candidate["selected_query_types"] = sorted(
            set(candidate.get("selected_query_types") or []) | {query_type}
        )
    logger.info(
        "TIMING feishu.doc_candidate_select sub_query_id=%s raw=%d selected=%d ms=%.1f",
        sub_query_id or "unknown",
        len(doc_candidates),
        len(selected),
        _elapsed_ms(started),
    )
    return selected


async def search_and_select_feishu_table_candidates(
    query: str,
    *,
    token: str,
    page_size: int = 3,
    query_type: str = "structured",
    focus_id: str = "",
    focus_text: str = "",
    sub_query_id: str = "",
    table_pool: "Optional[object]" = None,
    direct_limit: int = TABLE_SEARCH_ROUTE_TOP_K,
    wiki_limit: int = TABLE_SEARCH_ROUTE_TOP_K,
    owner_ids: Optional[List[str]] = None,
    executor: Optional[concurrent.futures.Executor] = None,
    child_rerank_enabled: bool = True,
    child_rerank_model: str = "qwen3-rerank",
    child_rerank_timeout_s: float = 5.0,
    child_rerank_top_n: int = TABLE_CHILD_READ_LIMIT,
    child_rerank_instruct: str = "",
) -> List[dict]:
    """执行 Feishu table 搜索，并对命中 parent 做有界子表下钻。

    流程：
    1. 原 query 搜索并按 API 结果顺序去重
    2. 在既有 page/route budget 内读取命中 parent 的子表结构
    3. child 数量大于 3 时，以 parent 为单位做一次 Qwen metadata rerank
    4. 每个 parent 最多返回 ``TABLE_CHILD_READ_LIMIT`` 个 child 给 reader

    ``executor``: 源专属线程池；None 时走 asyncio 默认。
    """
    started = time.perf_counter()
    query_type = _normalize_query_type(query_type)
    if query_type != "structured":
        return []

    preset = (os.getenv("RAG_PRESET", "balanced") or "balanced").strip().lower()
    is_lite = preset == "lite"
    default_route_limit = TABLE_NATIVE_ROUTE_TOP_K_LITE if is_lite else TABLE_NATIVE_ROUTE_TOP_K
    route_limit = min(default_route_limit, max(1, int(direct_limit or default_route_limit)),
                      max(1, int(wiki_limit or default_route_limit)))
    try:
        raw_tables = await _exec_to_thread(
            executor, search_feishu_tables,
            query=query, count=page_size, uat=token, owner_ids=owner_ids,
        )
        parent_candidates = _dedupe_table_candidates(
            raw_tables, direct_limit=route_limit, wiki_limit=route_limit,
        )
    except Exception as exc:
        logger.warning("feishu_retrieve primary table search failed: %s", exc)
        parent_candidates = []
    logger.info(
        "feishu_retrieve: table_native_primary sub_query_id=%s parents=%d",
        sub_query_id or "unknown", len(parent_candidates),
    )
    if not parent_candidates:
        logger.info(
            "TIMING feishu.table_parent_select sub_query_id=%s raw=0 selected=0 ms=%.1f",
            sub_query_id or "unknown", _elapsed_ms(started),
        )
        return []

    # ── Step 1: 并发 structure prefetch ──────────────────────
    async def _fetch_one(parent: dict) -> tuple:
        try:
            children = await _fetch_table_structure(parent, token=token, table_pool=table_pool)
            return (parent, children)
        except Exception as exc:
            logger.warning(
                "feishu_retrieve: structure fetch failed for parent=%s: %s",
                parent.get("title", ""), exc,
            )
            return (parent, [])

    fetch_results = await asyncio.gather(*[_fetch_one(p) for p in parent_candidates])

    # ── Step 2: 构建全局 child candidates（含 parent 归属）──
    all_child_candidates: List[dict] = []
    parent_by_child_id: Dict[str, dict] = {}   # child_id → parent
    children_by_parent: Dict[str, List[dict]] = {}  # parent_id → raw children

    for parent_rank, (parent, children) in enumerate(fetch_results, start=1):
        parent_id = parent.get("id", "")
        if not children:
            continue
        children_by_parent[parent_id] = children
        parent_doc_type = str(parent.get("doc_type", "") or "").upper()
        if parent_doc_type == "SHEET":
            child_cands = _build_sheet_structure_candidates(parent, children)
        else:
            child_cands = _build_bitable_structure_candidates(parent, children)
        for cc in child_cands:
            cc["_parent_rank"] = parent_rank
            all_child_candidates.append(cc)
            parent_by_child_id[cc["id"]] = parent

    if not all_child_candidates:
        logger.info(
            "TIMING feishu.table_parent_select sub_query_id=%s raw=%d selected=0 ms=%.1f",
            sub_query_id or "unknown", len(parent_candidates), _elapsed_ms(started),
        )
        return []

    # ── Step 3: 按 parent 做 query-aware、fail-open 的硬边界选择 ──
    from tools.core.table_child_rerank import select_table_children

    async def _rank_parent_children(parent: dict, raw_children: List[dict]) -> tuple[str, List[dict], dict]:
        parent_id = str(parent.get("id") or "")
        doc_type = str(parent.get("doc_type", "") or "").upper()
        child_candidates = (
            _build_sheet_structure_candidates(parent, raw_children)
            if doc_type == "SHEET"
            else _build_bitable_structure_candidates(parent, raw_children)
        )
        selected_children, selection = await _exec_to_thread(
            executor,
            select_table_children,
            query,
            child_candidates,
            top_n=child_rerank_top_n,
            enabled=child_rerank_enabled,
            model=child_rerank_model,
            timeout_s=child_rerank_timeout_s,
            instruct=child_rerank_instruct,
        )
        return parent_id, selected_children, selection

    ranked_parents = await asyncio.gather(*[
        _rank_parent_children(parent, children_by_parent.get(str(parent.get("id") or ""), []))
        for parent, _ in fetch_results if str(parent.get("id") or "") in children_by_parent
    ])
    children_selected_by_parent = {parent_id: children for parent_id, children, _ in ranked_parents}
    child_selection_by_parent = {parent_id: selection for parent_id, _, selection in ranked_parents}

    # ── Step 5: 组装 annotated parent 列表 ───────────────────
    selected: List[dict] = []
    for parent_rank, (parent, children) in enumerate(fetch_results, start=1):
        parent_id = parent.get("id", "")
        sel_children = children_selected_by_parent.get(parent_id, [])
        if not sel_children:
            continue

        parent_doc_type = str(parent.get("doc_type", "") or "").upper()
        if parent_doc_type == "SHEET":
            child_id_field = "selected_sheet_ids"
            child_title_field = "selected_sheet_titles"
        else:
            child_id_field = "selected_table_ids"
            child_title_field = "selected_table_titles"

        # parent score = 最高 child score
        annotated = _annotate_selected_candidate(
            parent,
            focus_id=focus_id,
            sub_query_id=sub_query_id,
            rank=parent_rank,
        )
        annotated["selected_query_types"] = sorted(
            set(annotated.get("selected_query_types") or []) | {query_type}
        )
        raw_children = children_by_parent.get(parent_id, [])
        annotated["table_structure_child_count"] = len(raw_children)
        annotated["table_selected_child_count"] = len(sel_children)
        annotated["table_child_overflow_count"] = max(0, len(raw_children) - len(sel_children))
        selection = child_selection_by_parent.get(parent_id, {"mode": "empty"})
        annotated["table_child_selection_mode"] = str(selection.get("mode") or "empty")
        annotated["table_child_rerank_called"] = bool(selection.get("provider_called", False))
        annotated["table_child_rerank_error_type"] = str(selection.get("error_type") or "")
        annotated["table_child_rerank_error"] = str(selection.get("error") or "")

        sel_ids = [cc["id"] for cc in sel_children if cc.get("id")]
        sel_titles = [cc.get("_child_title", "") for cc in sel_children]
        annotated[child_id_field] = _merge_unique_strs(
            list(annotated.get(child_id_field) or []), sel_ids,
        )
        annotated[child_title_field] = _merge_unique_strs(
            list(annotated.get(child_title_field) or []), sel_titles,
        )
        annotated["read_query"] = query
        selected.append(annotated)

    selected.sort(
        key=lambda c: (
            -int(c.get("cross_query_hit_count", 1) or 1),
            int(c.get("best_api_rank", 10**9) or 10**9),
            int(c.get("first_seen_order", 10**9) or 10**9),
            str(c.get("title", "") or ""),
        )
    )
    logger.info(
        "TIMING feishu.table_parent_select sub_query_id=%s raw=%d children_total=%d selected_parents=%d ms=%.1f",
        sub_query_id or "unknown",
        len(parent_candidates),
        len(all_child_candidates),
        len(selected),
        _elapsed_ms(started),
    )
    return selected


def _build_candidate_meta_lookup(candidates: List[dict]) -> Dict[str, Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    by_url: Dict[str, Dict[str, Any]] = {}
    by_title: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        meta = {
            "selected_focus_ids": list(candidate.get("selected_focus_ids") or []),
            "selected_sub_query_ids": list(candidate.get("selected_sub_query_ids") or []),
            "selected_feishu_urls": list(candidate.get("selected_feishu_urls") or []),
            "selected_query_types": list(candidate.get("selected_query_types") or []),
            "cross_query_hit_count": candidate.get("cross_query_hit_count", 1),
            "best_api_rank": candidate.get("best_api_rank", 10**9),
            "first_seen_order": candidate.get("first_seen_order", 10**9),
        }
        key = _candidate_key(candidate)
        if key:
            by_id[key] = meta
        if candidate.get("url"):
            by_url[candidate["url"]] = meta
        if candidate.get("title"):
            by_title[candidate["title"]] = meta
    return {"by_id": by_id, "by_url": by_url, "by_title": by_title}


def _candidate_meta_for_chunk_result(cr, lookup: Optional[Dict[str, Dict[str, Any]]]) -> Dict[str, Any]:
    if not lookup:
        return {}
    if cr.file_url and cr.file_url in lookup["by_url"]:
        return dict(lookup["by_url"][cr.file_url])
    if cr.file_name and cr.file_name in lookup["by_title"]:
        return dict(lookup["by_title"][cr.file_name])
    if cr.chunk_id and "_" in cr.chunk_id:
        doc_id = cr.chunk_id.rsplit("_", 1)[0]
        if doc_id in lookup["by_id"]:
            return dict(lookup["by_id"][doc_id])
    return {}


async def search_and_read_feishu_tables(
    query: str,
    *,
    token: str,
    query_type: str,
    page_size: int = 20,
    table_pool: "Optional[object]" = None,
    focus_id: str = "",
    focus_text: str = "",
    sub_query_id: str = "",
) -> List[UnifiedChunk]:
    """执行 Feishu table 搜索 + 读取；仅 structured 子查询启用。"""
    started = time.perf_counter()
    if _normalize_query_type(query_type) != "structured":
        return []

    try:
        table_candidates = await search_and_select_feishu_table_candidates(
            query,
            token=token,
            page_size=page_size,
            query_type=query_type,
            focus_id=focus_id,
            focus_text=focus_text or query,
            sub_query_id=sub_query_id,
            table_pool=table_pool,
        )
        if not table_candidates:
            return []

        if table_pool is not None:
            selected_ids = await table_pool.submit_and_read(table_candidates)
            chunks = await table_pool.wait_and_collect(selected_ids)
            logger.debug(
                "TIMING feishu.table_pipeline mode=pool selected=%d chunks=%d ms=%.1f",
                len(selected_ids),
                len(chunks),
                _elapsed_ms(started),
            )
            return chunks

        logger.warning("feishu_retrieve: table_pool is None, skipping table read")
        return []
    except Exception as e:
        logger.warning("feishu_retrieve table pipeline failed: %s", e)
        return []


# ── 主入口：Dispatcher ────────────────────────────────────────────────────────

async def feishu_retrieve(
    query: str,
    time_filter: Optional[str] = None,
    page_size: int = 20,
    uat: str = "",
    query_type: str = "structured",
    doc_pool: "Optional[object]" = None,
    table_pool: "Optional[object]" = None,
    doc_pool_timeout: Optional[float] = None,
    focus_id: str = "",
    focus_text: str = "",
    sub_query_id: str = "",
) -> List[UnifiedChunk]:
    """飞书多格式检索编排器。

    两阶段搜索：
      Phase A — 文档搜索（始终执行）：MCP + Wiki doc → doc_pool
      Phase B — 表格搜索（条件启动）：Direct + Wiki table → sheet/bitable readers
                仅在 query_type = "structured" 时启动

    Args:
        query:         单个子查询（已由 query_planner 拆解）
        time_filter:   时间过滤，如 "last_30_days"
        page_size:     搜索每路返回条数
        uat:           用户访问令牌（优先参数值，fallback env）
        query_type:    "structured" | "concept"
        doc_pool:      FeishuDocPool 实例（跨 task 共享 docx 缓存，必须提供）
        table_pool:    TablePool 实例（跨 task 共享 sheet/bitable 缓存，必须提供）
        doc_pool_timeout: DocPool.wait_and_collect 的 total_timeout（秒）。
                          None 时沿用 DocPool 默认值（12.0s）。由 SourceAgent
                          从 SourceAgentConfig.source_timeout_s 派生传入。
        focus_id:      当前 sub_query 对应的 focus_id（doc 候选选择层透传）
        focus_text:    当前 sub_query 对应的 focus 文本（预览评估用）
        sub_query_id:  当前运行时 sub_query 标识（light lineage 用）

    Returns:
        List[UnifiedChunk]，空列表表示无结果（不抛异常）
    """
    started = time.perf_counter()
    token = uat or FEISHU_UAT
    if not token:
        logger.warning("feishu_retrieve: FEISHU_UAT not configured")
        return []

    query_type = _normalize_query_type(query_type)
    enable_tables = query_type == "structured"

    # ═══════════════════════════════════════════════════════════════════════════
    # Phase A: 文档搜索（MCP + Wiki doc）— 始终执行
    # ═══════════════════════════════════════════════════════════════════════════
    doc_chunks: List[UnifiedChunk] = []

    doc_candidates = await search_and_select_feishu_doc_candidates(
        query,
        token=token,
        time_filter=time_filter,
        page_size=page_size,
        query_type=query_type,
        focus_id=focus_id,
        focus_text=focus_text or query,
        sub_query_id=sub_query_id,
    )

    # ═══════════════════════════════════════════════════════════════════════════
    # Phase A (DocPool 读取+评分) ∥ Phase B (Table 搜索+读取) 全并行编排
    #
    # 两路通过 asyncio.create_task 完全重叠执行：
    #   - doc_pool.submit_and_read → wait_and_collect      （Phase A 读取段）
    #   - search_feishu_tables → TablePool → sheet/bitable  （Phase B 全流程）
    # 最后 await table_read_task 收割，大概率已完成（Phase A 是瓶颈）。
    #
    # 历史（[P2.7] 之前）：Phase B 串行排在 Phase A 后面，平均多 6-8s；
    # 现在 table 任务先 create_task 抛出去，再去 await DocPool，两路自然重叠。
    # ═══════════════════════════════════════════════════════════════════════════

    # 启动 table search+read 的完整后台任务
    table_read_task = None
    if enable_tables:
        table_read_task = asyncio.create_task(
            search_and_read_feishu_tables(
                query,
                token=token,
                query_type=query_type,
                page_size=page_size,
                table_pool=table_pool,
                focus_id=focus_id,
                focus_text=focus_text or query,
                sub_query_id=sub_query_id,
            )
        )

    # DocPool 读取（与 table_read_task 并行）
    if doc_candidates:
        if doc_pool is not None:
            try:
                selected_ids = await doc_pool.submit_and_read(
                    doc_candidates, query, query_type=query_type,
                )
                ws_kwargs = {}
                if doc_pool_timeout is not None:
                    ws_kwargs["total_timeout"] = doc_pool_timeout
                doc_chunks = await doc_pool.wait_and_collect(
                    selected_ids, **ws_kwargs,
                )
                logger.debug(
                    "feishu_retrieve [pool]: query=%.30r → %d raw doc chunks (timeout=%s)",
                    query, len(doc_chunks),
                    f"{doc_pool_timeout:.1f}s" if doc_pool_timeout else "default",
                )
            except Exception as e:
                logger.warning("feishu_retrieve doc_pool failed: %s", e)

    # 收割 table 结果（大概率已完成，因为与 DocPool 并行）
    table_chunks: List[UnifiedChunk] = []
    if table_read_task is not None:
        table_chunks = await table_read_task

    all_chunks = doc_chunks + table_chunks
    logger.debug(
        "feishu_retrieve: total %d chunks (doc=%d table=%d) query_type=%s",
        len(all_chunks), len(doc_chunks), len(table_chunks), query_type,
    )
    logger.info(
        "TIMING feishu.retrieve query_type=%s doc_chunks=%d table_chunks=%d total=%d ms=%.1f",
        query_type,
        len(doc_chunks),
        len(table_chunks),
        len(all_chunks),
        _elapsed_ms(started),
    )
    return all_chunks
