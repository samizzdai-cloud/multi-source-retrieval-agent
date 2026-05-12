"""飞书多格式检索编排器 (Dispatcher)

职责：
  1. Phase A — 文档搜索（始终执行）：MCP + Wiki doc → doc_pool
  2. Phase B — 表格搜索（条件启动）：Direct + Wiki table → sheet/bitable readers
  3. 合并各 reader 输出的 UnifiedChunk[] 返回给 RetrievalAgent

搜索路由：
  - 文档类(doc/docx): MCP + Wiki doc → 按 title 去重 → doc_pool
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
from typing import Any, Callable, Dict, List, Optional


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
    return await loop.run_in_executor(executor, func_call)

from dotenv import load_dotenv

from feishu_read_tool.search import search_feishu_docs, search_feishu_tables
from feishu_read_tool.pipeline_models import ChunkContentType
from tools.core.models import UnifiedChunk
from tools.core.rerank import (
    build_focus_sub_query_question,
    get_doc_candidate_selection_config,
    select_search_candidates,
)

load_dotenv()
logger = logging.getLogger(__name__)

FEISHU_UAT = os.getenv("FEISHU_UAT", "")
TABLE_SEARCH_ROUTE_TOP_K = 6          # balanced/deep_dive 每路召回上限
TABLE_SEARCH_ROUTE_TOP_K_LITE = 3     # lite 每路召回上限
TABLE_CHILD_SELECT_TOP_K = 2          # 每个 parent 最多保留的 child 数
TABLE_CHILD_SELECT_GLOBAL_TOP_K = 10  # 跨所有 parent 的全局 child rerank 上限
TABLE_CHILD_SCORE_THRESHOLD = 0.18
TABLE_CHILD_MIN_KEEP = 1


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


# ── 预检策略：快速判断搜索结果是否值得深度读取 ──────────────────────────────────

# ── 零返回重试策略：简化 query + 品牌名大小写泛化 ────────────────────────────

# 已知品牌名（用于大小写泛化）— 与 source_query_config.BRAND_ROUTING 保持同步
_KNOWN_BRANDS = {
    "nubia": ["nubia", "Nubia", "NUBIA"],
    "redmagic": ["REDMAGIC", "Redmagic", "redmagic", "RedMagic"],
    "hypershell": ["Hypershell", "hypershell", "HYPERSHELL", "HyperShell"],
    "nothing": ["Nothing", "nothing", "NOTHING"],
}


def _try_brand_case_variant(query: str) -> Optional[str]:
    """尝试品牌名大小写变体，用于零返回时重试。

    检测 query 中的品牌名，返回不同大小写变体。
    例如：'NUBIA 运营' → 'nubia 运营' 或 'Nubia 运营'

    Returns:
        大小写变体的 query，或 None 表示无法生成变体
    """
    query_lower = query.lower()

    for brand_key, variants in _KNOWN_BRANDS.items():
        if brand_key in query_lower:
            # 找到当前使用的变体
            current_variant = None
            for v in variants:
                if v in query:
                    current_variant = v
                    break

            if current_variant:
                # 尝试其他变体（优先小写，次优首字母大写）
                for v in variants:
                    if v != current_variant:
                        new_query = query.replace(current_variant, v)
                        logger.info(
                            "_try_brand_case_variant: '%s' → '%s' (brand case)",
                            query, new_query
                        )
                        return new_query

    return None


def _simplify_feishu_query(query: str) -> Optional[str]:
    """简化飞书搜索 query，用于零返回时重试。

    [P2.7] 策略优先级：
    1. 如果 query 有 3+ 词，保留品牌名 + 第 2 关键词 + 文档类型提示
    2. 如果包含中文国家名，去掉国家名，加文档类型词
    3. 否则返回 None（不重试）
    """
    words = query.strip().split()
    if len(words) < 2:
        return None

    # [P2.7] 检测品牌名（保留为锚点）
    brand_word = None
    for w in words:
        if w.lower() in {"nubia", "hypershell", "redmagic", "nothing"}:
            brand_word = w
            break

    # [P3-A] 策略1：3+ 词 → 保留品牌 + 最泛化的动作词
    _BROAD_WORDS = {"销售", "运营", "策略", "营销", "推广", "市场", "合作", "进度", "表现", "计划"}
    if len(words) >= 3:
        if brand_word:
            # 找第一个泛化动作词
            action = next((w for w in words if w in _BROAD_WORDS and w != brand_word), None)
            if action:
                simplified = f"{brand_word} {action}"
            else:
                other = [w for w in words if w != brand_word]
                simplified = f"{brand_word} {other[0]}"
        else:
            simplified = " ".join(words[:2])
        logger.info("_simplify_feishu_query: '%s' → '%s' (brand+action)", query, simplified)
        return simplified

    # 策略2：去掉中文国家名
    country_words = {"美国", "日本", "中国", "韩国", "德国", "英国", "法国", "澳大利亚", "加拿大"}
    filtered = [w for w in words if w not in country_words]
    if len(filtered) < len(words) and filtered:
        simplified = " ".join(filtered)
        logger.info("_simplify_feishu_query: '%s' → '%s' (remove country)", query, simplified)
        return simplified

    # [P3-A] 策略3：品牌名单独搜索（最后兜底）
    if brand_word and len(words) == 2:
        logger.info("_simplify_feishu_query: '%s' → '%s' (brand only)", query, brand_word)
        return brand_word

    return None


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
    score: float,
    rank: int,
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
    out["candidate_rerank_score"] = score
    out["candidate_rerank_rank"] = rank
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
    merged["candidate_rerank_score"] = max(
        float(base.get("candidate_rerank_score", 0.0) or 0.0),
        float(extra.get("candidate_rerank_score", 0.0) or 0.0),
    )
    rank_a = int(base.get("candidate_rerank_rank", 10**9) or 10**9)
    rank_b = int(extra.get("candidate_rerank_rank", 10**9) or 10**9)
    merged["candidate_rerank_rank"] = min(rank_a, rank_b)
    return merged


def _normalize_table_candidate(candidate: dict, *, source: str) -> dict:
    normalized = dict(candidate)
    normalized["_source"] = normalized.get("_source") or source
    normalized["_sources"] = _merge_unique_strs(
        list(normalized.get("_sources") or []),
        [source],
    )
    return normalized


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
        for item in items[:direct_count][:direct_limit]
    ]
    wiki_items = [
        _normalize_table_candidate(item, source="wiki")
        for item in items[direct_count : direct_count + wiki_count][:wiki_limit]
    ]

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
            logger.info(
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
        logger.info(
            "TIMING feishu.table_structure kind=sheet parent=%s count=%d ms=%.1f cache=miss",
            parent.get("id", "")[:16],
            len(rows),
            _elapsed_ms(started),
        )
        return rows

    if doc_type == "BITABLE":
        if table_pool is not None and hasattr(table_pool, "get_bitable_structure"):
            rows = await table_pool.get_bitable_structure(parent)
            logger.info(
                "TIMING feishu.table_structure kind=bitable parent=%s count=%d ms=%.1f cache=pool",
                parent.get("id", "")[:16],
                len(rows),
                _elapsed_ms(started),
            )
            return rows
        from feishu_read_tool.basic_tool.bitable_read import list_tables

        rows = await asyncio.to_thread(list_tables, parent_id, token)
        logger.info(
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
    cfg = get_doc_candidate_selection_config(query_type)
    top_k = int(cfg["top_k"])
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

    query_type = _normalize_query_type(query_type)
    cfg = get_doc_candidate_selection_config(query_type)
    top_k = int(cfg["top_k"])
    score_threshold = float(cfg["score_threshold"])
    min_keep = int(cfg["min_keep"])
    question = build_focus_sub_query_question(focus_text or sub_query, sub_query)

    if len(candidates) == 1:
        return [
            _annotate_selected_candidate(
                candidates[0],
                focus_id=focus_id,
                sub_query_id=sub_query_id,
                score=1.0,
                rank=1,
            )
        ]

    # 候选 ≤ top_k → 全要：跳过 rerank API（省 300-500ms），按 search 原序赋分。
    # rerank 仅当候选数 > top_k 才有筛选价值；候选已经少于配额时 rerank 只是排序，
    # 而下游 chunk-level rerank/eval 会重新打分，doc 顺序对最终结果影响弱。
    if len(candidates) <= top_k:
        return [
            _annotate_selected_candidate(
                cand, focus_id=focus_id, sub_query_id=sub_query_id,
                score=max(0.0, 1.0 - rank_idx * 0.05),
                rank=rank_idx + 1,
            )
            for rank_idx, cand in enumerate(candidates)
        ]

    result = select_search_candidates(
        query=question,
        candidates=candidates,
        top_n=top_k,
        score_threshold=score_threshold,
        min_keep=min_keep,
        instruct_template="qa",
    )
    if result["status"] != "success":
        logger.warning(
            "feishu_retrieve: rerank doc selection failed for sub_query_id=%s, fallback to local ranking: %s",
            sub_query_id, result.get("message"),
        )
        return _fallback_select_doc_candidates(
            candidates,
            query=question,
            query_type=query_type,
            focus_id=focus_id,
            sub_query_id=sub_query_id,
        )

    selected = []
    for item in result["selected_results"]:
        selected.append(
            _annotate_selected_candidate(
                item["candidate"],
                focus_id=focus_id,
                sub_query_id=sub_query_id,
                score=float(item["relevance_score"]),
                rank=int(item["rank"]),
            )
        )
    if not selected:
        logger.warning(
            "feishu_retrieve: rerank doc selection returned 0 selected candidates for sub_query_id=%s, fallback to local ranking",
            sub_query_id,
        )
        return _fallback_select_doc_candidates(
            candidates,
            query=question,
            query_type=query_type,
            focus_id=focus_id,
            sub_query_id=sub_query_id,
        )
    logger.info(
        "feishu_retrieve: doc candidate selection sub_query_id=%s query_type=%s raw=%d selected=%d threshold=%.2f min_keep=%d top_k=%d",
        sub_query_id or "unknown",
        query_type,
        len(candidates),
        len(selected),
        score_threshold,
        min_keep,
        top_k,
    )
    return selected


async def search_and_select_feishu_doc_candidates(
    query: str,
    *,
    token: str,
    time_filter: Optional[str] = None,
    page_size: int = 20,
    query_type: str = "structured",
    focus_id: str = "",
    focus_text: str = "",
    sub_query_id: str = "",
    owner_ids: Optional[List[str]] = None,
    executor: Optional[concurrent.futures.Executor] = None,
) -> List[dict]:
    """执行 Feishu doc 搜索并返回当前 sub_query 选中的候选文档。

    搜索策略（按顺序，前一步有结果就不继续）：
    1. 原 query 直接搜索
    2. 零结果 → _simplify_feishu_query 简化重试
    3. 仍零结果 → _try_brand_case_variant 品牌变体 fallback

    ``executor``: 源专属线程池；None 时走 asyncio 默认（向后兼容）。
    """
    started = time.perf_counter()
    query_type = _normalize_query_type(query_type)
    try:
        # Step 1: 原 query 直接搜索（不做品牌变体并发）
        doc_result = await _exec_to_thread(
            executor, search_feishu_docs,
            query=query, count=page_size, uat=token, time_filter=time_filter, owner_ids=owner_ids,
        )
        doc_candidates = doc_result.get("items", [])

        # Step 2: 零结果 → 简化重试
        if not doc_candidates:
            simplified = _simplify_feishu_query(query)
            if simplified:
                logger.info("feishu_retrieve: ZERO results, retry with simplified '%s'", simplified)
                retry_result = await _exec_to_thread(
                    executor, search_feishu_docs,
                    query=simplified, count=page_size, uat=token, time_filter=time_filter, owner_ids=owner_ids,
                )
                doc_candidates = retry_result.get("items", [])

        # Step 3: 仍零结果 → 品牌变体 fallback
        if not doc_candidates:
            variant = _try_brand_case_variant(query)
            if variant:
                logger.info("feishu_retrieve: still ZERO, try brand variant '%s'", variant)
                variant_result = await _exec_to_thread(
                    executor, search_feishu_docs,
                    query=variant, count=page_size, uat=token, time_filter=time_filter, owner_ids=owner_ids,
                )
                doc_candidates = variant_result.get("items", [])

    except Exception as e:
        logger.warning("feishu_retrieve doc search failed: %s", e)
        doc_candidates = []

    if not doc_candidates:
        logger.info(
            "TIMING feishu.doc_candidate_select sub_query_id=%s raw=0 selected=0 ms=%.1f",
            sub_query_id or "unknown",
            _elapsed_ms(started),
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
                        logger.info("feishu_retrieve: filtered out doc candidate %s (edit_time: %s < %s)",
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
    page_size: int = 20,
    query_type: str = "structured",
    focus_id: str = "",
    focus_text: str = "",
    sub_query_id: str = "",
    table_pool: "Optional[object]" = None,
    direct_limit: int = TABLE_SEARCH_ROUTE_TOP_K,
    wiki_limit: int = TABLE_SEARCH_ROUTE_TOP_K,
    owner_ids: Optional[List[str]] = None,
    executor: Optional[concurrent.futures.Executor] = None,
) -> List[dict]:
    """执行 Feishu table 搜索，全局 child rerank，返回已选中子表的父文件候选。

    流程：
    1. search → _dedupe_table_candidates（按 direct_limit/wiki_limit 截断）
    2. 并发 structure prefetch（所有 parent）
    3. 全局 child rerank：把所有 parent 的 children 合并，一次 rerank 选 top
       TABLE_CHILD_SELECT_GLOBAL_TOP_K 个 child，再按 parent 分组，每个 parent
       最多保留 TABLE_CHILD_SELECT_TOP_K 个 child（按分数截断多余的）
    4. 返回有 selected children 的 parent 列表

    ``executor``: 源专属线程池；None 时走 asyncio 默认。
    """
    started = time.perf_counter()
    query_type = _normalize_query_type(query_type)
    if query_type != "structured":
        return []

    try:
        raw_result = await _exec_to_thread(
            executor, search_feishu_tables,
            query=query,
            count=page_size,
            uat=token,
            owner_ids=owner_ids,
        )
    except Exception as e:
        logger.warning("feishu_retrieve table search failed: %s", e)
        return []

    parent_candidates = _dedupe_table_candidates(
        raw_result, direct_limit=direct_limit, wiki_limit=wiki_limit,
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
    question = build_focus_sub_query_question(focus_text or query, query)
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

    # ── Step 3: 全局 child rerank ─────────────────────────────
    global_top_k = TABLE_CHILD_SELECT_GLOBAL_TOP_K
    per_parent_cap = TABLE_CHILD_SELECT_TOP_K

    if len(all_child_candidates) <= global_top_k:
        selected_child_cands = all_child_candidates
        selected_child_scores: Dict[str, float] = {
            cc["id"]: _default_table_parent_score(cc.get("_parent_rank", 1))
            for cc in all_child_candidates
        }
    else:
        rerank_result = select_search_candidates(
            query=question,
            candidates=all_child_candidates,
            top_n=global_top_k,
            score_threshold=TABLE_CHILD_SCORE_THRESHOLD,
            min_keep=TABLE_CHILD_MIN_KEEP,
            instruct_template="qa",
        )
        if rerank_result.get("status") == "success":
            selected_child_cands = list(rerank_result.get("selected_candidates") or [])
            selected_child_scores = {
                item["candidate"]["id"]: float(item.get("relevance_score", 0.0) or 0.0)
                for item in (rerank_result.get("selected_results") or [])
                if item.get("candidate", {}).get("id")
            }
        else:
            logger.warning(
                "feishu_retrieve: global child rerank failed sub_query_id=%s: %s",
                sub_query_id or "unknown", rerank_result.get("message"),
            )
            selected_child_cands = all_child_candidates[:global_top_k]
            selected_child_scores = {
                cc["id"]: _default_table_parent_score(cc.get("_parent_rank", 1))
                for cc in selected_child_cands
            }

    # ── Step 4: 按 parent 分组，每个 parent 最多 per_parent_cap 个 child ──
    # 按分数降序，超出 cap 的截断
    children_selected_by_parent: Dict[str, List[dict]] = {}
    for cc in selected_child_cands:
        parent = parent_by_child_id.get(cc["id"])
        if parent is None:
            continue
        parent_id = parent.get("id", "")
        if parent_id not in children_selected_by_parent:
            children_selected_by_parent[parent_id] = []
        if len(children_selected_by_parent[parent_id]) < per_parent_cap:
            children_selected_by_parent[parent_id].append(cc)

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
        best_score = max(
            (selected_child_scores.get(cc["id"], 0.0) for cc in sel_children),
            default=_default_table_parent_score(parent_rank),
        )

        annotated = _annotate_selected_candidate(
            parent,
            focus_id=focus_id,
            sub_query_id=sub_query_id,
            score=best_score,
            rank=parent_rank,
        )
        annotated["selected_query_types"] = sorted(
            set(annotated.get("selected_query_types") or []) | {query_type}
        )
        raw_children = children_by_parent.get(parent_id, [])
        annotated["table_structure_child_count"] = len(raw_children)
        annotated["table_selected_child_count"] = len(sel_children)

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
            -float(c.get("candidate_rerank_score", 0.0) or 0.0),
            int(c.get("candidate_rerank_rank", 10**9) or 10**9),
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
            "candidate_rerank_score": candidate.get("candidate_rerank_score", 0.0),
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
            logger.info(
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
        focus_text:    当前 sub_query 对应的 focus 文本（rerank question 用）
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
                logger.info(
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
    logger.info(
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
