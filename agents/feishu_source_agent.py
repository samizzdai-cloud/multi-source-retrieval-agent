"""FeishuSourceAgent v3 — 飞书源适配器（doc/table 独立流水线 + 结构感知 prefilter）。

架构要点：
- plan_only LLM 产出 sub_queries（query + query_type + time_filter）
- _execute_batch 覆写基类：doc 和 table 独立流水线，互不等待
  · doc: search → candidate rerank → doc_pool.submit（fire-and-forget）
  · table: search → structure prefetch → enriched prefilter → cache child select → table_pool.submit
- 每个 sub_query 并行等待 doc + table chunks → 统一 qwen3-rerank → chunk_pool
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
from typing import Dict, List, Optional, Set

from tools.feishu_retrieve import (
    search_and_read_feishu_tables,
    search_and_select_feishu_doc_candidates,
    search_and_select_feishu_table_candidates,
    TABLE_CHILD_SELECT_TOP_K,
    _table_candidate_key,
)
from tools.models import ChunkEntry, SourceAgentConfig, UnifiedChunk
from tools.rerank import (
    build_focus_sub_query_question,
    get_chunk_selection_config,
    select_reranked_chunks,
)
from tools.source_profiles import get_profile
from tools.source_query_config import SOURCE_QUERY_RULES

from agents.source_agent_base import Focus, SourceAgent, SubQuery
from agents.sub_agents import (
    build_eval_only_agent,
    build_plan_eval_agent,
    build_plan_only_agent,
)

logger = logging.getLogger(__name__)

_FEISHU_ID_PREFIX = "feishu:"


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def _strip_feishu_prefix(s: str) -> str:
    if not s:
        return s
    return s[len(_FEISHU_ID_PREFIX):] if s.startswith(_FEISHU_ID_PREFIX) else s


def _normalize_query_type(qt: Optional[str]) -> str:
    return "concept" if qt == "concept" else "structured"


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
        metadata=merged_meta, score=uc.score,
        sub_query=sub_query, target_focuses={target_focus}, tier=None,
    )


def _clone_uc_with_score(uc: UnifiedChunk, score: float, rank: int, mode: str) -> UnifiedChunk:
    meta = dict(uc.metadata or {})
    meta.setdefault("reader_score", float(uc.score or 0.0))
    meta["chunk_rerank_score"] = float(score)
    meta["chunk_rerank_rank"] = int(rank)
    meta["selection_mode"] = mode
    return UnifiedChunk(
        text=uc.text, source_name=uc.source_name, source_url=uc.source_url,
        source_tool=uc.source_tool, heading_path=uc.heading_path,
        score=float(score), confidence_tier=uc.confidence_tier, metadata=meta,
    )


def _uc_to_rerank_payload(uc: UnifiedChunk) -> dict:
    return {
        "text": uc.text or "",
        "source_name": uc.source_name or "",
        "heading_path": uc.heading_path or "",
    }


def _fallback_select(uc_list: List[UnifiedChunk], top_n: int) -> List[UnifiedChunk]:
    return sorted(uc_list, key=lambda x: float(x.score or 0.0), reverse=True)[:top_n]


def _select_chunks_for_sub_query(
    query: str, focus_text: str, query_type: str,
    uc_list: List[UnifiedChunk], top_n: int,
) -> List[UnifiedChunk]:
    started = time.perf_counter()
    if not uc_list:
        return []
    cfg = get_chunk_selection_config(query_type)
    question = build_focus_sub_query_question(focus_text, query)
    payloads = [_uc_to_rerank_payload(uc) for uc in uc_list]
    result = select_reranked_chunks(
        query=question, chunks=payloads,
        top_n=top_n, score_threshold=float(cfg["score_threshold"]),
        min_keep=int(cfg["min_keep"]), instruct_template="qa",
    )
    if result.get("status") != "success":
        logger.warning("[feishu] chunk rerank failed: %s; fallback", result.get("message"))
        selected = _fallback_select(uc_list, top_n)
        logger.info("TIMING feishu.chunk_select mode=fallback query=%.24r input=%d selected=%d ms=%.1f",
                     query, len(uc_list), len(selected), _elapsed_ms(started))
        return selected
    selected_items = result.get("selected_results") or []
    if not selected_items:
        selected = _fallback_select(uc_list, top_n)
        logger.info("TIMING feishu.chunk_select mode=fallback_empty query=%.24r input=%d selected=%d ms=%.1f",
                     query, len(uc_list), len(selected), _elapsed_ms(started))
        return selected
    selected: List[UnifiedChunk] = []
    for item in selected_items:
        idx = item.get("index")
        if idx is None or idx < 0 or idx >= len(uc_list):
            continue
        selected.append(_clone_uc_with_score(
            uc_list[idx], float(item.get("relevance_score", 0.0)),
            int(item.get("rank", len(selected) + 1)), "rerank",
        ))
    if not selected:
        selected = _fallback_select(uc_list, top_n)
    logger.info("TIMING feishu.chunk_select mode=rerank query=%.24r input=%d selected=%d ms=%.1f",
                 query, len(uc_list), len(selected), _elapsed_ms(started))
    return selected


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


def _stabilize_query(sq: SubQuery, focus_text: str, mode: str) -> SubQuery:
    """如果 LLM 产出的 query 过于泛化，用 focus 锚点词替换。"""
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", (sq.query or "").lower())
    if not tokens:
        return sq
    non_generic = [t for t in tokens if t not in _FEISHU_GENERIC_QUERY_TOKENS]
    brand_only = all(bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", t or "")) for t in non_generic) if non_generic else True
    has_generic = any(t in (sq.query or "").lower() for t in _FEISHU_GENERIC_QUERY_TOKENS if t)
    if not (has_generic and brand_only):
        return sq
    # 从 focus 提取锚点词
    anchors = []
    for hint in sorted(set(list(_FEISHU_STRUCTURED_FOCUS_HINTS) + list(_FEISHU_CONCEPT_FOCUS_HINTS)), key=len, reverse=True):
        if hint in (focus_text or "").lower() and hint not in (sq.query or "").lower():
            anchors.append(hint)
            if len(anchors) >= 3:
                break
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]*", focus_text or ""):
        if token.lower() not in (sq.query or "").lower():
            anchors.insert(0, token)
    if not anchors:
        return sq
    kept = [t for t in tokens if t not in _FEISHU_GENERIC_QUERY_TOKENS][:2]
    new_query = " ".join(dict.fromkeys(kept + anchors[:3])).strip()
    if not new_query:
        return sq
    return SubQuery(
        query=new_query, target_focus=sq.target_focus,
        tags=list(sq.tags), indexes=list(sq.indexes),
        time_filter=sq.time_filter, query_type=sq.query_type,
        sub_query_id=sq.sub_query_id,
    )


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
        # 每个 focus 默认保留 1 条，最多 2 条
        kept = items[:1]
        for sq in kept:
            sq.query_type = sq.query_type or effective_type
        logger.info(
            "[feishu] plan sanitize focus=%s mode=%s before=%d after=%d qtypes=%s queries=%s",
            focus.focus_id, mode, len(items), len(kept),
            [sq.query_type for sq in kept], [sq.query for sq in kept],
        )
        # 稳定化：用 focus 锚点词替换泛化 query
        stabilized = [_stabilize_query(sq, focus.text, mode) for sq in kept]
        if [sq.query for sq in stabilized] != [sq.query for sq in kept]:
            logger.info("[feishu] plan query stabilized focus=%s before=%s after=%s",
                        focus.focus_id, [sq.query for sq in kept], [sq.query for sq in stabilized])
        sanitized.extend(stabilized)
    return sanitized


# ── doc/table merge 辅助 ──────────────────────────────────────────


def _candidate_doc_key(c: dict) -> str:
    return c.get("id") or c.get("document_id") or c.get("doc_id") or c.get("url") or ""


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
        sq_docs = sorted(
            batch.get("doc_candidates") or [],
            key=lambda c: (-float(c.get("candidate_rerank_score", 0) or 0),
                           int(c.get("candidate_rerank_rank", 10**9) or 10**9)),
        )
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
                existing["candidate_rerank_score"] = max(
                    float(existing.get("candidate_rerank_score", 0) or 0),
                    float(c.get("candidate_rerank_score", 0) or 0),
                )
                for lk in ("selected_focus_ids", "selected_sub_query_ids", "selected_feishu_urls", "selected_query_types"):
                    existing[lk] = sorted(set(existing.get(lk) or []) | set(c.get(lk) or []))
        doc_ids_by_sq[sq.sub_query_id] = selected_ids

    merged_list = sorted(
        merged_by_doc.values(),
        key=lambda c: (-float(c.get("candidate_rerank_score", 0) or 0), c.get("title", "")),
    )
    # 把 sq_hit_count 和 top1 信号写回 merged_list，供 route_hint 决策使用
    top1_keys = set(top1_by_sq.values())
    for c in merged_list:
        key = _candidate_doc_key(c)
        c["_sq_hit_count"] = sq_hit_count.get(key, 1)
        c["_is_top1"] = key in top1_keys
    return merged_list, doc_ids_by_sq


def _assign_doc_route_hints(doc_list: List[dict], *, min_block: int = 2) -> None:
    """lite 模式：选择性分配 route_hint（block_api vs mcp）。

    策略（优先级高→低）：
    1. multi-hit（被 2+ 个 sq 命中）→ block_api
    2. 每个 sq 的 rank-1 doc → block_api
    3. 保底：至少 min_block 篇走 block_api（按 score 降序补足）
    4. 其余 → mcp
    """
    block_keys: set = set()
    for c in doc_list:
        key = _candidate_doc_key(c)
        if c.get("_sq_hit_count", 1) >= 2 or c.get("_is_top1", False):
            block_keys.add(key)

    # 保底：按 score 降序补足 min_block
    if len(block_keys) < min_block:
        for c in doc_list:
            if len(block_keys) >= min_block:
                break
            key = _candidate_doc_key(c)
            block_keys.add(key)

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
    logger.info("[feishu] doc route_hint block=%d mcp=%d total=%d",
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
                existing["candidate_rerank_score"] = max(
                    float(existing.get("candidate_rerank_score", 0) or 0),
                    float(c.get("candidate_rerank_score", 0) or 0),
                )
                for lk in ("selected_focus_ids", "selected_sub_query_ids", "selected_feishu_urls",
                            "selected_query_types", "selected_sheet_ids", "selected_table_ids"):
                    existing[lk] = sorted(set(existing.get(lk) or []) | set(c.get(lk) or []))
                existing["read_query"] = existing.get("read_query") or c.get("read_query", "")
        table_ids_by_sq[sq.sub_query_id] = selected_ids
    merged_list = sorted(
        merged_by_parent.values(),
        key=lambda c: (-float(c.get("candidate_rerank_score", 0) or 0), c.get("title", "")),
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
    """把 table_pool 缓存的子结构名称注入 parent summary，让 rerank 能看到子表。"""
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
    """从结构缓存直接提取 selected_sheet_ids / selected_table_ids，省掉 child select rerank。"""
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
        sel = children[:TABLE_CHILD_SELECT_TOP_K]
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
    3. candidate_rerank_score 高的 parent
    4. low_yield（structure 子表多但 selected 少）的 parent 降权排后
    """
    structured = [b for b in batches if b.get("query_type") == "structured" and b.get("table_candidates")]
    if not structured:
        return []

    # Step 1: 每个 sq 保底 min_per_sq 张
    keep_ids: set = set()
    for batch in structured:
        ordered = sorted(batch.get("table_candidates") or [],
                         key=lambda c: (-float(c.get("candidate_rerank_score", 0) or 0), c.get("title", "")))
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
            -float(c.get("candidate_rerank_score", 0) or 0),    # 高分优先
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

    def __init__(
        self,
        cfg: Optional[SourceAgentConfig] = None,
        uat: str = "",
        default_query_type: str = "structured",
        doc_pool: Optional[object] = None,
        table_pool: Optional[object] = None,
        preset: str = "balanced",
    ):
        cfg = cfg or SourceAgentConfig()
        profile = get_profile("feishu")
        super().__init__(
            profile=profile, cfg=cfg,
            plan_only_agent=build_plan_only_agent(name="feishu_plan_only"),
            eval_only_agent=build_eval_only_agent(source_top_k=cfg.source_top_k, name="feishu_eval_only"),
            plan_eval_agent=(
                build_plan_eval_agent(source_top_k=cfg.source_top_k, name="feishu_plan_eval")
                if cfg.max_loops > 1 else None
            ),
        )
        self._uat = uat
        self._default_query_type = default_query_type
        self._doc_pool = doc_pool
        self._table_pool = table_pool
        self._preset = preset

    # ── plan_only prompt 注入 ─────────────────────────────────
    def _source_query_rules_block(self) -> str:
        return (
            SOURCE_QUERY_RULES["feishu"]
            + "\n\n提醒：tags / indexes 必须留空 []；time_filter 仅相对时间用 "
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
        return sanitized

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
        logger.info(
            "[feishu] table roi summary total=%d submitted=%d final_selected=%d wasted=%d skipped=%d read_ms=%.1f read_chunks=%d",
            len(rows), len(submitted), len(final_sel), len(wasted), len(skipped),
            sum(float(r.get("read_ms", 0) or 0) for r in submitted),
            sum(int(r.get("read_chunks", 0) or 0) for r in submitted),
        )
        for r in rows:
            logger.info(
                "[feishu] table roi token=%s title=%r doc_type=%s score=%.3f rank=%d "
                "selected_child_count=%d structure_child_count=%d child_ratio=%.3f "
                "read_ms=%.1f read_chunks=%d submit_selected=%s submit_skipped=%s final_selected=%s "
                "focus_ids=%s sub_query_ids=%s",
                str(r.get("token", ""))[:16], r.get("title", ""), r.get("doc_type", ""),
                float(r.get("candidate_rerank_score", 0) or 0),
                int(r.get("candidate_rerank_rank", 10**9) or 10**9),
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

    # ── 批处理路径：doc/table 完全并行流水线 ─────────────────
    async def _execute_batch(
        self, sub_queries: List[SubQuery], chunk_pool: Dict[str, ChunkEntry],
    ) -> None:
        batch_started = time.perf_counter()
        if self._doc_pool is None:
            raise RuntimeError("FeishuSourceAgent requires doc_pool; ensure FEISHU_UAT is configured")

        doc_pool_timeout = max(8.0, self.cfg.source_timeout_s * 0.8)

        # ── lite 模式：极简 MCP-only 路径（无 table）────────
        if self._preset == "lite":
            await self._execute_batch_lite(sub_queries, chunk_pool, doc_pool_timeout, batch_started)
            return

        # ── Step 1: 并行搜索 doc + table candidates ──────────
        async def _prepare_one(sq: SubQuery) -> dict:
            started = time.perf_counter()
            qt = _normalize_query_type(sq.query_type or self._default_query_type)
            focus_obj = self._current_focus_lookup.get(sq.target_focus)
            focus_text = focus_obj.text if focus_obj else sq.target_focus

            # doc 和 table search 完全并行
            doc_task = search_and_select_feishu_doc_candidates(
                sq.query, token=self._uat, time_filter=sq.time_filter, page_size=20,
                query_type=qt, focus_id=sq.target_focus, focus_text=focus_text,
                sub_query_id=sq.sub_query_id,
            )
            table_task: asyncio.Task = None
            if qt == "structured":
                table_task = search_and_select_feishu_table_candidates(
                    sq.query, token=self._uat, page_size=20,
                    query_type=qt, focus_id=sq.target_focus, focus_text=focus_text,
                    sub_query_id=sq.sub_query_id, table_pool=self._table_pool,
                    direct_limit=3 if self._preset in ("lite", "balanced") else 6,
                    wiki_limit=3 if self._preset in ("lite", "balanced") else 6,
                )

            if table_task is not None:
                doc_cands, table_cands = await asyncio.gather(
                    doc_task, table_task, return_exceptions=True,
                )
                if isinstance(doc_cands, Exception):
                    logger.warning("[feishu] doc search failed for %r: %s", sq.query, doc_cands)
                    doc_cands = []
                if isinstance(table_cands, Exception):
                    logger.warning("[feishu] table search failed for %r: %s", sq.query, table_cands)
                    table_cands = []
            else:
                try:
                    doc_cands = await doc_task
                except Exception as e:
                    logger.warning("[feishu] doc search failed for %r: %s", sq.query, e)
                    doc_cands = []
                table_cands = []

            logger.info("TIMING feishu.prepare_one sub_query_id=%s doc=%d table=%d ms=%.1f",
                         sq.sub_query_id or "unknown", len(doc_cands), len(table_cands),
                         _elapsed_ms(started))
            return {"sq": sq, "query_type": qt, "focus_text": focus_text,
                    "doc_candidates": doc_cands, "table_candidates": table_cands}

        prepare_started = time.perf_counter()
        prepared = await asyncio.gather(*[_prepare_one(sq) for sq in sub_queries])
        logger.info("TIMING feishu.batch_prepare_search sub_queries=%d ms=%.1f",
                     len(sub_queries), _elapsed_ms(prepare_started))

        # ── Step 2: doc submit + table trim + table fire-and-forget（同时进行）
        # 2a. doc submit（fire-and-forget）
        early_doc_list, doc_ids_by_sq = _merge_doc_demands(prepared)
        if early_doc_list and self._doc_pool is not None:
            # balanced/deep_dive：选择性 route_hint（block_api 只给高价值 doc）
            _assign_doc_route_hints(early_doc_list, min_block=2)
            try:
                await self._doc_pool.submit_and_read(early_doc_list, query="__global_feishu_route_plan__")
                logger.info("TIMING feishu.submit_doc docs=%d preset=%s ms=%.1f",
                             len(early_doc_list), self._preset, _elapsed_ms(prepare_started))
            except Exception as e:
                logger.warning("[feishu] doc submit failed: %s", e)

        # 2b. table trim + merge + fire-and-forget read
        _trim_table_parents(prepared, target=6, min_per_sq=2)
        table_list, table_ids_by_sq = _merge_table_demands(prepared)

        table_chunks_by_token: Dict[str, List[UnifiedChunk]] = {}
        table_events: Dict[str, asyncio.Event] = {}

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

            async def _read_table_one(cand: dict) -> None:
                token = _candidate_doc_key(cand)
                doc_type = str(cand.get("doc_type", "")).upper()
                rq = cand.get("read_query", "") or ""
                rd_started = time.perf_counter()
                try:
                    if doc_type == "SHEET":
                        chunks = await asyncio.to_thread(read_sheets, rq, [cand], self._uat)
                    elif doc_type == "BITABLE":
                        chunks = await asyncio.to_thread(read_bitables, rq, [cand], self._uat)
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
                        meta["candidate_rerank_score"] = float(cand.get("candidate_rerank_score", 0) or 0)
                        meta["candidate_source"] = cand.get("_source", "")
                        meta["candidate_sources"] = list(cand.get("_sources") or [])
                        meta["read_query"] = rq
                        meta["table_selected_child_count"] = int(cand.get("table_selected_child_count", 0) or 0)
                        meta["table_structure_child_count"] = int(cand.get("table_structure_child_count", 0) or 0)
                        meta["table_read_ms"] = read_ms
                        meta["table_read_chunks"] = len(chunks)
                        meta["table_submit_selected"] = True
                        meta["table_submit_skipped"] = False
                        normalized.append(UnifiedChunk(
                            text=uc.text, source_name=uc.source_name, source_url=uc.source_url,
                            source_tool=uc.source_tool, heading_path=uc.heading_path,
                            score=0.0, confidence_tier=uc.confidence_tier, metadata=meta,
                        ))
                    table_chunks_by_token[token] = normalized
                    logger.info("TIMING feishu.table_read token=%s type=%s chunks=%d ms=%.1f",
                                token[:16], doc_type, len(normalized), read_ms)
                except Exception as e:
                    logger.warning("[feishu] table read failed token=%s: %s", token[:16], e)
                    table_chunks_by_token[token] = []
                finally:
                    evt = table_events.get(token)
                    if evt:
                        evt.set()

            for cand in table_list:
                asyncio.create_task(_read_table_one(cand))

        logger.info("TIMING feishu.batch_prepare docs=%d tables=%d ms=%.1f",
                     len(early_doc_list), len(table_list), _elapsed_ms(prepare_started))

        # ── Step 4: 并行等待 doc + table → chunk rerank ──────
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
                    chunks = await self._doc_pool.wait_and_collect(d_ids, total_timeout=doc_pool_timeout)
                    logger.info("TIMING feishu.execute_one_doc_wait sub_query_id=%s docs=%d chunks=%d ms=%.1f",
                                 sq.sub_query_id, len(d_ids), len(chunks), _elapsed_ms(started))
                    return chunks
                except Exception as e:
                    logger.warning("[feishu] doc wait failed: %s", e)
                    return []

            async def _wait_table():
                if qt != "structured" or not t_ids:
                    return []
                collected: List[UnifiedChunk] = []
                for tid in t_ids:
                    evt = table_events.get(tid)
                    if evt:
                        try:
                            await asyncio.wait_for(evt.wait(), timeout=doc_pool_timeout)
                        except asyncio.TimeoutError:
                            logger.warning("[feishu] table wait timeout token=%s", tid[:16])
                    collected.extend(table_chunks_by_token.get(tid, []))
                logger.info("TIMING feishu.execute_one_table_wait sub_query_id=%s parents=%d chunks=%d ms=%.1f",
                             sq.sub_query_id, len(t_ids), len(collected), _elapsed_ms(started))
                return collected

            doc_chunks, table_chunks = await asyncio.gather(_wait_doc(), _wait_table())

            # doc chunks 做 rerank top-n；table chunks 直接全量保留（已经是精选子表）
            doc_selected: List[UnifiedChunk] = []
            if doc_chunks:
                doc_selected = _select_chunks_for_sub_query(
                    query=sq.query, focus_text=batch["focus_text"],
                    query_type=qt, uc_list=doc_chunks, top_n=self.cfg.sub_query_top_n,
                )
                logger.info("TIMING feishu.chunk_select_doc sub_query_id=%s raw=%d selected=%d ms=0",
                             sq.sub_query_id, len(doc_chunks), len(doc_selected))

            # table chunks 直接全量（不做 rerank，避免额外 API 调用）
            uc_list = doc_selected + table_chunks
            if not uc_list:
                return []
            logger.info("TIMING feishu.execute_one_total sub_query_id=%s doc=%d table=%d total=%d ms=%.1f",
                         sq.sub_query_id, len(doc_selected), len(table_chunks), len(uc_list), _elapsed_ms(started))
            return [_unified_to_chunk_entry(uc, sq.query, sq.target_focus, sq.sub_query_id) for uc in uc_list]

        batches_result = await asyncio.gather(*[_execute_one(b) for b in prepared])
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
                    "candidate_rerank_score": float(cand.get("candidate_rerank_score", 0) or 0),
                    "candidate_rerank_rank": int(cand.get("candidate_rerank_rank", 10**9) or 10**9),
                    "selected_focus_ids": list(cand.get("selected_focus_ids") or []),
                    "selected_sub_query_ids": list(cand.get("selected_sub_query_ids") or []),
                    "selected_query_types": list(cand.get("selected_query_types") or []),
                    "table_selected_child_count": int(cand.get("table_selected_child_count", 0) or 0),
                    "table_structure_child_count": int(cand.get("table_structure_child_count", 0) or 0),
                    "table_submit_selected": True,
                    "table_submit_skipped": False,
                    "table_read_ms": read_ms,
                    "table_read_chunks": len(cand_chunks),
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
        全部强制 MCP 路由，无 candidate rerank，无 table 流水线。
        """
        _LITE_DOC_CAP = 8  # 跨 sq 去重后最多提交的 doc 数

        # Step 1: 并行搜索 doc（直接取搜索结果，不做 candidate rerank）
        async def _search_docs_lite(sq: SubQuery) -> dict:
            started = time.perf_counter()
            qt = _normalize_query_type(sq.query_type or self._default_query_type)
            focus_obj = self._current_focus_lookup.get(sq.target_focus)
            focus_text = focus_obj.text if focus_obj else sq.target_focus
            doc_cands: List[dict] = []
            try:
                from feishu_read_tool.search import search_feishu_docs
                result = await asyncio.to_thread(
                    search_feishu_docs, query=sq.query, count=20,
                    uat=self._uat, time_filter=sq.time_filter,
                )
                raw = result.get("items", [])
                # 零结果重试
                if not raw:
                    simplified = None
                    try:
                        from tools.feishu_retrieve import _simplify_feishu_query
                        simplified = _simplify_feishu_query(sq.query)
                    except Exception:
                        pass
                    if simplified:
                        r2 = await asyncio.to_thread(
                            search_feishu_docs, query=simplified, count=20, uat=self._uat,
                        )
                        raw = r2.get("items", [])
                # 直接用搜索结果，注入 lineage 字段
                for rank, c in enumerate(raw, start=1):
                    c["selected_focus_ids"] = [sq.target_focus]
                    c["selected_sub_query_ids"] = [sq.sub_query_id]
                    c["selected_query_types"] = [qt]
                    c["candidate_rerank_score"] = max(0.0, 1.0 - (rank - 1) * 0.05)
                    c["candidate_rerank_rank"] = rank
                doc_cands = raw
            except Exception as e:
                logger.warning("[feishu][lite] doc search failed for %r: %s", sq.query, e)
            logger.info("TIMING feishu.lite.search_docs sub_query_id=%s raw=%d ms=%.1f",
                         sq.sub_query_id or "unknown", len(doc_cands), _elapsed_ms(started))
            return {"sq": sq, "query_type": qt, "focus_text": focus_text,
                    "doc_candidates": doc_cands, "table_candidates": []}

        prepare_started = time.perf_counter()
        prepared = await asyncio.gather(*[_search_docs_lite(sq) for sq in sub_queries])
        logger.info("TIMING feishu.lite.batch_prepare_search sub_queries=%d ms=%.1f",
                     len(sub_queries), _elapsed_ms(prepare_started))

        # Step 2: 跨 sq 去重合并，取前 _LITE_DOC_CAP 篇，全部 MCP
        doc_list, doc_ids_by_sq = _merge_doc_demands(prepared)
        doc_list = doc_list[:_LITE_DOC_CAP]
        for cand in doc_list:
            cand["route_hint"] = "mcp"
        logger.info("[feishu][lite] doc route_hint mcp=%d total=%d", len(doc_list), len(doc_list))

        if doc_list and self._doc_pool is not None:
            try:
                await self._doc_pool.submit_and_read(doc_list, query="__lite_mcp__")
                logger.info("TIMING feishu.lite.submit_doc docs=%d ms=%.1f",
                             len(doc_list), _elapsed_ms(prepare_started))
            except Exception as e:
                logger.warning("[feishu][lite] doc submit failed: %s", e)

        logger.info("TIMING feishu.lite.batch_prepare docs=%d tables=0 ms=%.1f",
                     len(doc_list), _elapsed_ms(prepare_started))

        # Step 3: 等待 doc chunks → doc-only rerank → chunk_pool
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
                doc_chunks = await self._doc_pool.wait_and_collect(d_ids, total_timeout=doc_pool_timeout)
                logger.info("TIMING feishu.lite.doc_wait sub_query_id=%s docs=%d chunks=%d ms=%.1f",
                             sq.sub_query_id, len(d_ids), len(doc_chunks), _elapsed_ms(started))
            except Exception as e:
                logger.warning("[feishu][lite] doc wait failed: %s", e)
                doc_chunks = []
            if not doc_chunks:
                return []
            selected = _select_chunks_for_sub_query(
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
        logger.info("TIMING feishu.lite.batch_total sub_queries=%d chunk_pool=%d ms=%.1f",
                     len(sub_queries), len(chunk_pool), _elapsed_ms(batch_started))
