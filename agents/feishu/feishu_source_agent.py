"""FeishuSourceAgent v3 — 飞书源适配器（doc/table 独立流水线 + 结构感知 prefilter）。

架构要点：
- plan_only LLM 产出 sub_queries（query + query_type + time_filter）
- _execute_batch 覆写基类：doc 和 table 独立流水线，互不等待
  · doc: search → bounded native candidates → doc_pool.submit（fire-and-forget）
  · table: search → structure prefetch → enriched prefilter → cache child select → table_pool.submit
- 每个 sub_query 并行等待 doc + table chunks → token-aware evidence pool
- eval_only LLM 最终打 tier + relevance
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from tools.feishu.feishu_retrieve import (
    TABLE_CHILD_READ_LIMIT,
    search_and_read_feishu_tables,
    search_and_select_feishu_doc_candidates,
    search_and_select_feishu_table_candidates,
    _table_candidate_key,
)
from tools.core.models import ChunkEntry, SourceAgentConfig, UnifiedChunk
from tools.core.chunk_preview import build_chunk_preview
from tools.core.source_profiles import get_profile
from tools.core.source_query_config import SOURCE_QUERY_RULES
from tools.core.evidence_role_funnel import current as current_role_funnel

from agents.source_agent_base import Focus, SourceAgent, SubQuery
from google.adk.agents.llm_agent import LlmAgent

logger = logging.getLogger(__name__)

_FEISHU_ID_PREFIX = "feishu:"


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def _doc_collect_timeout(preset: str, source_timeout_s: float) -> float:
    """Return the per-document consumer deadline for the active preset."""
    if preset == "lite":
        return 6.0
    if preset == "deep_dive":
        return min(35.0, max(20.0, source_timeout_s * 0.70))
    return min(18.0, max(12.0, source_timeout_s * 0.35))


def _metadata_overlap(query: str, value: str) -> float:
    """Cheap audit signal only; never a parent filtering decision."""
    q = {item.casefold() for item in re.findall(r"[\w.-]+", query or "", flags=re.UNICODE) if len(item) > 1}
    v = {item.casefold() for item in re.findall(r"[\w.-]+", value or "", flags=re.UNICODE) if len(item) > 1}
    return round(len(q & v) / max(1, len(q)), 4)


def _strip_feishu_prefix(s: str) -> str:
    if not s:
        return s
    return s[len(_FEISHU_ID_PREFIX):] if s.startswith(_FEISHU_ID_PREFIX) else s


def _normalize_query_type(qt: Optional[str]) -> str:
    if qt in ("concept", "personal_recent"):
        return qt
    return "structured"


def _get_env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_env_float(name: str, default: float) -> float:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _merge_unique_strs(*values: List[str]) -> List[str]:
    merged: List[str] = []
    seen = set()
    for items in values:
        for item in items or []:
            if not item or item in seen:
                continue
            seen.add(item)
            merged.append(item)
    return merged


# ── chunk 转换 ────────────────────────────────────────────────────


def _unified_to_chunk_entry(
    uc: UnifiedChunk, sub_query: str, target_focus: str, sub_query_id: str = "",
) -> ChunkEntry:
    meta = uc.metadata or {}
    doc_id = (
        _strip_feishu_prefix(meta.get("doc_token") or "")
        or _strip_feishu_prefix(meta.get("doc_id") or "")
        or _strip_feishu_prefix(uc.source_url or "")
        or _strip_feishu_prefix(uc.source_name or "")
        or "unknown"
    )
    node_id = (
        meta.get("block_id")
        or meta.get("chunk_id")
        or meta.get("row_id")
        or meta.get("section_id")
        or meta.get("table_id")
        or hashlib.md5((uc.text or "").encode("utf-8")).hexdigest()[:10]
    )
    cid = f"feishu:{doc_id}:{node_id}"
    merged_meta = {
        **meta,
        "source_name": uc.source_name,
        "source_url": uc.source_url,
        "heading_path": uc.heading_path,
        "source_tool": uc.source_tool or "feishu",
        "focus_ids": sorted(
            set(meta.get("focus_ids") or meta.get("selected_focus_ids") or []) | {target_focus}
        ),
        "sub_query_ids": sorted(
            set(meta.get("sub_query_ids") or meta.get("selected_sub_query_ids") or [])
            | ({sub_query_id} if sub_query_id else set())
        ),
        "feishu_urls": sorted(
            set(meta.get("feishu_urls") or meta.get("selected_feishu_urls") or [])
        ),
        "selected_query_types": sorted(
            set(meta.get("selected_query_types") or [])
        ),
    }
    return ChunkEntry(
        id=cid, source="feishu", content=uc.text or "",
        preview_content=build_chunk_preview(uc.text or "", merged_meta),
        metadata=merged_meta, score=uc.score,
        sub_query=sub_query, target_focuses={target_focus}, tier=None,
    )


def _preserve_api_order_chunks(
    query: str, focus_text: str, query_type: str,
    uc_list: List[UnifiedChunk], top_n: int,
) -> List[UnifiedChunk]:
    started = time.perf_counter()
    if not uc_list:
        return []
    # The bounded readers have already applied API/page budgets.  Preserve all
    # resulting chunks for token-aware evidence evaluation; do not score or
    # truncate them here.
    logger.info(
        "TIMING feishu.chunk_bucket mode=api_order query=%.24r input=%d selected=%d ms=%.1f",
        query, len(uc_list), len(uc_list), _elapsed_ms(started),
    )
    return list(uc_list)


# ── sub_query 后处理（sanitize）────────────────────────────────────

_FEISHU_STRUCTURED_FOCUS_HINTS = (
    "名单", "列表", "清单", "花费", "费用", "预算", "roas", "gmv",
    "销售", "销量", "订单", "佣金", "合作伙伴", "进度", "状态", "排期",
    "交付", "里程碑", "任务", "报表", "数据", "渠道",
)
_FEISHU_CONCEPT_FOCUS_HINTS = (
    "原因", "背景", "策略", "方案", "建议", "改进", "优化", "痛点",
    "对比", "差异", "现状", "逻辑", "模型", "定位", "方法",
)
_FEISHU_GENERIC_QUERY_TOKENS = {
    "运营", "策略", "市场", "计划", "管理", "业务", "项目", "工作", "内容", "合作", "情况", "方案",
}


def _classify_focus_mode(text: str) -> str:
    lowered = (text or "").lower()
    s = sum(1 for h in _FEISHU_STRUCTURED_FOCUS_HINTS if h in lowered)
    c = sum(1 for h in _FEISHU_CONCEPT_FOCUS_HINTS if h in lowered)
    if s > 0 and c > 0:
        return "mixed"
    if s > 0:
        return "structured"
    if c > 0:
        return "concept"
    return "unknown"


def _sanitize_feishu_sub_queries(
    focuses: List[Focus], sub_queries: List[SubQuery],
) -> List[SubQuery]:
    if not sub_queries:
        return []
    focus_lookup = {f.focus_id: f.text for f in focuses}
    grouped: Dict[str, List[SubQuery]] = defaultdict(list)
    for sq in sub_queries:
        grouped[sq.target_focus].append(sq)
    sanitized: List[SubQuery] = []
    for focus in focuses:
        items = grouped.get(focus.focus_id, [])
        if not items:
            continue
        mode = _classify_focus_mode(focus.text)
        effective_type = "concept" if mode == "concept" else "structured"
        # 一个 focus 可以有多条不同证据 intent 的并行 query。只做稳定去重，
        # 不再用 source 层规则压掉 planner 已明确规划的 raw/implementation/timeline 证据面。
        kept: List[SubQuery] = []
        seen_queries: Set[tuple[str, str]] = set()
        for sq in items:
            key = (str(sq.query_type or "").strip().casefold(), str(sq.query or "").strip().casefold())
            if not key[1] or key in seen_queries:
                continue
            seen_queries.add(key)
            kept.append(sq)
        for sq in kept:
            sq.query_type = sq.query_type or effective_type
        logger.info(
            "[feishu] plan sanitize focus=%s mode=%s before=%d after=%d qtypes=%s queries=%s",
            focus.focus_id, mode, len(items), len(kept),
            [sq.query_type for sq in kept], [sq.query for sq in kept],
        )
        sanitized.extend(kept)
    return sanitized


# ── doc/table merge 辅助 ──────────────────────────────────────────


def _candidate_doc_key(c: dict) -> str:
    return c.get("id") or c.get("document_id") or c.get("doc_id") or c.get("url") or ""


def _original_surface_fallback_query(
    sq: SubQuery,
    *,
    primary_doc_candidate_count: int,
    primary_table_candidate_count: int,
    current_doc_candidates: List[dict],
    current_table_candidates: List[dict],
) -> str:
    """Return one original-surface probe only after both primary routes miss."""
    if (
        primary_doc_candidate_count != 0
        or primary_table_candidate_count != 0
        or current_doc_candidates
        or current_table_candidates
    ):
        return ""
    return next(
        (
            variant for variant in dict.fromkeys(sq.variants)
            if sq.variant_origins.get(variant) == "original_surface"
        ),
        "",
    )


def _candidate_order_key(candidate: dict) -> tuple:
    """Stable bounded-I/O order: repeated API hits, then original API rank."""
    return (
        -int(candidate.get("cross_query_hit_count", 1) or 1),
        int(candidate.get("best_api_rank", 10**9) or 10**9),
        int(candidate.get("first_seen_order", 10**9) or 10**9),
        str(candidate.get("title", "") or ""),
    )


def _merge_candidate_order(existing: dict, candidate: dict) -> None:
    existing["cross_query_hit_count"] = (
        int(existing.get("cross_query_hit_count", 1) or 1)
        + int(candidate.get("cross_query_hit_count", 1) or 1)
    )
    existing["best_api_rank"] = min(
        int(existing.get("best_api_rank", 10**9) or 10**9),
        int(candidate.get("best_api_rank", 10**9) or 10**9),
    )
    existing["first_seen_order"] = min(
        int(existing.get("first_seen_order", 10**9) or 10**9),
        int(candidate.get("first_seen_order", 10**9) or 10**9),
    )


def _merge_doc_demands(batches: List[dict]) -> tuple:
    """跨 sq 合并 doc candidates，返回 (merged_list, {sq_id: [doc_ids]})。"""
    merged_by_doc: Dict[str, dict] = {}
    doc_ids_by_sq: Dict[str, List[str]] = {}
    # 记录每个 doc 在哪些 sq 里出现（用于 multi-hit 判断）
    sq_hit_count: Dict[str, int] = {}
    # 记录每个 sq 的 rank-1 doc（最高分）
    top1_by_sq: Dict[str, str] = {}

    for batch in batches:
        sq = batch["sq"]
        selected_ids: List[str] = []
        sq_docs = sorted(batch.get("doc_candidates") or [], key=_candidate_order_key)
        for rank_in_sq, c in enumerate(sq_docs):
            key = _candidate_doc_key(c)
            if not key:
                continue
            if key not in [k for k in selected_ids]:
                selected_ids.append(key)
            sq_hit_count[key] = sq_hit_count.get(key, 0) + 1
            if rank_in_sq == 0:
                top1_by_sq[sq.sub_query_id] = key
            if key not in merged_by_doc:
                merged_by_doc[key] = dict(c)
            else:
                existing = merged_by_doc[key]
                _merge_candidate_order(existing, c)
                for lk in ("selected_focus_ids", "selected_sub_query_ids", "selected_feishu_urls", "selected_query_types"):
                    existing[lk] = sorted(set(existing.get(lk) or []) | set(c.get(lk) or []))
        doc_ids_by_sq[sq.sub_query_id] = selected_ids

    merged_list = sorted(
        merged_by_doc.values(),
        key=_candidate_order_key,
    )
    # 把 sq_hit_count 和 top1 信号写回 merged_list，供 route_hint 决策使用
    top1_keys = set(top1_by_sq.values())
    for c in merged_list:
        key = _candidate_doc_key(c)
        c["_sq_hit_count"] = sq_hit_count.get(key, 1)
        c["_is_top1"] = key in top1_keys
    return merged_list, doc_ids_by_sq


def _assign_doc_route_hints(doc_list: List[dict], *, min_block: int = 2) -> None:
    """Route normal Docx reads to Block API; MCP is discovery/fallback only.

    MCP remains the only document *search* path. Its text reader, however,
    can omit structured Block content such as the tables required by P3. A
    document's retrieval rank must not choose a lossy reader.  ``min_block``
    remains accepted for backwards-compatible callers but has no role in the
    correctness contract.
    """
    del min_block
    block_keys = {
        _candidate_doc_key(c)
        for c in doc_list
        if "personal_recent" not in (c.get("selected_query_types") or [])
    }

    block_count = 0
    mcp_count = 0
    for c in doc_list:
        key = _candidate_doc_key(c)
        if key in block_keys:
            c["route_hint"] = "block_api"
            block_count += 1
        else:
            c["route_hint"] = "mcp"
            mcp_count += 1
    logger.debug("[feishu] doc route_hint block=%d mcp=%d total=%d",
                block_count, mcp_count, len(doc_list))


def _merge_table_demands(batches: List[dict]) -> tuple:
    """跨 sq 合并 table candidates，返回 (merged_list, {sq_id: [table_tokens]})。"""
    merged_by_parent: Dict[str, dict] = {}
    table_ids_by_sq: Dict[str, List[str]] = {}
    for batch in batches:
        sq = batch["sq"]
        selected_ids: List[str] = []
        for c in batch.get("table_candidates") or []:
            key = _candidate_doc_key(c)
            if not key:
                continue
            if key not in selected_ids:
                selected_ids.append(key)
            if key not in merged_by_parent:
                merged_by_parent[key] = dict(c)
            else:
                existing = merged_by_parent[key]
                _merge_candidate_order(existing, c)
                for lk in ("selected_focus_ids", "selected_sub_query_ids", "selected_feishu_urls",
                            "selected_query_types"):
                    existing[lk] = _merge_unique_strs(
                        list(existing.get(lk) or []), list(c.get(lk) or []),
                    )
                for lk in ("selected_sheet_ids", "selected_table_ids"):
                    combined = _merge_unique_strs(
                        list(existing.get(lk) or []), list(c.get(lk) or []),
                    )
                    existing[lk] = combined[:TABLE_CHILD_READ_LIMIT]
                for lk in ("selected_sheet_titles", "selected_table_titles"):
                    combined = _merge_unique_strs(
                        list(existing.get(lk) or []), list(c.get(lk) or []),
                    )
                    existing[lk] = combined[:TABLE_CHILD_READ_LIMIT]
                existing["table_selected_child_count"] = max(
                    len(existing.get("selected_sheet_ids") or []),
                    len(existing.get("selected_table_ids") or []),
                )
                existing["table_structure_child_count"] = max(
                    int(existing.get("table_structure_child_count", 0) or 0),
                    int(c.get("table_structure_child_count", 0) or 0),
                )
                existing["table_child_overflow_count"] = max(
                    0,
                    int(existing.get("table_structure_child_count", 0) or 0)
                    - int(existing.get("table_selected_child_count", 0) or 0),
                )
                existing["read_query"] = existing.get("read_query") or c.get("read_query", "")
        table_ids_by_sq[sq.sub_query_id] = selected_ids
    merged_list = sorted(
        merged_by_parent.values(),
        key=_candidate_order_key,
    )
    return merged_list, table_ids_by_sq


# ── table 辅助：结构感知 prefilter + cache child select ──────────


def _build_table_parent_summary(parent: dict) -> str:
    title = parent.get("title") or parent.get("name") or "Unknown"
    doc_type = parent.get("doc_type") or "TABLE"
    return f"[{doc_type}] {title}"


def _enrich_parents_with_structure(
    parents: List[dict], table_pool: Optional[object],
) -> List[dict]:
    """把 table_pool 缓存的子结构名称注入 parent summary，供预览评估使用。"""
    if table_pool is None:
        return parents
    enriched = []
    for parent in parents:
        doc_type = str(parent.get("doc_type", "") or "").upper()
        token = parent.get("id") or parent.get("document_id") or ""
        children: List[dict] = []
        if doc_type == "SHEET" and hasattr(table_pool, "_sheet_struct_cache"):
            children = list(table_pool._sheet_struct_cache.get(token, []))
        elif doc_type == "BITABLE" and hasattr(table_pool, "_bitable_struct_cache"):
            children = list(table_pool._bitable_struct_cache.get(token, []))
        if not children:
            enriched.append(parent)
            continue
        names = []
        for ch in children[:8]:
            n = ch.get("title") or ch.get("name") or ch.get("sheet_title") or ""
            if n:
                names.append(str(n).strip())
        if len(children) > 8:
            names.append(f"...+{len(children)-8}more")
        out = dict(parent)
        base = out.get("summary") or _build_table_parent_summary(out)
        label = "Tabs" if doc_type == "SHEET" else "Tables"
        out["summary"] = f"{base} | {label}: [{', '.join(names)}]" if names else base
        enriched.append(out)
    return enriched


def _select_children_from_cache(
    candidates: List[dict], *, table_pool: Optional[object],
    focus_id: str, sub_query_id: str, query_type: str,
) -> List[dict]:
    """从结构缓存直接提取 selected_sheet_ids / selected_table_ids。"""
    result = []
    for parent in candidates:
        doc_type = str(parent.get("doc_type", "") or "").upper()
        token = parent.get("id") or parent.get("document_id") or ""
        children: List[dict] = []
        if table_pool is not None:
            if doc_type == "SHEET" and hasattr(table_pool, "_sheet_struct_cache"):
                children = list(table_pool._sheet_struct_cache.get(token, []))
            elif doc_type == "BITABLE" and hasattr(table_pool, "_bitable_struct_cache"):
                children = list(table_pool._bitable_struct_cache.get(token, []))
        out = dict(parent)
        out["table_structure_child_count"] = len(children)
        out["selected_focus_ids"] = _merge_unique_strs(list(out.get("selected_focus_ids") or []), [focus_id] if focus_id else [])
        out["selected_sub_query_ids"] = _merge_unique_strs(list(out.get("selected_sub_query_ids") or []), [sub_query_id] if sub_query_id else [])
        out["selected_query_types"] = sorted(set(out.get("selected_query_types") or []) | {query_type})
        if not children:
            out["table_selected_child_count"] = 0
            result.append(out)
            continue
        # Defensive bound for the legacy cache helper.  The primary selector
        # already ranks children by query; this path preserves API-order
        # fail-open behavior without allowing an unbounded parent expansion.
        sel = children[:TABLE_CHILD_READ_LIMIT]
        out["table_selected_child_count"] = len(sel)
        if doc_type == "SHEET":
            out["selected_sheet_ids"] = _merge_unique_strs(list(out.get("selected_sheet_ids") or []),
                                                            [c.get("sheet_id", "") for c in sel if c.get("sheet_id")])
            out["selected_sheet_titles"] = _merge_unique_strs(list(out.get("selected_sheet_titles") or []),
                                                               [c.get("title") or c.get("sheet_title") or "" for c in sel])
        else:
            out["selected_table_ids"] = _merge_unique_strs(list(out.get("selected_table_ids") or []),
                                                            [c.get("table_id", "") for c in sel if c.get("table_id")])
            out["selected_table_titles"] = _merge_unique_strs(list(out.get("selected_table_titles") or []),
                                                               [c.get("name") or c.get("title") or "" for c in sel])
        out["read_query"] = parent.get("read_query") or ""
        result.append(out)
    return result


# ── table trim ────────────────────────────────────────────────────


def _trim_table_parents(batches: List[dict], target: int = 6, min_per_sq: int = 2) -> List[dict]:
    """跨 sq 去重 + 截断到 target 张。

    优先级（高→低）：
    1. 每个 structured sq 的头部 min_per_sq 张（保底）
    2. 多个 sq 共命中的 parent（multi-hit）
    3. API 原始返回顺序靠前的 parent
    4. low_yield（structure 子表多但 selected 少）的 parent 降权排后
    """
    structured = [b for b in batches if b.get("query_type") == "structured" and b.get("table_candidates")]
    if not structured:
        return []

    # Step 1: 每个 sq 保底 min_per_sq 张
    keep_ids: set = set()
    for batch in structured:
        ordered = sorted(batch.get("table_candidates") or [], key=_candidate_order_key)
        for c in ordered[:min_per_sq]:
            key = _candidate_doc_key(c)
            if key:
                keep_ids.add(key)

    # Step 2: 合并所有 candidates，统计 multi-hit
    all_merged: Dict[str, dict] = {}
    hit_count: Dict[str, int] = {}
    for batch in structured:
        for c in batch.get("table_candidates") or []:
            key = _candidate_doc_key(c)
            if not key:
                continue
            hit_count[key] = hit_count.get(key, 0) + 1
            if key not in all_merged:
                all_merged[key] = c

    def _is_low_yield(c: dict) -> bool:
        """structure 子表多（>=6）但 selected 少（<=1）的表，认为是低产出。"""
        selected = int(c.get("table_selected_child_count", 0) or 0)
        structure = int(c.get("table_structure_child_count", 0) or 0)
        sq_hits = hit_count.get(_candidate_doc_key(c), 1)
        return sq_hits <= 1 and selected <= 1 and structure >= 6

    def _sort_key(c: dict) -> tuple:
        key = _candidate_doc_key(c)
        return (
            _is_low_yield(c),                                    # low_yield 排后
            -hit_count.get(key, 1),                              # multi-hit 优先
            int(c.get("best_api_rank", 10**9) or 10**9),
            int(c.get("first_seen_order", 10**9) or 10**9),
            c.get("title", ""),
        )

    ordered_all = sorted(all_merged.values(), key=_sort_key)

    # Step 3: 填满 target
    for c in ordered_all:
        if len(keep_ids) >= target:
            break
        key = _candidate_doc_key(c)
        if key:
            keep_ids.add(key)

    # 回写 batch
    for batch in structured:
        batch["table_candidates"] = [c for c in (batch.get("table_candidates") or []) if _candidate_doc_key(c) in keep_ids]
    return [c for c in ordered_all if _candidate_doc_key(c) in keep_ids]


# ══════════════════════════════════════════════════════════════════
# FeishuSourceAgent 类
# ══════════════════════════════════════════════════════════════════


class FeishuSourceAgent(SourceAgent):
    """飞书 SourceAgent v3：doc/table 独立流水线 + 结构感知 prefilter。"""

    source = "feishu"

    DEFAULT_EXECUTOR_WORKERS: int = 24   # V14: 16 → 24, 容纳 dual-path × 多 sub_query 并发

    def __init__(
        self,
        cfg: Optional[SourceAgentConfig] = None,
        uat: str = "",
        default_query_type: str = "structured",
        doc_pool: Optional[object] = None,
        table_pool: Optional[object] = None,
        preset: str = "balanced",
        executor_workers: Optional[int] = None,
    ):
        cfg = cfg or SourceAgentConfig()
        profile = get_profile("feishu")
        super().__init__(
            profile=profile, cfg=cfg, executor_workers=executor_workers,
        )
        self._uat = uat
        self._default_query_type = default_query_type
        self._doc_pool = doc_pool
        self._table_pool = table_pool
        self._preset = preset
        # 把源专属 executor 注入给 doc_pool / table_pool（如果有 _executor 字段）
        for pool in (doc_pool, table_pool):
            if pool is not None and hasattr(pool, "_executor"):
                if pool._executor is None:
                    pool._executor = self._executor

    async def drain_inflight(self, timeout_s: float = 10.0) -> int:
        """Include detached DocPool readers in the request cancellation gate."""
        own_remaining = await super().drain_inflight(timeout_s=timeout_s)
        pool_remaining = 0
        if self._doc_pool is not None and hasattr(self._doc_pool, "drain_inflight"):
            pool_remaining = await self._doc_pool.drain_inflight(timeout_s=timeout_s)
        table_remaining = 0
        if self._table_pool is not None and hasattr(self._table_pool, "drain_inflight"):
            table_remaining = await self._table_pool.drain_inflight(timeout_s=timeout_s)
        return own_remaining + pool_remaining + table_remaining

    def _build_plan_only_agent(self) -> Optional[LlmAgent]:
        from .feishu_retrieve_llm_build import build_feishu_plan_only_agent
        return build_feishu_plan_only_agent()

    def _build_eval_only_agent(self) -> LlmAgent:
        from .feishu_retrieve_llm_build import build_feishu_eval_only_agent
        return build_feishu_eval_only_agent(self.cfg.source_top_k)

    def _build_plan_eval_agent(self) -> Optional[LlmAgent]:
        if self.cfg.max_loops <= 1:
            return None
        from .feishu_retrieve_llm_build import build_feishu_plan_eval_agent
        return build_feishu_plan_eval_agent(self.cfg.source_top_k)

    # ── plan_only prompt 注入 ─────────────────────────────────
    def _source_query_rules_block(self) -> str:
        from tools.core.source_query_config import get_source_query_rules
        base_rules = get_source_query_rules("feishu")
        return (
            base_rules
            + "\n\n提醒：time_filter 仅相对时间用 "
            + "\"last_N_days\"，季度/月份等具体时间点请并入 query 本身或留 null。"
            + "\n"
            + "额外规划约束：\n"
            + "  · 默认每个 focus 只生成 1 条 sub_query；只有同一 focus 明确包含两个不同证据面时才允许第 2 条。\n"
            + "  · 不要为了'保险'补一个泛泛的陪跑 query。\n"
            + "  · concept query 必须保留 focus 中最核心的主题锚点词。"
        )

    def _post_process_sub_queries(self, focuses: List[Focus], sub_queries: List[SubQuery]) -> List[SubQuery]:
        sanitized = _sanitize_feishu_sub_queries(focuses, sub_queries)
        if len(sanitized) != len(sub_queries):
            logger.info("[feishu] plan sanitized total_sub_queries %d -> %d", len(sub_queries), len(sanitized))
        # v5 fallback alternatives are derived only from the bounded profile pack;
        # retrieval invokes them only after the primary API call returns zero.
        resolution = getattr(self, "_profile_resolution", None)
        for sq in sanitized:
            pack = resolution.fallback_pack if resolution is not None else None
            specs = pack.fallback_variant_specs(sq.query) if pack is not None else []
            existing_variants = list(sq.variants)
            existing_origins = dict(sq.variant_origins)
            existing_ids = dict(sq.variant_origin_ids)
            sq.variants = list(dict.fromkeys([
                *existing_variants,
                *(variant for variant, _kind, _item_id in specs),
            ]))
            sq.variant_origins = {
                **existing_origins,
                **{variant: kind for variant, kind, _item_id in specs},
            }
            sq.variant_origin_ids = {
                **existing_ids,
                **{variant: item_id for variant, _kind, item_id in specs if item_id > 0},
            }
        return sanitized

    def _enrich_sub_queries_with_variants(
        self, focuses: List[Focus], sub_queries: List[SubQuery],
    ) -> List[SubQuery]:
        """Legacy compatibility no-op; generic expansion is forbidden in v5."""
        return sub_queries

    # ── table ROI 日志 ────────────────────────────────────────
    def _final_selected_table_tokens(self, chunks: List[ChunkEntry]) -> Set[str]:
        tokens: Set[str] = set()
        for chunk in chunks:
            meta = dict(chunk.metadata or {})
            route = str(meta.get("route", "") or "").lower()
            if route in {"sheet_api", "bitable_api"} or "table_selected_child_count" in meta:
                token = _strip_feishu_prefix(str(meta.get("doc_token") or meta.get("doc_id") or ""))
                if token:
                    tokens.add(token)
        return tokens

    def _log_table_roi(self, chunks: List[ChunkEntry]) -> None:
        if self._table_pool is None or not hasattr(self._table_pool, "get_roi_rows"):
            return
        try:
            final_tokens = self._final_selected_table_tokens(chunks)
            rows = self._table_pool.get_roi_rows(final_selected_tokens=final_tokens)
        except Exception as exc:
            logger.warning("[feishu] table roi logging failed: %s", exc)
            return
        if not rows:
            return
        submitted = [r for r in rows if r.get("submit_selected")]
        final_sel = [r for r in rows if r.get("final_selected")]
        wasted = [r for r in submitted if not r.get("final_selected")]
        skipped = [r for r in rows if r.get("submit_skipped")]
        logger.debug(
            "[feishu] table roi summary total=%d submitted=%d final_selected=%d wasted=%d skipped=%d read_ms=%.1f read_chunks=%d",
            len(rows), len(submitted), len(final_sel), len(wasted), len(skipped),
            sum(float(r.get("read_ms", 0) or 0) for r in submitted),
            sum(int(r.get("read_chunks", 0) or 0) for r in submitted),
        )
        for r in rows:
            logger.debug(
                "[feishu] table roi token=%s title=%r doc_type=%s api_rank=%d "
                "selected_child_count=%d structure_child_count=%d child_ratio=%.3f "
                "read_ms=%.1f read_chunks=%d submit_selected=%s submit_skipped=%s final_selected=%s "
                "focus_ids=%s sub_query_ids=%s",
                str(r.get("token", ""))[:16], r.get("title", ""), r.get("doc_type", ""),
                int(r.get("best_api_rank", 10**9) or 10**9),
                int(r.get("selected_child_count", 0) or 0),
                int(r.get("structure_child_count", 0) or 0),
                float(r.get("selected_child_ratio", 0) or 0),
                float(r.get("read_ms", 0) or 0), int(r.get("read_chunks", 0) or 0),
                bool(r.get("submit_selected")), bool(r.get("submit_skipped")),
                bool(r.get("final_selected")),
                list(r.get("selected_focus_ids") or []),
                list(r.get("selected_sub_query_ids") or []),
            )

    async def _eval_only_finalize(self, focuses: List[Focus], chunk_pool: Dict[str, ChunkEntry]) -> List[ChunkEntry]:
        chunks = await super()._eval_only_finalize(focuses, chunk_pool)
        self._log_table_roi(chunks)
        return chunks

    # ── lazy user identity：只在 personal_recent 且未配置 open_id 时请求 ──
    async def _ensure_user_identity(self, sub_queries: List[SubQuery]) -> None:
        """按需获取 open_id：仅当 sub_queries 含 personal_recent 且未配置时发起 API 请求。"""
        has_personal_recent = any(
            (sq.query_type or self._default_query_type) == "personal_recent"
            for sq in sub_queries
        )
        if not has_personal_recent:
            return
        if getattr(self, "_current_open_id", None):
            return

        uat = self._uat
        if not uat:
            logger.warning("[feishu] personal_recent requires open_id but no UAT available")
            return

        try:
            from feishu_read_tool.client import _auth_headers
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://open.feishu.cn/open-apis/authen/v1/user_info",
                    headers=_auth_headers(uat),
                    timeout=5.0,
                )
                data = resp.json()
                if data.get("code") == 0:
                    self._current_open_id = data["data"].get("open_id")
                    logger.debug("[feishu] Lazy fetched open_id for personal_recent")
        except Exception as e:
            logger.warning("[feishu] Failed to fetch open_id for personal_recent: %s", e)

    # ── 批处理路径：doc/table 完全并行流水线 ─────────────────
    async def _execute_batch(
        self, sub_queries: List[SubQuery], chunk_pool: Dict[str, ChunkEntry], *, loop_n: int = 1,
    ) -> None:
        # Direct/Wiki table search dispatches through feishu_retrieve's async
        # executor helper rather than SourceAgent._to_thread. Give that helper
        # this request's registry so cancellation drain sees its real threads.
        from tools.feishu.feishu_retrieve import IO_FUTURE_REGISTRY
        IO_FUTURE_REGISTRY.set(self._inflight_futures)
        batch_started = time.perf_counter()
        await self._ensure_user_identity(sub_queries)
        if self._doc_pool is None:
            raise RuntimeError("FeishuSourceAgent requires doc_pool; ensure FEISHU_UAT is configured")

        doc_pool_timeout = max(8.0, self.cfg.source_timeout_s * 0.8)
        # 单 doc 超时必须小于 source 总预算，但不能短到把已命中的 Block API
        # 文档（实测 16s）在完整 chunk 化前丢弃。lite 继续优先时延；其他 preset
        # 使用 source budget 的受限比例，保留后续 eval 的时间。
        # A consumer deadline only controls waiting; the synchronous Block API
        # reader continues. Deep-dive leaves enough room for an already-started,
        # relevant document to finish and enter the current evidence pool.
        per_doc_timeout = _doc_collect_timeout(self._preset, self.cfg.source_timeout_s)

        # ── lite 模式：极简 MCP-only 路径（无 table）────────
        if self._preset == "lite":
            await self._execute_batch_lite(sub_queries, chunk_pool, doc_pool_timeout, batch_started)
            return

        # ── Step 1: doc / table 解耦双 track 并行 ────────────
        # 旧实现：_prepare_one 在 asyncio.gather(doc_task, table_task) 内等齐两者；
        # 所有 sq 的 prepare_one 又被外层 gather 等齐；结果是 doc submit 必须等到最慢的
        # table search + structure fetch + child read 全跑完（实测多压 1.5~3s）。
        # 新实现：doc track 走快路径——doc 候选一就绪立即 submit 到 doc_pool 触发 read；
        # table track 独立跑（仍需 cross-sq trim 所以 sq 之间需聚合），与 doc 不互等。

        prepare_started = time.perf_counter()

        # 公共上下文：sq → (query_type, focus_text, owner_ids)
        def _resolve_sq_ctx(sq: SubQuery) -> Tuple[str, str, Optional[List[str]]]:
            qt = sq.query_type or self._default_query_type
            if qt not in ("personal_recent", "structured", "concept"):
                qt = "concept"
            if self._preset == "lite" and qt == "structured":
                qt = "concept"
            focus_obj = self._current_focus_lookup.get(sq.target_focus)
            focus_text = focus_obj.text if focus_obj else sq.target_focus
            owner_ids = (
                [getattr(self, "_current_open_id", None)]
                if sq.author_filter == "me" and getattr(self, "_current_open_id", None)
                else None
            )
            return qt, focus_text, owner_ids

        sq_ctx: Dict[str, Tuple[str, str, Optional[List[str]]]] = {
            sq.sub_query_id: _resolve_sq_ctx(sq) for sq in sub_queries
        }

        # ── Doc track：每个 sq 的 doc_candidates 一就绪即 submit ────
        async def _prepare_doc_one(sq: SubQuery) -> dict:
            t0 = time.perf_counter()
            primary_doc_candidate_count = 0
            qt, focus_text, owner_ids = sq_ctx[sq.sub_query_id]
            if qt == "personal_recent":
                try:
                    from feishu_read_tool.search import search_feishu_mcp_pure
                    raw_result = await self._to_thread(
                        search_feishu_mcp_pure,
                        query=sq.query, count=4, uat=self._uat, time_filter=sq.time_filter,
                        owner_ids=owner_ids, sort_rule="EDIT_TIME",
                    )
                    doc_cands = raw_result.get("items", [])
                    primary_doc_candidate_count = len(doc_cands)
                    for rank, cand in enumerate(doc_cands, start=1):
                        cand["selected_focus_ids"] = [sq.target_focus]
                        cand["selected_sub_query_ids"] = [sq.sub_query_id]
                        cand["selected_query_types"] = [qt]
                        cand["cross_query_hit_count"] = 1
                        cand["best_api_rank"] = rank
                        cand["first_seen_order"] = rank
                        cand["route_hint"] = "mcp"
                except Exception as e:
                    logger.warning("[feishu] personal_recent search failed for %r: %s", sq.query, e)
                    doc_cands = []
            else:
                try:
                    doc_cands = await search_and_select_feishu_doc_candidates(
                        sq.query, token=self._uat, time_filter=sq.time_filter, page_size=4,
                        query_type=qt, focus_id=sq.target_focus, focus_text=focus_text,
                        sub_query_id=sq.sub_query_id, owner_ids=owner_ids,
                        executor=self._executor,
                    )
                    primary_doc_candidate_count = len(doc_cands)
                    if not doc_cands and sq.variants:
                        fallback_queries = [
                            variant for variant in dict.fromkeys(sq.variants)
                            if sq.variant_origins.get(variant) != "original_surface"
                        ][:2]
                        self._trace_events.extend({
                            "event": "query", "source": self.source, "focus": sq.target_focus,
                            "query_id": sq.sub_query_id, "query_type": sq.query_type,
                            "api_query": fallback_query, "fallback": True, "fallback_origin": "profile",
                            "fallback_kind": sq.variant_origins.get(fallback_query, "profile"),
                            "profile_item_id": sq.variant_origin_ids.get(fallback_query, 0),
                            "profile_item_type": sq.variant_origins.get(fallback_query, "profile"),
                        } for fallback_query in fallback_queries)
                        fallback_results = await asyncio.gather(*[
                            search_and_select_feishu_doc_candidates(
                                fallback_query, token=self._uat, time_filter=sq.time_filter, page_size=4,
                                query_type=qt, focus_id=sq.target_focus, focus_text=focus_text,
                                sub_query_id=sq.sub_query_id, owner_ids=owner_ids,
                                executor=self._executor,
                            ) for fallback_query in fallback_queries
                        ])
                        for fallback_query, group in zip(fallback_queries, fallback_results):
                            self._trace_events.append({
                                "event": "profile_fallback", "source": self.source,
                                "focus": sq.target_focus, "query_id": sq.sub_query_id,
                                "query_type": qt, "fallback_origin": "profile",
                                "fallback_kind": sq.variant_origins.get(fallback_query, "profile"),
                                "profile_item_id": sq.variant_origin_ids.get(fallback_query, 0),
                                "profile_item_type": sq.variant_origins.get(fallback_query, "profile"),
                                "api_query": fallback_query,
                                "primary_candidate_count": 0, "fallback_candidate_count": len(group),
                            })
                        doc_cands = [candidate for group in fallback_results for candidate in group]
                except Exception as e:
                    logger.warning("[feishu] doc search failed for %r: %s", sq.query, e)
                    doc_cands = []
            logger.info("TIMING feishu.prepare_doc sub_query_id=%s doc=%d ms=%.1f",
                         sq.sub_query_id or "unknown", len(doc_cands), _elapsed_ms(t0))
            return {"sq": sq, "query_type": qt, "focus_text": focus_text,
                    "doc_candidates": doc_cands,
                    "primary_doc_candidate_count": primary_doc_candidate_count}

        # ── Table track：每 sq 独立跑 search + structure + read ──
        async def _prepare_table_one(sq: SubQuery) -> dict:
            t0 = time.perf_counter()
            primary_table_candidate_count = 0
            qt, focus_text, _ = sq_ctx[sq.sub_query_id]
            if qt != "structured":
                return {"sq": sq, "table_candidates": []}
            try:
                table_cands = await search_and_select_feishu_table_candidates(
                    sq.query, token=self._uat, page_size=3,
                    query_type=qt, focus_id=sq.target_focus, focus_text=focus_text,
                    sub_query_id=sq.sub_query_id, table_pool=self._table_pool,
                    direct_limit=3,
                    wiki_limit=3,
                    executor=self._executor,
                    child_rerank_enabled=self.cfg.table_child_rerank_enabled,
                    child_rerank_model=self.cfg.table_child_rerank_model,
                    child_rerank_timeout_s=self.cfg.table_child_rerank_timeout_s,
                    child_rerank_top_n=self.cfg.table_child_rerank_top_n,
                    child_rerank_instruct=self.cfg.table_child_rerank_instruct,
                )
                primary_table_candidate_count = len(table_cands)
                if not table_cands and sq.variants:
                    fallback_queries = [
                        variant for variant in dict.fromkeys(sq.variants)
                        if sq.variant_origins.get(variant) != "original_surface"
                    ][:2]
                    self._trace_events.extend({
                        "event": "query", "source": self.source, "focus": sq.target_focus,
                        "query_id": sq.sub_query_id, "query_type": sq.query_type,
                        "api_query": fallback_query, "fallback": True, "fallback_origin": "profile",
                        "fallback_kind": sq.variant_origins.get(fallback_query, "profile"),
                        "profile_item_id": sq.variant_origin_ids.get(fallback_query, 0),
                        "profile_item_type": sq.variant_origins.get(fallback_query, "profile"),
                    } for fallback_query in fallback_queries)
                    fallback_results = await asyncio.gather(*[
                        search_and_select_feishu_table_candidates(
                            fallback_query, token=self._uat, page_size=3,
                            query_type=qt, focus_id=sq.target_focus, focus_text=focus_text,
                            sub_query_id=sq.sub_query_id, table_pool=self._table_pool,
                            direct_limit=3,
                            wiki_limit=3,
                            executor=self._executor,
                            child_rerank_enabled=self.cfg.table_child_rerank_enabled,
                            child_rerank_model=self.cfg.table_child_rerank_model,
                            child_rerank_timeout_s=self.cfg.table_child_rerank_timeout_s,
                            child_rerank_top_n=self.cfg.table_child_rerank_top_n,
                            child_rerank_instruct=self.cfg.table_child_rerank_instruct,
                        ) for fallback_query in fallback_queries
                    ])
                    for fallback_query, group in zip(fallback_queries, fallback_results):
                        self._trace_events.append({
                            "event": "profile_fallback", "source": self.source,
                            "focus": sq.target_focus, "query_id": sq.sub_query_id,
                            "query_type": qt, "fallback_origin": "profile",
                            "fallback_kind": sq.variant_origins.get(fallback_query, "profile"),
                            "profile_item_id": sq.variant_origin_ids.get(fallback_query, 0),
                            "profile_item_type": sq.variant_origins.get(fallback_query, "profile"),
                            "api_query": fallback_query,
                            "primary_candidate_count": 0, "fallback_candidate_count": len(group),
                        })
                    table_cands = [candidate for group in fallback_results for candidate in group]
            except Exception as e:
                logger.warning("[feishu] table search failed for %r: %s", sq.query, e)
                table_cands = []
            logger.info("TIMING feishu.prepare_table sub_query_id=%s table=%d ms=%.1f",
                         sq.sub_query_id or "unknown", len(table_cands), _elapsed_ms(t0))
            return {"sq": sq, "table_candidates": table_cands,
                    "primary_table_candidate_count": primary_table_candidate_count}

        # ── Doc track 跑完即 submit；与 table track 并发 ────────
        async def _doc_track() -> Tuple[List[dict], List[dict], Dict[str, List[str]]]:
            doc_prepared = await asyncio.gather(*[_prepare_doc_one(sq) for sq in sub_queries])
            for prepared in doc_prepared:
                sq = prepared["sq"]
                for rank, candidate in enumerate(prepared.get("doc_candidates") or [], 1):
                    self._trace_events.append({
                        "event": "candidate", "source": self.source,
                        "focus": sq.target_focus, "query_id": sq.sub_query_id,
                        "query_type": sq.query_type, "api_rank": int(candidate.get("best_api_rank") or rank),
                        "route": "mcp_doc",
                        "document_id": str(candidate.get("document_id") or candidate.get("doc_token") or candidate.get("token") or candidate.get("id") or ""),
                        "document": str(candidate.get("title") or candidate.get("name") or ""),
                        "document_type": str(candidate.get("doc_type") or candidate.get("type") or ""),
                    })
                    observer = current_role_funnel()
                    if observer is not None:
                        observer.observe_candidates([self._trace_events[-1]])
            early_doc_list, doc_ids_by_sq = _merge_doc_demands(doc_prepared)
            if early_doc_list and self._doc_pool is not None:
                _assign_doc_route_hints(early_doc_list, min_block=2)
                try:
                    await self._doc_pool.submit_and_read(
                        early_doc_list, query="__global_feishu_route_plan__",
                    )
                    logger.debug("TIMING feishu.submit_doc docs=%d preset=%s ms=%.1f",
                                 len(early_doc_list), self._preset, _elapsed_ms(prepare_started))
                except Exception as e:
                    logger.warning("[feishu] doc submit failed: %s", e)
            return doc_prepared, early_doc_list, doc_ids_by_sq

        async def _table_track() -> Tuple[List[dict], List[dict], Dict[str, List[str]]]:
            table_prepared = await asyncio.gather(*[_prepare_table_one(sq) for sq in sub_queries])
            _trim_table_parents(table_prepared, target=6, min_per_sq=2)
            for prepared in table_prepared:
                sq = prepared["sq"]
                for rank, candidate in enumerate(prepared.get("table_candidates") or [], 1):
                    source_route = "wiki_table" if candidate.get("_source") == "wiki" else "direct_table"
                    self._trace_events.append({
                        "event": "candidate", "source": self.source,
                        "focus": sq.target_focus, "query_id": sq.sub_query_id,
                        "query_type": sq.query_type, "api_rank": int(candidate.get("best_api_rank") or rank),
                        "route": source_route,
                        "document_id": _candidate_doc_key(candidate),
                        "document": str(candidate.get("title") or candidate.get("name") or ""),
                        "document_url": str(candidate.get("url") or ""),
                        "document_type": str(candidate.get("doc_type") or candidate.get("type") or ""),
                        "candidate_source": str(candidate.get("_source") or ""),
                        "candidate_sources": list(candidate.get("_sources") or []),
                        "owner_id": str(candidate.get("owner_id") or ""),
                        "owner_name": str(candidate.get("owner_name") or ""),
                        "table_structure_child_count": int(candidate.get("table_structure_child_count", 0) or 0),
                        "table_selected_child_count": int(candidate.get("table_selected_child_count", 0) or 0),
                        "table_child_overflow_count": int(candidate.get("table_child_overflow_count", 0) or 0),
                        "table_child_selection_mode": str(candidate.get("table_child_selection_mode") or ""),
                        "table_child_rerank_called": bool(candidate.get("table_child_rerank_called", False)),
                        "table_child_rerank_error_type": str(candidate.get("table_child_rerank_error_type") or ""),
                        "table_child_rerank_error": str(candidate.get("table_child_rerank_error") or ""),
                        "selected_child_ids": list(
                            candidate.get("selected_sheet_ids")
                            or candidate.get("selected_table_ids")
                            or []
                        ),
                        "parent_query_title_overlap": _metadata_overlap(
                            sq.query, str(candidate.get("title") or candidate.get("name") or ""),
                        ),
                        "parent_query_summary_overlap": _metadata_overlap(
                            sq.query, str(candidate.get("summary") or candidate.get("description") or ""),
                        ),
                        "parent_relevance_audit": "observed_only_no_filter",
                    })
                    observer = current_role_funnel()
                    if observer is not None:
                        observer.observe_candidates([self._trace_events[-1]])
            table_list, table_ids_by_sq = _merge_table_demands(table_prepared)
            return table_prepared, table_list, table_ids_by_sq

        (doc_prepared, early_doc_list, doc_ids_by_sq), (table_prepared, table_list, table_ids_by_sq) = (
            await asyncio.gather(_doc_track(), _table_track())
        )

        # A cross-language original-surface probe is a last-resort recovery
        # path, never a Planner primary query.  Do not run it from either
        # independent track above: it is eligible only after both primary
        # source routes for this subquery have produced zero candidates.
        table_prepared_by_sq = {item["sq"].sub_query_id: item for item in table_prepared}
        surface_doc_prepared: List[dict] = []
        surface_table_prepared: List[dict] = []
        for doc_item in doc_prepared:
            sq = doc_item["sq"]
            table_item = table_prepared_by_sq.get(sq.sub_query_id, {})
            doc_primary_count = int(doc_item.get("primary_doc_candidate_count") or 0)
            table_primary_count = int(table_item.get("primary_table_candidate_count") or 0)
            fallback_query = _original_surface_fallback_query(
                sq,
                primary_doc_candidate_count=doc_primary_count,
                primary_table_candidate_count=table_primary_count,
                current_doc_candidates=list(doc_item.get("doc_candidates") or []),
                current_table_candidates=list(table_item.get("table_candidates") or []),
            )
            if not fallback_query:
                continue
            qt, focus_text, owner_ids = sq_ctx[sq.sub_query_id]
            self._trace_events.append({
                "event": "query", "source": self.source, "focus": sq.target_focus,
                "query_id": sq.sub_query_id, "query_type": sq.query_type,
                "api_query": fallback_query, "fallback": True,
                "fallback_origin": "original_surface",
                "fallback_reason": "primary_doc_and_table_zero_candidates",
                "primary_doc_candidate_count": doc_primary_count,
                "primary_table_candidate_count": table_primary_count,
            })

            async def _surface_doc() -> List[dict]:
                if qt == "personal_recent":
                    return []
                return await search_and_select_feishu_doc_candidates(
                    fallback_query, token=self._uat, time_filter=sq.time_filter, page_size=4,
                    query_type=qt, focus_id=sq.target_focus, focus_text=focus_text,
                    sub_query_id=sq.sub_query_id, owner_ids=owner_ids,
                    executor=self._executor,
                )

            async def _surface_table() -> List[dict]:
                if qt != "structured":
                    return []
                return await search_and_select_feishu_table_candidates(
                    fallback_query, token=self._uat, page_size=3,
                    query_type=qt, focus_id=sq.target_focus, focus_text=focus_text,
                    sub_query_id=sq.sub_query_id, table_pool=self._table_pool,
                    direct_limit=3, wiki_limit=3, executor=self._executor,
                    child_rerank_enabled=self.cfg.table_child_rerank_enabled,
                    child_rerank_model=self.cfg.table_child_rerank_model,
                    child_rerank_timeout_s=self.cfg.table_child_rerank_timeout_s,
                    child_rerank_top_n=self.cfg.table_child_rerank_top_n,
                    child_rerank_instruct=self.cfg.table_child_rerank_instruct,
                )

            try:
                fallback_docs, fallback_tables = await asyncio.gather(_surface_doc(), _surface_table())
            except Exception as exc:
                logger.warning("[feishu] original-surface fallback failed for %r: %s", fallback_query, exc)
                fallback_docs, fallback_tables = [], []
            doc_item["doc_candidates"] = list(fallback_docs)
            if fallback_docs:
                surface_doc_prepared.append(doc_item)
            if table_item:
                table_item["table_candidates"] = list(fallback_tables)
                if fallback_tables:
                    surface_table_prepared.append(table_item)
            self._trace_events.append({
                "event": "source_fallback", "source": self.source,
                "focus": sq.target_focus, "query_id": sq.sub_query_id,
                "query_type": sq.query_type, "api_query": fallback_query,
                "fallback": True, "fallback_origin": "original_surface",
                "fallback_reason": "primary_doc_and_table_zero_candidates",
                "primary_doc_candidate_count": doc_primary_count,
                "primary_table_candidate_count": table_primary_count,
                "fallback_doc_candidate_count": len(fallback_docs),
                "fallback_table_candidate_count": len(fallback_tables),
            })
        if surface_doc_prepared:
            surface_docs, surface_doc_ids = _merge_doc_demands(surface_doc_prepared)
            for sq_id, ids in surface_doc_ids.items():
                doc_ids_by_sq[sq_id] = ids
            if surface_docs and self._doc_pool is not None:
                _assign_doc_route_hints(surface_docs, min_block=2)
                try:
                    await self._doc_pool.submit_and_read(
                        surface_docs, query="__original_surface_fallback__",
                    )
                except Exception as exc:
                    logger.warning("[feishu] original-surface doc submit failed: %s", exc)
        if surface_table_prepared:
            _trim_table_parents(surface_table_prepared, target=6, min_per_sq=2)
            surface_tables, surface_table_ids = _merge_table_demands(surface_table_prepared)
            for sq_id, ids in surface_table_ids.items():
                table_ids_by_sq[sq_id] = ids
            existing_table_ids = {_candidate_doc_key(item) for item in table_list}
            table_list.extend(
                item for item in surface_tables if _candidate_doc_key(item) not in existing_table_ids
            )
        logger.debug("TIMING feishu.batch_prepare_search sub_queries=%d ms=%.1f",
                     len(sub_queries), _elapsed_ms(prepare_started))

        # 合并 doc/table prepared 为下游 _execute_one 用的 batch 列表（保留 sq 顺序）
        prepared: List[dict] = []
        table_by_sq: Dict[str, List[dict]] = {
            t["sq"].sub_query_id: t.get("table_candidates", []) for t in table_prepared
        }
        for d in doc_prepared:
            sid = d["sq"].sub_query_id
            prepared.append({
                "sq": d["sq"], "query_type": d["query_type"], "focus_text": d["focus_text"],
                "doc_candidates": d.get("doc_candidates", []),
                "table_candidates": table_by_sq.get(sid, []),
            })

        table_chunks_by_token: Dict[str, List[UnifiedChunk]] = {}
        table_events: Dict[str, asyncio.Event] = {}
        # The reader itself has a hard wall budget.  A consumer must never use
        # the document collector's much larger budget merely because an event
        # was not signalled after that reader has already timed out.
        table_wait_timeout_s = min(
            doc_pool_timeout,
            max(float(self.cfg.feishu_sheet_read_wall_s), float(self.cfg.feishu_bitable_read_wall_s)) + 1.0,
        )

        if table_list:
            from feishu_read_tool.sheet_reader import read_sheets
            from feishu_read_tool.bitable_reader import read_bitables

            for cand in table_list:
                token = _candidate_doc_key(cand)
                doc_type = str(cand.get("doc_type", "")).upper()
                if doc_type == "SHEET" and self._table_pool and hasattr(self._table_pool, "_sheet_struct_cache"):
                    cached = self._table_pool._sheet_struct_cache.get(token)
                    if cached is not None:
                        cand["_cached_tabs"] = cached
                elif doc_type == "BITABLE" and self._table_pool and hasattr(self._table_pool, "_bitable_struct_cache"):
                    cached = self._table_pool._bitable_struct_cache.get(token)
                    if cached is not None:
                        cand["_cached_tables"] = cached
                if token:
                    table_events[token] = asyncio.Event()

            # SHEET 读取总预算硬墙：sheet API（90235 暂态 + 多 tab 并发）尾部偶发
            # 25-40s 卡顿。配合下层 sheet_reader 的 per_call_timeout_s=3.0 + retry，
            # 总预算 8s 足够正常情况 + 1-2 次 90235 重试；超过即放弃这个 sheet（chunks=[]），
            # consumer (sq) 不再被它阻塞。底层 to_thread 任务由于跨线程无法 cancel,
            # 会继续在 worker 中跑完，但结果会被丢弃。
            _SHEET_READ_WALL_S = float(self.cfg.feishu_sheet_read_wall_s)
            _BITABLE_READ_WALL_S = float(self.cfg.feishu_bitable_read_wall_s)
            # This execution path reads tables directly so it must retain the
            # TablePool's request-wide concurrency limits rather than relying
            # only on the 24-worker source executor.
            sheet_read_sem = getattr(self._table_pool, "_sheet_read_sem", None) or asyncio.Semaphore(6)
            bitable_read_sem = getattr(self._table_pool, "_bitable_read_sem", None) or asyncio.Semaphore(6)
            table_read_tasks: List[asyncio.Task[None]] = []

            async def _read_table_one(cand: dict) -> None:
                token = _candidate_doc_key(cand)
                doc_type = str(cand.get("doc_type", "")).upper()
                rq = cand.get("read_query", "") or ""
                rd_started = time.perf_counter()
                try:
                    if doc_type == "SHEET":
                        try:
                            async with sheet_read_sem:
                                chunks = await asyncio.wait_for(
                                    self._to_thread(read_sheets, rq, [cand], self._uat),
                                    timeout=_SHEET_READ_WALL_S,
                                )
                        except asyncio.TimeoutError:
                            logger.warning(
                                "[feishu] sheet read wall timeout %.1fs token=%s (abandoning)",
                                _SHEET_READ_WALL_S, token[:16],
                            )
                            self._trace_events.append({
                                "event": "table_read_timeout", "source": self.source,
                                "route": "sheet_api", "document_id": token,
                                "document_type": doc_type,
                                "wall_timeout_s": _SHEET_READ_WALL_S,
                                "sync_io_may_continue": True,
                                "focus_ids": list(cand.get("selected_focus_ids") or []),
                                "query_ids": list(cand.get("selected_sub_query_ids") or []),
                            })
                            chunks = []
                    elif doc_type == "BITABLE":
                        try:
                            async with bitable_read_sem:
                                chunks = await asyncio.wait_for(
                                    self._to_thread(read_bitables, rq, [cand], self._uat),
                                    timeout=_BITABLE_READ_WALL_S,
                                )
                        except asyncio.TimeoutError:
                            logger.warning(
                                "[feishu] bitable read wall timeout %.1fs token=%s (abandoning)",
                                _BITABLE_READ_WALL_S, token[:16],
                            )
                            self._trace_events.append({
                                "event": "table_read_timeout", "source": self.source,
                                "route": "bitable_api", "document_id": token,
                                "document_type": doc_type,
                                "wall_timeout_s": _BITABLE_READ_WALL_S,
                                "sync_io_may_continue": True,
                                "focus_ids": list(cand.get("selected_focus_ids") or []),
                                "query_ids": list(cand.get("selected_sub_query_ids") or []),
                            })
                            chunks = []
                    else:
                        chunks = []
                    read_ms = _elapsed_ms(rd_started)
                    normalized = []
                    for uc in chunks:
                        meta = dict(uc.metadata or {})
                        meta["reader_score"] = float(uc.score or 0.0)
                        meta["doc_id"] = token
                        meta["doc_token"] = token
                        meta["route"] = "sheet_api" if doc_type == "SHEET" else "bitable_api"
                        meta["selected_focus_ids"] = list(cand.get("selected_focus_ids") or [])
                        meta["selected_sub_query_ids"] = list(cand.get("selected_sub_query_ids") or [])
                        meta["selected_feishu_urls"] = list(cand.get("selected_feishu_urls") or [])
                        meta["selected_query_types"] = list(cand.get("selected_query_types") or [])
                        meta["api_rank"] = int(cand.get("best_api_rank", 10**9) or 10**9)
                        meta["candidate_source"] = cand.get("_source", "")
                        meta["candidate_sources"] = list(cand.get("_sources") or [])
                        meta["read_query"] = rq
                        meta["table_selected_child_count"] = int(cand.get("table_selected_child_count", 0) or 0)
                        meta["table_structure_child_count"] = int(cand.get("table_structure_child_count", 0) or 0)
                        meta["table_read_ms"] = read_ms
                        meta["table_read_chunks"] = len(chunks)
                        meta["table_selected_child_ids"] = list(
                            cand.get("selected_sheet_ids") or cand.get("selected_table_ids") or []
                        )
                        meta["table_parent_query_title_overlap"] = _metadata_overlap(
                            str(cand.get("read_query") or ""), str(cand.get("title") or ""),
                        )
                        meta["table_submit_selected"] = True
                        meta["table_submit_skipped"] = False
                        normalized.append(UnifiedChunk(
                            text=uc.text, source_name=uc.source_name, source_url=uc.source_url,
                            source_tool=uc.source_tool, heading_path=uc.heading_path,
                            score=0.0, confidence_tier=uc.confidence_tier, metadata=meta,
                        ))
                    table_chunks_by_token[token] = normalized
                    self._trace_events.append({
                        "event": "table_read", "source": self.source,
                        "route": "sheet_api" if doc_type == "SHEET" else "bitable_api",
                        "document_id": token, "document_type": doc_type,
                        "chunk_count": len(normalized), "elapsed_ms": round(read_ms, 1),
                    })
                    logger.debug("TIMING feishu.table_read token=%s type=%s chunks=%d ms=%.1f",
                                token[:16], doc_type, len(normalized), read_ms)
                except Exception as e:
                    logger.warning("[feishu] table read failed token=%s: %s", token[:16], e)
                    table_chunks_by_token[token] = []
                finally:
                    evt = table_events.get(token)
                    if evt:
                        evt.set()

            # FocusBoard 早停门：cand 服务的 focus 已被别源全部满足 → 跳过本次 table 读取，
            # 直接 set event 让 _wait_table 立刻返回空。修补关键路径上的 10-17s 浪费。
            board = getattr(self, "_focus_board", None)
            for cand in table_list:
                token = _candidate_doc_key(cand)
                focus_ids = list(cand.get("selected_focus_ids") or [])
                if (
                    board is not None
                    and focus_ids
                    and all(board.is_satisfied(fid) for fid in focus_ids)
                ):
                    logger.debug(
                        "[feishu] table skip (focus satisfied) token=%s focus_ids=%s",
                        token[:16], focus_ids,
                    )
                    table_chunks_by_token[token] = []
                    evt = table_events.get(token)
                    if evt:
                        evt.set()
                    continue
                table_read_tasks.append(asyncio.create_task(
                    _read_table_one(cand), name=f"feishu-table:{token[:16]}",
                ))

        logger.debug("TIMING feishu.batch_prepare docs=%d tables=%d ms=%.1f",
                     len(early_doc_list), len(table_list), _elapsed_ms(prepare_started))

        # ── Step 4: 并行等待 doc + table → evidence pool ─────
        async def _execute_one(batch: dict) -> List[ChunkEntry]:
            started = time.perf_counter()
            sq = batch["sq"]
            qt = batch["query_type"]
            d_ids = doc_ids_by_sq.get(sq.sub_query_id, [])
            t_ids = table_ids_by_sq.get(sq.sub_query_id, [])

            async def _wait_doc():
                if not d_ids:
                    return []
                try:
                    chunks = await self._doc_pool.wait_and_collect(
                        d_ids, total_timeout=doc_pool_timeout, per_doc_timeout=per_doc_timeout
                    )
                    logger.debug("TIMING feishu.execute_one_doc_wait sub_query_id=%s docs=%d chunks=%d ms=%.1f",
                                 sq.sub_query_id, len(d_ids), len(chunks), _elapsed_ms(started))
                    return chunks
                except Exception as e:
                    logger.warning("[feishu] doc wait failed: %s", e)
                    return []

            async def _wait_table():
                if qt != "structured" or not t_ids:
                    return []
                # In-flight cancel: 抢答 evt vs focus.satisfied_event。当本 sq 的 focus
                # 被别源 satisfied 时立刻放弃剩余 tid 的等待，sq 直接进入 eval 流程。
                # 注意：**不** 取消底层 read task —— 多 sq 可能共享同一 token（cross-sq
                # dedup），cancel 会让 evt.set() 前的赋值跳过，坑共享 sq；而且 to_thread
                # 底层是阻塞线程，cancel 也救不回真正的 I/O。只断本 sq 的等待是最稳妥的。
                board = getattr(self, "_focus_board", None)
                sat_evt = board.satisfied_event(sq.target_focus) if board is not None else None

                collected: List[UnifiedChunk] = []
                abandoned = 0
                for tid in t_ids:
                    evt = table_events.get(tid)
                    if not evt:
                        continue
                    if sat_evt is not None and sat_evt.is_set():
                        abandoned += 1
                        continue
                    if evt.is_set():
                        collected.extend(table_chunks_by_token.get(tid, []))
                        continue
                    if sat_evt is not None:
                        evt_task = asyncio.create_task(evt.wait())
                        sat_task = asyncio.create_task(sat_evt.wait())
                        try:
                            done, _ = await asyncio.wait(
                                {evt_task, sat_task},
                                timeout=table_wait_timeout_s,
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                        finally:
                            for t in (evt_task, sat_task):
                                if not t.done():
                                    t.cancel()
                        if sat_task in done and evt_task not in done:
                            abandoned += 1
                            continue
                        if not done:
                            logger.warning("[feishu] table wait timeout token=%s", tid[:16])
                    else:
                        try:
                            await asyncio.wait_for(evt.wait(), timeout=table_wait_timeout_s)
                        except asyncio.TimeoutError:
                            logger.warning("[feishu] table wait timeout token=%s", tid[:16])
                    collected.extend(table_chunks_by_token.get(tid, []))
                if abandoned:
                    logger.debug("[feishu] table wait abandoned sub_query_id=%s focus=%s abandoned=%d (focus satisfied externally)",
                                 sq.sub_query_id, sq.target_focus, abandoned)
                logger.debug("TIMING feishu.execute_one_table_wait sub_query_id=%s parents=%d chunks=%d ms=%.1f",
                             sq.sub_query_id, len(t_ids), len(collected), _elapsed_ms(started))
                return collected

            doc_chunks, table_chunks = await asyncio.gather(_wait_doc(), _wait_table())

            # Both pools already obey API/read budgets.  Keep every resulting
            # chunk in API order; preview evaluation, not a secondary selector,
            # decides evidence relevance.

            async def _collect_doc_chunks() -> List[UnifiedChunk]:
                if not doc_chunks:
                    return []
                selected = await self._to_thread(
                    _preserve_api_order_chunks,
                    query=sq.query, focus_text=batch["focus_text"],
                    query_type=qt, uc_list=doc_chunks, top_n=len(doc_chunks),
                )
                logger.info("TIMING feishu.chunk_select_doc sub_query_id=%s raw=%d selected=%d",
                             sq.sub_query_id, len(doc_chunks), len(selected))
                return selected

            async def _collect_table_chunks() -> List[UnifiedChunk]:
                if not table_chunks:
                    return []
                selected = await self._to_thread(
                    _preserve_api_order_chunks,
                    query=sq.query, focus_text=batch["focus_text"],
                    query_type=qt, uc_list=table_chunks, top_n=len(table_chunks),
                )
                logger.info("TIMING feishu.chunk_select_table sub_query_id=%s raw=%d selected=%d",
                             sq.sub_query_id, len(table_chunks), len(selected))
                return selected

            doc_selected, table_selected = await asyncio.gather(_collect_doc_chunks(), _collect_table_chunks())

            uc_list = doc_selected + table_selected
            if not uc_list:
                return []
            logger.info("TIMING feishu.execute_one_total sub_query_id=%s doc=%d table=%d total=%d ms=%.1f",
                         sq.sub_query_id, len(doc_selected), len(table_selected), len(uc_list), _elapsed_ms(started))
            return [_unified_to_chunk_entry(uc, sq.query, sq.target_focus, sq.sub_query_id) for uc in uc_list]

        async def _tagged_execute(batch: dict) -> tuple[SubQuery, List[ChunkEntry]]:
            return batch["sq"], await _execute_one(batch)

        # Consume each completed read result immediately.  A large early doc
        # can now trigger a bounded post-I/O Qwen generation while slower docs
        # and table reads are still in flight; do not wait for the final
        # cross-subquery gather merely to begin independent rerank work.
        execute_tasks = [asyncio.create_task(_tagged_execute(batch)) for batch in prepared]
        partial_read_observed: set[str] = set()
        embedded_sheet_skip_observed: set[str] = set()
        embedded_sheet_selection_observed: set[str] = set()
        for completed in asyncio.as_completed(execute_tasks):
            sq, chunks = await completed
            for c in chunks:
                doc_id = str(c.metadata.get("doc_id") or "")
                if (
                    int(c.metadata.get("embedded_sheet_selection_count", 0) or 0) > 0
                    and doc_id not in embedded_sheet_selection_observed
                ):
                    embedded_sheet_selection_observed.add(doc_id)
                    self._trace_events.append({
                        "event": "embedded_sheet_child_selection", "source": self.source,
                        "document_id": doc_id,
                        "selected_child_ids": list(c.metadata.get("embedded_sheet_selected_child_ids") or []),
                        "selection_parent_count": int(c.metadata.get("embedded_sheet_selection_count", 0) or 0),
                    })
                if (
                    int(c.metadata.get("embedded_sheet_skip_count", 0) or 0) > 0
                    and doc_id not in embedded_sheet_skip_observed
                ):
                    embedded_sheet_skip_observed.add(doc_id)
                    disposition = str(c.metadata.get("embedded_sheet_failure_disposition") or "")
                    self._trace_events.append({
                        "event": "embedded_sheet_unavailable", "source": self.source,
                        "document_id": doc_id,
                        "count": int(c.metadata.get("embedded_sheet_skip_count", 0) or 0),
                        "reason": disposition or "invalid_or_transport_embed_failure",
                        "children": list(c.metadata.get("embedded_sheet_failures") or []),
                    })
                if c.metadata.get("reader_partial") and doc_id not in partial_read_observed:
                    partial_read_observed.add(doc_id)
                    self._trace_events.append({
                        "event": "partial_read", "source": self.source,
                        "document_id": doc_id,
                        "route": str(c.metadata.get("route") or ""),
                        "failure_count": int(c.metadata.get("reader_failure_count", 0) or 0),
                        "failure_types": list(c.metadata.get("reader_failure_types") or []),
                        "transport_inconclusive": True,
                    })
                entry = ChunkEntry(
                    id=c.id, source=c.source, content=c.content,
                    preview_content=c.preview_content,
                    metadata=dict(c.metadata), score=c.score,
                    sub_query=c.sub_query or sq.query,
                    target_focuses=set(c.target_focuses) | {sq.target_focus}, tier=c.tier,
                )
                existing = chunk_pool.get(entry.id)
                chunk_pool[entry.id] = existing.merge(entry) if existing else entry
                await self._offer_chunk_for_context_eval(entry, chunk_pool, loop_n=loop_n)
            await self._flush_rerank_generation_if_ready(chunk_pool, loop_n=loop_n)
        if table_list:
            # Every request-owned table coroutine reaches its finally/event
            # boundary before the source returns.  Sync HTTP work is separately
            # tracked by SourceAgent._to_thread for quiescent retry checks.
            await asyncio.gather(*table_read_tasks, return_exceptions=True)
        if self._doc_pool is not None and hasattr(self._doc_pool, "consume_observability_events"):
            for event in self._doc_pool.consume_observability_events():
                # Successful document-read timing is diagnostic data, not a
                # transport degradation. Preserve the inconclusive marker only
                # for actual deadline/failure paths.
                is_transport_gap = str(event.get("event") or "") in {
                    "consumer_deadline", "block_api_failed", "mcp_fallback_failed",
                }
                self._trace_events.append({
                    **event, "source": self.source,
                    **({"transport_inconclusive": True} if is_transport_gap else {}),
                })
        logger.info("TIMING feishu.batch_total sub_queries=%d chunk_pool=%d ms=%.1f",
                     len(sub_queries), len(chunk_pool), _elapsed_ms(batch_started))

        # 回填 ROI 数据到 table_pool 供 _log_table_roi 使用
        if table_list and self._table_pool is not None:
            for cand in table_list:
                token = _candidate_doc_key(cand)
                if not token:
                    continue
                cand_chunks = table_chunks_by_token.get(token, [])
                read_ms = 0.0
                if cand_chunks:
                    m0 = cand_chunks[0].metadata or {}
                    read_ms = float(m0.get("table_read_ms", 0) or 0)
                meta = dict(self._table_pool._parent_meta.get(token) or {})
                meta.update({
                    "id": token,
                    "title": cand.get("title", ""),
                    "doc_type": cand.get("doc_type", ""),
                    "best_api_rank": int(cand.get("best_api_rank", 10**9) or 10**9),
                    "first_seen_order": int(cand.get("first_seen_order", 10**9) or 10**9),
                    "selected_focus_ids": list(cand.get("selected_focus_ids") or []),
                    "selected_sub_query_ids": list(cand.get("selected_sub_query_ids") or []),
                    "selected_query_types": list(cand.get("selected_query_types") or []),
                    "table_selected_child_count": int(cand.get("table_selected_child_count", 0) or 0),
                    "table_structure_child_count": int(cand.get("table_structure_child_count", 0) or 0),
                    "table_submit_selected": True,
                    "table_submit_skipped": False,
                    "table_read_ms": read_ms,
                    "table_read_chunks": len(cand_chunks),
                    "table_selected_child_ids": list(
                        cand.get("selected_sheet_ids") or cand.get("selected_table_ids") or []
                    ),
                    "parent_query_title_overlap": _metadata_overlap(
                        str(cand.get("read_query") or ""), str(cand.get("title") or ""),
                    ),
                })
                self._table_pool._parent_meta[token] = meta

    # ── lite 极简路径：doc MCP-only，无 table ─────────────────
    async def _execute_batch_lite(
        self,
        sub_queries: List[SubQuery],
        chunk_pool: Dict[str, ChunkEntry],
        doc_pool_timeout: float,
        batch_started: float,
    ) -> None:
        """lite 模式：每个 sq 独立搜索 doc，跨 sq 去重后取前 8 篇，
        全部强制 MCP 路由，无候选选择器，无 table 流水线。
        """
        await self._ensure_user_identity(sub_queries)
        _LITE_DOC_CAP = 8  # 跨 sq 去重后最多提交的 doc 数

        # Step 1: 并行搜索 doc（直接取搜索 API 原顺序）
        async def _search_docs_lite(sq: SubQuery) -> dict:
            started = time.perf_counter()
            qt = sq.query_type or self._default_query_type
            if qt not in ("personal_recent", "structured", "concept"):
                qt = "concept"
            # lite preset 不启动 table 流水线，structured 降级为 concept 以保持行为一致
            if self._preset == "lite" and qt == "structured":
                qt = "concept"
            focus_obj = self._current_focus_lookup.get(sq.target_focus)
            focus_text = focus_obj.text if focus_obj else sq.target_focus
            
            owner_ids = [getattr(self, "_current_open_id", None)] if sq.author_filter == "me" and getattr(self, "_current_open_id", None) else None
            
            doc_cands: List[dict] = []
            try:
                if qt == "personal_recent":
                    from feishu_read_tool.search import search_feishu_mcp_pure
                    raw_result = await self._to_thread(
                        search_feishu_mcp_pure,
                        query=sq.query, count=4, uat=self._uat, time_filter=sq.time_filter,
                        owner_ids=owner_ids, sort_rule="EDIT_TIME"
                    )
                    raw = raw_result.get("items", [])
                else:
                    from feishu_read_tool.search import search_feishu_docs
                    result = await self._to_thread(
                        search_feishu_docs, query=sq.query, count=4,
                        uat=self._uat, time_filter=sq.time_filter, owner_ids=owner_ids
                    )
                    raw = result.get("items", [])
                    if not raw and sq.variants:
                        fallback_queries = list(dict.fromkeys(sq.variants))[:2]
                        self._trace_events.extend({
                            "event": "query", "source": self.source, "focus": sq.target_focus,
                            "query_id": sq.sub_query_id, "query_type": sq.query_type,
                            "api_query": fallback_query, "fallback": True, "fallback_origin": "profile",
                            "fallback_kind": sq.variant_origins.get(fallback_query, "profile"),
                            "profile_item_id": sq.variant_origin_ids.get(fallback_query, 0),
                            "profile_item_type": sq.variant_origins.get(fallback_query, "profile"),
                        } for fallback_query in fallback_queries)
                        fallback_results = await asyncio.gather(*[
                            self._to_thread(
                                search_feishu_docs, query=fallback_query, count=4,
                                uat=self._uat, time_filter=sq.time_filter, owner_ids=owner_ids,
                            ) for fallback_query in fallback_queries
                        ])
                        for fallback_query, result in zip(fallback_queries, fallback_results):
                            self._trace_events.append({
                                "event": "profile_fallback", "source": self.source,
                                "focus": sq.target_focus, "query_id": sq.sub_query_id,
                                "query_type": qt, "fallback_origin": "profile",
                                "fallback_kind": sq.variant_origins.get(fallback_query, "profile"),
                                "profile_item_id": sq.variant_origin_ids.get(fallback_query, 0),
                                "profile_item_type": sq.variant_origins.get(fallback_query, "profile"),
                                "api_query": fallback_query,
                                "primary_candidate_count": 0,
                                "fallback_candidate_count": len(result.get("items", [])),
                            })
                        raw = [candidate for result in fallback_results for candidate in result.get("items", [])]
                            
                # 直接用搜索结果，注入 lineage 字段
                for rank, c in enumerate(raw, start=1):
                    c["selected_focus_ids"] = [sq.target_focus]
                    c["selected_sub_query_ids"] = [sq.sub_query_id]
                    c["selected_query_types"] = [qt]
                    c["cross_query_hit_count"] = 1
                    c["best_api_rank"] = rank
                    c["first_seen_order"] = rank
                doc_cands = raw
            except Exception as e:
                logger.warning("[feishu][lite] doc search failed for %r: %s", sq.query, e)
            logger.info("TIMING feishu.lite.search_docs sub_query_id=%s raw=%d ms=%.1f",
                         sq.sub_query_id or "unknown", len(doc_cands), _elapsed_ms(started))
            return {"sq": sq, "query_type": qt, "focus_text": focus_text,
                    "doc_candidates": doc_cands, "table_candidates": []}

        prepare_started = time.perf_counter()
        prepared = await asyncio.gather(*[_search_docs_lite(sq) for sq in sub_queries])
        logger.debug("TIMING feishu.lite.batch_prepare_search sub_queries=%d ms=%.1f",
                     len(sub_queries), _elapsed_ms(prepare_started))

        # Step 2: 跨 sq 去重合并，取前 _LITE_DOC_CAP 篇；
        # 分流策略：前 _LITE_MCP_TOP_N 篇走 MCP（综合评分高的优先用 MCP），
        # 其余走 block_api（DocPool 已经按 lite_block_only=True 跳过 IO+comments，
        # 只调 list_document_blocks 主路径）。
        #
        # 这样在保持并发的前提下把负载在两条 API 通道间分摊，避免单条 MCP 通道
        # 集中触发服务端 5002 频控；block_api 由 99991400 共享 5 qps 桶兜底。
        _LITE_MCP_TOP_N = 4
        doc_list, doc_ids_by_sq = _merge_doc_demands(prepared)
        doc_list = doc_list[:_LITE_DOC_CAP]
        mcp_count = 0
        block_count = 0
        for idx, cand in enumerate(doc_list):
            if idx < _LITE_MCP_TOP_N:
                cand["route_hint"] = "mcp"
                mcp_count += 1
            else:
                cand["route_hint"] = "block_api"
                block_count += 1
        logger.debug(
            "[feishu][lite] doc route_hint mcp=%d block_api=%d total=%d (lite_block_only=True)",
            mcp_count, block_count, len(doc_list),
        )

        if doc_list and self._doc_pool is not None:
            try:
                await self._doc_pool.submit_and_read(doc_list, query="__lite_split__")
                logger.debug("TIMING feishu.lite.submit_doc docs=%d ms=%.1f",
                             len(doc_list), _elapsed_ms(prepare_started))
            except Exception as e:
                logger.warning("[feishu][lite] doc submit failed: %s", e)

        logger.debug("TIMING feishu.lite.batch_prepare docs=%d tables=0 ms=%.1f",
                     len(doc_list), _elapsed_ms(prepare_started))

        # Step 3: 等待 doc chunks → chunk_pool
        async def _execute_one_lite(batch: dict) -> List[ChunkEntry]:
            started = time.perf_counter()
            sq = batch["sq"]
            qt = batch["query_type"]
            d_ids = doc_ids_by_sq.get(sq.sub_query_id, [])
            # 只保留在 doc_list 里的 ids（已截断到 _LITE_DOC_CAP）
            cap_ids = {_candidate_doc_key(c) for c in doc_list}
            d_ids = [did for did in d_ids if did in cap_ids]
            if not d_ids:
                return []
            try:
                # Lite path: per-doc 6s 上限，慢 doc 不阻塞批次；其他文档继续 chunking
                doc_chunks = await self._doc_pool.wait_and_collect(
                    d_ids, total_timeout=doc_pool_timeout, per_doc_timeout=6.0,
                )
                logger.debug("TIMING feishu.lite.doc_wait sub_query_id=%s docs=%d chunks=%d ms=%.1f",
                             sq.sub_query_id, len(d_ids), len(doc_chunks), _elapsed_ms(started))
            except Exception as e:
                logger.warning("[feishu][lite] doc wait failed: %s", e)
                doc_chunks = []
            if not doc_chunks:
                return []
            selected = _preserve_api_order_chunks(
                query=sq.query, focus_text=batch["focus_text"],
                query_type=qt, uc_list=doc_chunks, top_n=self.cfg.sub_query_top_n,
            )
            logger.info("TIMING feishu.lite.execute_one sub_query_id=%s raw=%d selected=%d ms=%.1f",
                         sq.sub_query_id, len(doc_chunks), len(selected), _elapsed_ms(started))
            return [_unified_to_chunk_entry(uc, sq.query, sq.target_focus, sq.sub_query_id) for uc in selected]

        batches_result = await asyncio.gather(*[_execute_one_lite(b) for b in prepared])
        for sq, chunks in zip(sub_queries, batches_result):
            for c in chunks:
                entry = ChunkEntry(
                    id=c.id, source=c.source, content=c.content,
                    metadata=dict(c.metadata), score=c.score,
                    sub_query=c.sub_query or sq.query,
                    target_focuses=set(c.target_focuses) | {sq.target_focus}, tier=c.tier,
                )
                existing = chunk_pool.get(entry.id)
                chunk_pool[entry.id] = existing.merge(entry) if existing else entry
                await self._offer_chunk_for_context_eval(entry, chunk_pool)
        logger.info("TIMING feishu.lite.batch_total sub_queries=%d chunk_pool=%d ms=%.1f",
                     len(sub_queries), len(chunk_pool), _elapsed_ms(batch_started))
