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
TABLE_SEARCH_ROUTE_TOP_K = 6          # balanced/deep_dive 每路召回上限(单路 / Path A 现状)
TABLE_SEARCH_ROUTE_TOP_K_LITE = 3     # lite 每路召回上限(单路 / Path A 现状)
TABLE_CHILD_SELECT_TOP_K = 2          # 每个 parent 最多保留的 child 数
TABLE_CHILD_SELECT_GLOBAL_TOP_K = 10  # 跨所有 parent 的全局 child rerank 上限
TABLE_CHILD_SCORE_THRESHOLD = 0.18
TABLE_CHILD_MIN_KEEP = 1

# Dual-path table 召回: Path A/B 不对称配置
# Path A (原 query): 收缩到 2 per route → 6 candidates max → 资源主要节省点
# Path B (变体 + RRF): 保留 3 per route → 6 candidates per variant,RRF 后 cap 3
# 总进入 Step 2 (structure fetch) 的 unique parents ≈ 5-7,与单路 6 持平
TABLE_PATH_A_ROUTE_TOP_K = 3               # Path A 每路每个 route 的 cap (回退到 3)
TABLE_PATH_A_ROUTE_TOP_K_LITE = 2          # lite Path A cap
TABLE_PATH_B_ROUTE_TOP_K = 3               # Path B 每路每个 route 的 cap (保持)
TABLE_PATH_B_ROUTE_TOP_K_LITE = 2          # lite Path B cap
TABLE_PATH_B_RRF_CAP = 3                   # Path B RRF 合并后输出 cap (非 lite)
TABLE_PATH_B_RRF_CAP_LITE = 2              # Path B RRF 合并后输出 cap (lite)

# Time-filter widening: planner sets time_filter from user's date intent (e.g. "1月15日"
# → ">2026-01-15"). But edit_time often doesn't reflect content time — narrow cutoff
# excludes valid docs. Widen left boundary, then narrow via date-anchor content filter.
TIME_FILTER_LEFT_BUFFER_DAYS = 90


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


# ── 日期锚点抽取与候选预过滤 ─────────────────────────────────────────────────────
# 用户 query 中的日期(如 "2026年1月15日" / "1月15日到21日")会被 planner 蒸馏掉，
# 进入 rerank 时只剩通用关键词("Nubia 销售")。但目标文档的 title/summary 经常含日期
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


# ── 双路召回:query 变体生成 + RRF 合并 ────────────────────────────────────────
# Path A 用原 sub_query (单条 + 0 结果 fallback);
# Path B 用 user-anchored 变体并行 fan-out + RRF 合并。
# 两路独立 fetch,合并后统一过 time_filter + date_prefilter + rerank。

# 高信息量名词:从 focus_text 抽 user 真实说的词,补回 sub_query 末尾
_USER_ANCHOR_PATTERNS = [
    re.compile(r"[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*"),       # 英文专有名 e.g. "X Ultra", "Paid Media"
    re.compile(r"[A-Z]{2,}[0-9]*"),                              # 缩写 e.g. "UGC", "EDM", "ROAS"
    re.compile(r"[A-Z][0-9]+(?:\s+[A-Za-z]+)*"),                  # 型号 e.g. "Z80 Ultra", "Z70"
    re.compile(r"\d{4}-\d{1,2}-\d{1,2}"),                        # 日期 e.g. "2026-01-15"
    re.compile(r"\d{1,2}月\d{1,2}日"),                            # 中文日期
]

_GENERIC_NOUNS_TO_DROP = {
    "运营", "销售", "营销", "市场", "计划", "策略", "管理", "业务",
    "项目", "工作", "内容", "合作", "情况", "方案", "进度", "表现",
    "数据", "报告", "总结", "复盘", "动态", "情况",
}


def _extract_user_anchors(focus_text: str, max_anchors: int = 3) -> List[str]:
    """从 focus_text 抽取高信息量的 user 原词:专有名、缩写、型号、日期。"""
    if not focus_text:
        return []
    anchors: List[str] = []
    for pat in _USER_ANCHOR_PATTERNS:
        for m in pat.findall(focus_text):
            m = m.strip()
            if m and m.lower() not in {a.lower() for a in anchors}:
                anchors.append(m)
                if len(anchors) >= max_anchors:
                    return anchors
    return anchors


def _build_query_variants(sub_query: str, focus_text: str) -> List[str]:
    """Path B 变体生成。返回 1-2 条"与原 sub_query 不同"的搜索 query。

    策略:
      V1. user-anchored: 原 sub_query + 从 focus_text 抽的高信息量名词
      V2. brand-only:   只保留 sub_query 中的品牌/英文词,做宽召回兜底
    """
    variants: List[str] = []
    seen = {sub_query.strip().lower()}

    # V1: 加 user 锚点
    anchors = _extract_user_anchors(focus_text)
    if anchors:
        v1 = f"{sub_query.strip()} {' '.join(anchors)}".strip()
        if v1.lower() not in seen:
            variants.append(v1)
            seen.add(v1.lower())

    # V2: 品牌-only(从原 sub_query 抽英文专有名)
    brand_tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]+", sub_query)
    if brand_tokens:
        v2 = " ".join(brand_tokens[:2])
        if v2.lower() not in seen and v2.lower() != sub_query.strip().lower():
            variants.append(v2)
            seen.add(v2.lower())

    return variants


def _rrf_merge(result_lists: List[List[dict]], k: int = 60) -> List[dict]:
    """Reciprocal Rank Fusion: 多组排名结果合并,重复出现的 doc 累加分数。

    返回按融合分数降序的去重列表。
    """
    scores: Dict[str, float] = {}
    cand_by_key: Dict[str, dict] = {}
    for results in result_lists:
        for rank_idx, cand in enumerate(results):
            key = cand.get("id") or cand.get("document_id") or cand.get("doc_id") or cand.get("url") or ""
            if not key:
                continue
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank_idx + 1)
            if key not in cand_by_key:
                cand_by_key[key] = cand
    ordered_keys = sorted(scores.keys(), key=lambda x: -scores[x])
    return [cand_by_key[k] for k in ordered_keys]


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
                        logger.debug(
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
        logger.debug("_simplify_feishu_query: '%s' → '%s' (brand+action)", query, simplified)
        return simplified

    # 策略2：去掉中文国家名
    country_words = {"美国", "日本", "中国", "韩国", "德国", "英国", "法国", "澳大利亚", "加拿大"}
    filtered = [w for w in words if w not in country_words]
    if len(filtered) < len(words) and filtered:
        simplified = " ".join(filtered)
        logger.debug("_simplify_feishu_query: '%s' → '%s' (remove country)", query, simplified)
        return simplified

    # [P3-A] 策略3：品牌名单独搜索（最后兜底）
    if brand_word and len(words) == 2:
        logger.debug("_simplify_feishu_query: '%s' → '%s' (brand only)", query, brand_word)
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

    # Date-anchor content pre-filter: when user query has date intent, narrow
    # candidates to those whose title/summary contains the date before rerank.
    # No-op if no date detected or zero candidates match (preserves recall).
    candidates = _filter_candidates_by_date_anchors(
        candidates, focus_text=focus_text, sub_query=sub_query, sub_query_id=sub_query_id,
    )
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
    logger.debug(
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
    variants: Optional[List[str]] = None,
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

    # ── Path A: 原 query + 0 结果 fallback 链 ──
    async def _path_a() -> List[dict]:
        try:
            r = await _exec_to_thread(
                executor, search_feishu_docs,
                query=query, count=page_size, uat=token, time_filter=time_filter, owner_ids=owner_ids,
            )
            cands = r.get("items", [])
            if not cands:
                simplified = _simplify_feishu_query(query)
                if simplified:
                    logger.debug("feishu_retrieve: PathA ZERO, retry simplified '%s'", simplified)
                    r = await _exec_to_thread(
                        executor, search_feishu_docs,
                        query=simplified, count=page_size, uat=token, time_filter=time_filter, owner_ids=owner_ids,
                    )
                    cands = r.get("items", [])
            if not cands:
                variant = _try_brand_case_variant(query)
                if variant:
                    logger.debug("feishu_retrieve: PathA still ZERO, try brand variant '%s'", variant)
                    r = await _exec_to_thread(
                        executor, search_feishu_docs,
                        query=variant, count=page_size, uat=token, time_filter=time_filter, owner_ids=owner_ids,
                    )
                    cands = r.get("items", [])
            return cands
        except Exception as e:
            logger.warning("feishu_retrieve PathA failed: %s", e)
            return []

    # ── Path B: 多变体并行 + RRF 合并 ──
    # 变体来源优先级:1) 调用方传入的 variants (source_agent 在 _enrich_sub_queries 中生成);
    # 2) 回退到本地 _build_query_variants (向后兼容,无 enrich 时仍能工作)
    # Path B page_size 减半: 变体是探索性召回,不需要 path A 同等深度,
    # 减半可降低 wiki/mcp API 调用开销 ~30-40%,RRF 合并后仍有足够候选送 rerank。
    path_b_page_size = max(5, page_size // 2)
    async def _path_b() -> List[dict]:
        path_b_variants = list(variants) if variants else _build_query_variants(query, focus_text or query)
        if not path_b_variants:
            return []
        logger.debug(
            "feishu_retrieve: PathB sub_query_id=%s page_size=%d variants=%s",
            sub_query_id or "unknown", path_b_page_size, path_b_variants,
        )
        try:
            # Path B 跳过 MCP: Path A 已经覆盖 MCP,Path B 只需 Wiki 补充长尾召回。
            # 省 ~0.5-1s API RTT,避免 MCP 重复调用。
            results = await asyncio.gather(*[
                _exec_to_thread(
                    executor, search_feishu_docs,
                    query=v, count=path_b_page_size, uat=token, time_filter=time_filter, owner_ids=owner_ids,
                    skip_mcp=True,
                )
                for v in path_b_variants
            ], return_exceptions=True)
            valid = [r.get("items", []) if isinstance(r, dict) else [] for r in results]
            merged = _rrf_merge(valid)
            return merged
        except Exception as e:
            logger.warning("feishu_retrieve PathB failed: %s", e)
            return []

    raw_a, raw_b = await asyncio.gather(_path_a(), _path_b())

    # 记录两路独立召回情况
    logger.info(
        "feishu_retrieve: dual_path sub_query_id=%s pathA=%d pathB=%d",
        sub_query_id or "unknown", len(raw_a), len(raw_b),
    )

    if not raw_a and not raw_b:
        logger.info(
            "TIMING feishu.doc_candidate_select sub_query_id=%s raw=0 selected=0 ms=%.1f",
            sub_query_id or "unknown", _elapsed_ms(started),
        )
        return []

    # 合并去重(A 顺序优先)。time_filter 应用于合并后的池子,避免 2x metadata fetch。
    doc_candidates = _dedupe_candidates(raw_a, raw_b)

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

    # Lite 模式无 rerank: 按 path A=2 / path B=3 平分,去重后保留 ≤5 个 doc 进入读取阶段
    preset = (os.getenv("RAG_PRESET", "balanced") or "balanced").strip().lower()
    if preset == "lite":
        # 用 doc_id 标记每个候选属于哪一路(在 time_filter 之后仍保留 raw_a/raw_b 顺序)
        survived_ids = set()
        for c in doc_candidates:
            cid = c.get("id") or c.get("document_id") or c.get("doc_id") or c.get("url") or ""
            if cid:
                survived_ids.add(cid)
        a_filtered_ordered = [c for c in raw_a
                              if (c.get("id") or c.get("document_id") or c.get("doc_id") or c.get("url") or "") in survived_ids]
        b_filtered_ordered = [c for c in raw_b
                              if (c.get("id") or c.get("document_id") or c.get("doc_id") or c.get("url") or "") in survived_ids]

        a_pick = a_filtered_ordered[:2]
        a_pick_ids = {c.get("id") or c.get("document_id") or c.get("doc_id") or c.get("url") or "" for c in a_pick}
        b_pick = [c for c in b_filtered_ordered
                  if (c.get("id") or c.get("document_id") or c.get("doc_id") or c.get("url") or "") not in a_pick_ids][:3]

        selected = []
        for rank_idx, cand in enumerate(a_pick + b_pick):
            selected.append(_annotate_selected_candidate(
                cand, focus_id=focus_id, sub_query_id=sub_query_id,
                score=max(0.0, 1.0 - rank_idx * 0.05), rank=rank_idx + 1,
            ))
        logger.debug(
            "feishu_retrieve: lite_split sub_query_id=%s pathA=%d pathB=%d selected=%d",
            sub_query_id or "unknown", len(a_pick), len(b_pick), len(selected),
        )
    else:
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
    variants: Optional[List[str]] = None,
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

    # Dual-path table 召回(Path A/B 不对称):
    # - Path A 收缩到 2/route → 6 raw → 减少 structure fetch 数量(主要省时点)
    # - Path B 保持 3/route → 通过 variants 扩宽召回
    preset = (os.getenv("RAG_PRESET", "balanced") or "balanced").strip().lower()
    is_lite = preset == "lite"
    path_a_direct = TABLE_PATH_A_ROUTE_TOP_K_LITE if is_lite else TABLE_PATH_A_ROUTE_TOP_K
    path_a_wiki = path_a_direct
    path_b_direct = TABLE_PATH_B_ROUTE_TOP_K_LITE if is_lite else TABLE_PATH_B_ROUTE_TOP_K
    path_b_wiki = path_b_direct
    path_b_cap = TABLE_PATH_B_RRF_CAP_LITE if is_lite else TABLE_PATH_B_RRF_CAP

    # ── Path A: 原 query 单路调用 search_feishu_tables (收缩 2/route) ──
    async def _path_a() -> List[dict]:
        try:
            r = await _exec_to_thread(
                executor, search_feishu_tables,
                query=query, count=page_size, uat=token, owner_ids=owner_ids,
            )
            return _dedupe_table_candidates(
                r, direct_limit=path_a_direct, wiki_limit=path_a_wiki,
            )
        except Exception as e:
            logger.warning("feishu_retrieve table PathA failed: %s", e)
            return []

    # ── Path B: 多变体并行 search,RRF 合并 (保留 3/route) ──
    async def _path_b() -> List[dict]:
        b_variants = list(variants or [])
        if not b_variants:
            return []
        try:
            results = await asyncio.gather(*[
                _exec_to_thread(
                    executor, search_feishu_tables,
                    query=v, count=page_size, uat=token, owner_ids=owner_ids,
                )
                for v in b_variants
            ], return_exceptions=True)
            per_variant_lists: List[List[dict]] = []
            for r in results:
                if isinstance(r, dict):
                    deduped = _dedupe_table_candidates(
                        r, direct_limit=path_b_direct, wiki_limit=path_b_wiki,
                    )
                    per_variant_lists.append(deduped)
            merged = _rrf_merge(per_variant_lists)
            return merged[:path_b_cap]
        except Exception as e:
            logger.warning("feishu_retrieve table PathB failed: %s", e)
            return []

    raw_a, raw_b = await asyncio.gather(_path_a(), _path_b())
    logger.info(
        "feishu_retrieve: table dual_path sub_query_id=%s variants=%s pathA=%d pathB=%d",
        sub_query_id or "unknown", list(variants or [])[:3], len(raw_a), len(raw_b),
    )

    parent_candidates = _dedupe_candidates(raw_a, raw_b)
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
