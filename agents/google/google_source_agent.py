"""GoogleSourceAgent——Google Workspace / 搜索源适配器。

Phase 4 完整版：
- 继承 ``SourceAgent`` 通用骨架；单/多 loop 由 ``SourceAgentConfig`` 决定
- 引入 SheetPool：跨 sub_query 合并 spreadsheet 元数据获取 + qwen rerank 选 sheets + batchGet 只读选中 sheets
- 非原生文件（Excel/CSV/Word/PDF/Slide）走原有 drive_content_reader 路径
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Dict, List, Optional, Set, Tuple

from tools.core.models import ChunkEntry, SourceAgentConfig, UnifiedChunk
from tools.core.source_profiles import get_profile
from tools.core.rerank import (
    build_focus_sub_query_question,
    get_chunk_selection_config,
    select_reranked_chunks,
)

from agents.source_agent_base import Focus, SourceAgent, SubQuery
from google.adk.agents.llm_agent import LlmAgent


logger = logging.getLogger(__name__)


# ── MIME 类型常量（与 drive_content_reader 对齐）─────────────────────

_MIME_SHEETS = "application/vnd.google-apps.spreadsheet"
_MIME_EXCEL = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MIME_EXCEL_OLD = "application/vnd.ms-excel"
_MIME_EXCEL_MACRO = "application/vnd.ms-excel.sheet.macroEnabled.12"
_MIME_CSV = "text/csv"
_MIME_DOCS = "application/vnd.google-apps.document"
_MIME_SLIDES = "application/vnd.google-apps.presentation"
_MIME_WORD = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_MIME_WORD_OLD = "application/msword"
_MIME_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_MIME_TXT = "text/plain"

_TABLE_MIMES = {_MIME_SHEETS, _MIME_EXCEL, _MIME_EXCEL_OLD, _MIME_EXCEL_MACRO, _MIME_CSV}


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def _drive_file_size(file_meta: dict) -> Optional[int]:
    """统一读取 Drive 文件大小，兼容 size / quotaBytesUsed。"""
    if not file_meta:
        return None
    size = file_meta.get("size")
    if size is None:
        size = file_meta.get("quotaBytesUsed")
    if size in (None, "", "N/A"):
        return None
    try:
        return int(size)
    except (TypeError, ValueError):
        return None


def _uc_to_rerank_payload(uc: UnifiedChunk) -> dict:
    return {
        "text": uc.text or "",
        "source_name": uc.source_name or "",
        "heading_path": uc.heading_path or "",
    }


def _fallback_select(uc_list: List[UnifiedChunk], top_n: int) -> List[UnifiedChunk]:
    return sorted(uc_list, key=lambda x: float(x.score or 0.0), reverse=True)[:top_n]


# ── 跨 query 文件级 LRU cache ────────────────────────────────────────────
# 缓存的是 raw GoogleChunk list（pre query-aware scoring），避免每个 query 重新
# 跑一次 read_drive_chunks。命中后调用方仍要做 _google_chunk_to_unified（含 query 评分）。
_FILE_CONTENT_CACHE_LOCK = threading.Lock() if False else None  # threading 已 imported 时才用
import threading as _threading
_FILE_CONTENT_CACHE_LOCK = _threading.Lock()
_FILE_CONTENT_CACHE: "Dict[str, tuple]" = {}  # cache_key → (timestamp, gc_list)


def _cache_key(file_id: str, mime: str, sheet_titles: Optional[tuple] = None) -> str:
    if sheet_titles:
        return f"{file_id}|{mime}|{','.join(sorted(sheet_titles))}"
    return f"{file_id}|{mime}"


def _cache_get(key: str, ttl_s: float):
    with _FILE_CONTENT_CACHE_LOCK:
        item = _FILE_CONTENT_CACHE.get(key)
        if not item:
            return None
        ts, gc_list = item
        if time.time() - ts > ttl_s:
            _FILE_CONTENT_CACHE.pop(key, None)
            return None
        return gc_list


def _cache_put(key: str, gc_list, max_files: int):
    with _FILE_CONTENT_CACHE_LOCK:
        if len(_FILE_CONTENT_CACHE) >= max_files:
            # FIFO 驱逐最旧的 1/4
            oldest = sorted(_FILE_CONTENT_CACHE.items(), key=lambda kv: kv[1][0])[: max_files // 4]
            for k, _ in oldest:
                _FILE_CONTENT_CACHE.pop(k, None)
        _FILE_CONTENT_CACHE[key] = (time.time(), gc_list)


# 月份/季度双语映射：Drive 全文索引对中英文敏感，搜 "March" 会漏掉只含 "3月" 的文件
# 在 keyword_groups 内对识别到的月份/季度词自动 OR 同义词，提高召回稳健性
_MONTH_BILINGUAL = {
    "january":  ["1月", "jan"],   "1月":  ["january", "jan"],
    "february": ["2月", "feb"],   "2月":  ["february", "feb"],
    "march":    ["3月", "mar"],   "3月":  ["march", "mar"],
    "april":    ["4月", "apr"],   "4月":  ["april", "apr"],
    "may":      ["5月"],            "5月":  ["may"],
    "june":     ["6月", "jun"],   "6月":  ["june", "jun"],
    "july":     ["7月", "jul"],   "7月":  ["july", "jul"],
    "august":   ["8月", "aug"],   "8月":  ["august", "aug"],
    "september":["9月", "sep", "sept"], "9月": ["september", "sep", "sept"],
    "october":  ["10月", "oct"],  "10月": ["october", "oct"],
    "november": ["11月", "nov"],  "11月": ["november", "nov"],
    "december": ["12月", "dec"],  "12月": ["december", "dec"],
    # 季度
    "q1": ["第一季度", "一季度"], "第一季度": ["q1"],
    "q2": ["第二季度", "二季度"], "第二季度": ["q2"],
    "q3": ["第三季度", "三季度"], "第三季度": ["q3"],
    "q4": ["第四季度", "四季度"], "第四季度": ["q4"],
}


def _adaptive_cap(n_candidates: int, abundant_cap: int, moderate_cap: int) -> int:
    """根据候选数量自适应 top_n cap：
    - sparse  (<=3): 不砍（候选已经稀缺，再砍直接召回崩）
    - moderate(4-7): 用 moderate_cap（稍宽）
    - abundant(>=8): 用 abundant_cap（preset 默认 aggressive）
    """
    if n_candidates <= 3:
        return n_candidates
    if n_candidates <= 7:
        return min(n_candidates, moderate_cap)
    return abundant_cap


def _expand_bilingual(keyword_groups: Optional[List[List[str]]]) -> Optional[List[List[str]]]:
    """对每个 keyword group，识别月份/季度词并 OR 上中英同义词。
    OR 不会减少召回，只会增加。低成本提升 Drive 中英混库下的命中率。"""
    if not keyword_groups:
        return keyword_groups
    expanded = []
    for grp in keyword_groups:
        new_grp = list(grp)
        seen = {t.lower() for t in new_grp}
        for term in list(grp):
            t_low = term.lower().strip()
            if t_low in _MONTH_BILINGUAL:
                for syn in _MONTH_BILINGUAL[t_low]:
                    if syn.lower() not in seen:
                        new_grp.append(syn)
                        seen.add(syn.lower())
        expanded.append(new_grp)
    return expanded


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[一-鿿]")


def _tokenize(text: str) -> List[str]:
    if not text:
        return []
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _normalize_google_query_type(query_type: Optional[str]) -> str:
    """Google keeps concept/personal_recent explicit; everything else is structured."""
    if query_type in ("concept", "personal_recent"):
        return query_type
    return "structured"


def _sheet_files_cap_for_sq_query_type(cfg: SourceAgentConfig, query_type: str) -> int:
    """Per-sub_query Sheet 文件 cap，按该 sq 的 query_type 取值。

    structured / personal_recent 用 structured cap；concept 走更紧的 cap。
    """
    if query_type == "concept":
        return max(0, int(cfg.google_sheet_files_per_sq_concept))
    return max(0, int(cfg.google_sheet_files_per_sq_structured))


def _drive_has_time_or_fresh_intent(text: str) -> bool:
    """Detect explicit time / freshness intent in a query.

    Used to gate stale-year penalties — when there is no time intent, we keep
    older artifacts at full weight (long-shelf-life materials).
    """
    if not text:
        return False
    lowered = text.lower()
    if re.search(r"\b20\d{2}\b|20\d{2}年|\bq[1-4]\b|[1-9]月|1[0-2]月", lowered):
        return True
    return any(
        term in lowered
        for term in (
            "今年", "本年", "当前", "最新", "最近", "近况", "近期",
            "current", "latest", "recent", "this year", "new", "launch",
            "open sales", "上线", "首发", "开售",
        )
    )


# ---- Family / native classification ------------------------------------

_FAMILY_BY_MIME: Dict[str, str] = {
    _MIME_DOCS: "doc", _MIME_WORD: "doc", _MIME_WORD_OLD: "doc", _MIME_TXT: "doc",
    _MIME_SHEETS: "sheet", _MIME_EXCEL: "sheet", _MIME_EXCEL_OLD: "sheet", _MIME_EXCEL_MACRO: "sheet", _MIME_CSV: "sheet",
    _MIME_SLIDES: "slide", _MIME_PPTX: "slide",
}

_NATIVE_MIMES: Set[str] = {_MIME_DOCS, _MIME_SHEETS, _MIME_SLIDES}


def _family_of(mime: Optional[str]) -> Optional[str]:
    """Classify a Drive mime into sheet/slide/doc, or None for unknown."""
    return _FAMILY_BY_MIME.get(mime or "")


def _is_native(mime: Optional[str]) -> bool:
    return (mime or "") in _NATIVE_MIMES


def _format_label(mime: Optional[str]) -> str:
    """Short Format tag for Qwen rerank candidate summary."""
    return {
        _MIME_DOCS: "native_doc", _MIME_SHEETS: "native_sheet", _MIME_SLIDES: "native_slide",
        _MIME_WORD: "docx", _MIME_WORD_OLD: "doc", _MIME_TXT: "txt",
        _MIME_EXCEL: "excel", _MIME_EXCEL_OLD: "excel", _MIME_EXCEL_MACRO: "excel", _MIME_CSV: "csv",
        _MIME_PPTX: "pptx",
    }.get(mime or "", "unknown")


_TITLE_NORM_STRIP_RE = re.compile(r"[\s\W_]+", flags=re.UNICODE)
_TITLE_NORM_TRAILING_YEAR_RE = re.compile(r"(?:[\-_\s/]?20\d{2}(?:年)?)+\s*$")


def _normalize_title_for_dedup(title: str) -> str:
    """Lowercase, strip trailing year suffixes, drop whitespace/punct.

    Used for native ↔ non-native duplicate detection.
    """
    if not title:
        return ""
    t = title.strip().lower()
    t = _TITLE_NORM_TRAILING_YEAR_RE.sub("", t)
    t = _TITLE_NORM_STRIP_RE.sub("", t)
    return t


def _dedup_native_vs_nonnative(files: List[dict]) -> List[dict]:
    """Within each family, drop non-native files whose normalized title matches
    or is contained in a native sibling's title (length diff <= 5 chars).

    Native preference is fully captured here: when a duplicate exists we keep
    the native entry, drop the non-native. Files from other families are
    untouched.
    """
    if not files:
        return files
    native_norms_by_family: Dict[str, List[str]] = {}
    for f in files:
        if not _is_native(f.get("mimeType")):
            continue
        fam = _family_of(f.get("mimeType"))
        if not fam:
            continue
        norm = _normalize_title_for_dedup(f.get("name") or "")
        if norm:
            native_norms_by_family.setdefault(fam, []).append(norm)

    if not native_norms_by_family:
        return files

    kept: List[dict] = []
    for f in files:
        mime = f.get("mimeType")
        if _is_native(mime):
            kept.append(f)
            continue
        fam = _family_of(mime)
        norm = _normalize_title_for_dedup(f.get("name") or "")
        if not fam or not norm:
            kept.append(f)
            continue
        natives = native_norms_by_family.get(fam, [])
        is_dup = any(
            n == norm
            or (n and norm and (n in norm or norm in n) and abs(len(n) - len(norm)) <= 5)
            for n in natives
        )
        if not is_dup:
            kept.append(f)
    return kept


def _drive_file_local_score(
    file_meta: dict,
    query_text: str,
    source_channels: Set[str],
    time_filter: Optional[str] = None,
) -> float:
    """Cheap file-level score for any Drive family.

    Penalizes but does not hard-drop stale/sidecar candidates.

    ``time_filter`` (when provided by plan_only, e.g. ">2026-01-01" or
    "last_30_days") triggers the stale-year penalty deterministically and
    overrides the regex inference; falls back to regex when absent.
    """
    title = str(file_meta.get("name") or "")
    desc = str(file_meta.get("description") or "")
    modified = str(file_meta.get("modifiedTime") or "")
    hay = f"{title} {desc} {modified}".lower()
    q_tokens = [t for t in _tokenize(query_text) if len(t) > 1 or re.match(r"[一-鿿]", t)]
    unique_q = set(q_tokens)
    title_tokens = set(_tokenize(title))
    hay_tokens = set(_tokenize(hay))

    score = 0.0
    if unique_q:
        title_hits = len(unique_q & title_tokens)
        all_hits = len(unique_q & hay_tokens)
        score += min(0.55, 0.12 * title_hits)
        score += min(0.35, 0.05 * max(0, all_hits - title_hits))

    metric_terms = (
        "gmv", "revenue", "sales", "traffic", "projection", "budget", "roas", "cvr", "ctr",
        "库存", "销量", "销售", "收入", "预算", "转化", "流量", "进度", "项目", "状态",
    )
    if any(term in hay for term in metric_terms):
        score += 0.12
    if any(ch in source_channels for ch in ("fulltext", "fallback")):
        score += 0.08

    # Source channel penalty: sidecar is useful fallback, not fulltext-equivalent.
    if "sidecar" in source_channels and "fulltext" not in source_channels:
        sidecar_penalty = -0.05 if score >= 0.28 else -0.15
        score += sidecar_penalty
    if "broad_fallback" in source_channels and "fulltext" not in source_channels:
        score -= 0.10
    elif "fallback" in source_channels and "fulltext" not in source_channels:
        score -= 0.05

    has_time_intent = bool(time_filter) or _drive_has_time_or_fresh_intent(query_text)
    if has_time_intent:
        target_year: Optional[int] = None
        if time_filter:
            m = re.search(r"20\d{2}", time_filter)
            if m:
                target_year = int(m.group())
            elif re.search(r"last[_\-]?\d+[_\-]?day", time_filter, flags=re.I):
                target_year = date.today().year
        if target_year is None:
            query_years = [int(y) for y in re.findall(r"20\d{2}", query_text)]
            target_year = max(query_years) if query_years else date.today().year
        years = [int(y) for y in re.findall(r"20\d{2}", hay)]
        if years and max(years) < target_year:
            score -= min(0.35, 0.12 * (target_year - max(years)))

    return score


def _query_aware_truncate(
    uc_list: List[UnifiedChunk], query: str, top_n: int,
) -> List[UnifiedChunk]:
    """关键词命中 + reader_score 融合的快速截断。
    比纯 reader_score 截断更稳，开销 <50ms（无外部 rerank 调用）。
    """
    if len(uc_list) <= top_n:
        return uc_list
    q_tokens = set(_tokenize(query))
    if not q_tokens:
        return _fallback_select(uc_list, top_n)
    scored: List[tuple] = []
    for uc in uc_list:
        text = uc.text or ""
        c_tokens = _tokenize(text)
        if c_tokens:
            hit = sum(1 for t in c_tokens if t in q_tokens)
            kw_score = hit / (len(c_tokens) ** 0.5 + 1e-3)
        else:
            kw_score = 0.0
        reader_score = float(uc.score or 0.0)
        # 0.6 关键词命中 + 0.4 reader_score（reader_score 范围一般 0-1）
        fused = 0.6 * min(kw_score * 4, 1.0) + 0.4 * reader_score
        scored.append((fused, uc))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [uc for _, uc in scored[:top_n]]


def _quantiles(values: List[float]) -> tuple[float, float, float]:
    if not values:
        return 0.0, 0.0, 0.0
    ordered = sorted(values)
    n = len(ordered)
    p50 = ordered[min(n - 1, int((n - 1) * 0.50))]
    p95 = ordered[min(n - 1, int((n - 1) * 0.95))]
    return p50, p95, ordered[-1]


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


def _select_chunks_for_sub_query(
    query: str, focus_text: str, query_type: str,
    uc_list: List[UnifiedChunk], top_n: int,
) -> List[UnifiedChunk]:
    """Per-sub_query chunk 筛选（qwen rerank）。"""
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
        logger.warning("[google] chunk rerank failed: %s; fallback", result.get("message"))
        selected = _fallback_select(uc_list, top_n)
        logger.info("TIMING google.chunk_select mode=fallback query=%.24r input=%d selected=%d ms=%.1f",
                     query, len(uc_list), len(selected), _elapsed_ms(started))
        return selected
    selected_items = result.get("selected_results") or []
    if not selected_items:
        selected = _fallback_select(uc_list, top_n)
        logger.info("TIMING google.chunk_select mode=fallback_empty query=%.24r input=%d selected=%d ms=%.1f",
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
    logger.info("TIMING google.chunk_select mode=rerank query=%.24r input=%d selected=%d ms=%.1f",
                 query, len(uc_list), len(selected), _elapsed_ms(started))
    return selected


def _unified_to_chunk_entry(
    uc: UnifiedChunk, sub_query: str, target_focus: str,
) -> ChunkEntry:
    """UnifiedChunk → ChunkEntry。id 设计见 spec_plan §3。"""
    meta = uc.metadata or {}
    doc_id = (
        meta.get("url")
        or uc.source_url
        or meta.get("doc_id")
        or uc.source_name
        or "unknown"
    )
    node_id = (
        meta.get("anchor")
        or meta.get("chunk_id")
        or hashlib.md5((uc.text or "").encode("utf-8")).hexdigest()[:10]
    )
    cid = f"google:{doc_id}:{node_id}"

    merged_meta = {
        **meta,
        "source_name": uc.source_name,
        "source_url": uc.source_url,
        "heading_path": uc.heading_path,
        "source_tool": uc.source_tool or "google",
    }

    return ChunkEntry(
        id=cid,
        source="google",
        content=uc.text or "",
        metadata=merged_meta,
        score=uc.score,
        sub_query=sub_query,
        target_focuses={target_focus},
        tier=None,
    )


# ── SheetPool 集成 ──────────────────────────────────────────────────

class GoogleSourceAgent(SourceAgent):
    """Google SourceAgent（完整版，含 SheetPool 跨 sub_query 合并读取）。"""

    source = "google"

    def __init__(self, cfg: Optional[SourceAgentConfig] = None):
        cfg = cfg or SourceAgentConfig()
        profile = get_profile("google")
        super().__init__(
            profile=profile,
            cfg=cfg,
        )
        # Pool 跨 run() 调用持久化
        self._sheet_pool = None  # lazy init
        self._doc_pool = None
        self._slide_pool = None
        self._gmail_pool = None

    def _ensure_sheet_pool(self):
        if self._sheet_pool is None:
            from google_workspace_tool.sheet_pool import SheetPool
            self._sheet_pool = SheetPool()
        return self._sheet_pool

    def _ensure_doc_pool(self):
        if self._doc_pool is None:
            from google_workspace_tool.doc_pool import DocPool
            self._doc_pool = DocPool()
        return self._doc_pool

    def _ensure_slide_pool(self):
        if self._slide_pool is None:
            from google_workspace_tool.slide_pool import SlidePool
            self._slide_pool = SlidePool()
        return self._slide_pool

    def _ensure_gmail_pool(self):
        if self._gmail_pool is None:
            from google_workspace_tool.gmail_pool import GmailPool
            self._gmail_pool = GmailPool()
        return self._gmail_pool

    @staticmethod
    def _build_gmail_q(query: str, keyword_groups: Optional[List[List[str]]] = None) -> str:
        """构建 Gmail 搜索 q 字符串（关键词泛化）。

        复用 generate_grouped_keywords 的同义词组，组内 OR，组间隐式 AND。
        自动附加 -in:spam -in:trash 过滤。

        注意：
        - 对每组同义词限制最多 3 个，避免过宽 OR 导致召回噪声
        - 对 term 做 Gmail q 语法转义（双引号、反斜杠、尖括号等）
        """
        import re

        def _escape_gmail_term(t: str) -> str:
            # 先移除尖括号、管道符、控制字符和原始反斜杠
            t = re.sub(r'[<>|\x00-\x1f]', '', t)
            t = t.replace('\\', '')
            # 最后转义双引号
            t = t.replace('"', '\\"')
            return t.strip()

        if keyword_groups:
            group_clauses = []
            for group in keyword_groups:
                if not group:
                    continue
                # 限制每组最多 3 个同义词，减少噪声召回
                limited_group = group[:3]
                terms = [_escape_gmail_term(t) for t in limited_group]
                terms = [t for t in terms if t]
                if terms:
                    group_clauses.append("(" + " OR ".join(terms) + ")")
            if group_clauses:
                q = " ".join(group_clauses)
            else:
                q = _escape_gmail_term(query)
        else:
            q = _escape_gmail_term(query)
        return f"{q} -in:spam -in:trash"

    def _select_file_candidates(
        self,
        query: str,
        focus_text: str,
        files: List[dict],
        top_n: int = 4,
        score_threshold: float = 0.10,
        min_keep: int = 1,
        use_description: bool = True,
        filter_fn=None,
    ) -> List[str]:
        """对 file 列表做 qwen rerank，返回选中的 file_id 列表。

        Args:
            query: sub_query 文本
            focus_text: focus 文本
            files: Drive search 返回的 file dict 列表（含 name, description, owners 等）
            top_n: 最多返回几个 file
            use_description: 是否使用 file.description 作为 summary
            filter_fn: 可选的过滤函数，接收 file dict 返回 bool；
                       被过滤掉的候选会从 rerank 排名中按序补齐
        """
        if not files:
            return []
        if len(files) <= top_n:
            ids = [f.get("id", "") for f in files if f.get("id")]
            if filter_fn:
                ids = [fid for fid in ids if filter_fn(next(f for f in files if f.get("id") == fid))]
            return ids

        try:
            from tools.core.rerank import select_search_candidates, build_focus_sub_query_question
        except ImportError:
            ids = [f.get("id", "") for f in files[:top_n]]
            if filter_fn:
                ids = [fid for fid in ids if filter_fn(next(f for f in files if f.get("id") == fid))]
            return ids

        candidates = []
        file_by_id = {}
        for f in files:
            fid = f.get("id", "")
            if fid:
                file_by_id[fid] = f
            parts = [f"Title: {f.get('name', '')}"]
            fmt_label = _format_label(f.get("mimeType"))
            if fmt_label and fmt_label != "unknown":
                parts.append(f"Format: {fmt_label}")
            if use_description and f.get("description"):
                parts.append(f"Desc: {f.get('description', '')}")
            owners = ", ".join([o.get("displayName", "") for o in f.get("owners", []) if o.get("displayName")])
            if owners:
                parts.append(f"Owner: {owners}")
            if f.get("modifiedTime"):
                parts.append(f"Modified: {f.get('modifiedTime', '')}")
            candidates.append({
                "id": fid,
                "title": f.get("name", ""),
                "summary": " | ".join(parts),
                "_file_id": fid,
            })

        question = build_focus_sub_query_question(focus_text, query)
        rerank_result = select_search_candidates(
            query=question,
            candidates=candidates,
            top_n=len(candidates),  # 请求全部候选的完整排名
            score_threshold=score_threshold,
            min_keep=min_keep,
            instruct_template="qa",
        )

        if rerank_result.get("status") != "success":
            ids = [f.get("id", "") for f in files[:top_n]]
            if filter_fn:
                ids = [fid for fid in ids if filter_fn(file_by_id.get(fid, {}))]
            return ids

        # 获取完整 rerank 排名（按 relevance_score 降序）
        all_ranked = list(rerank_result.get("results") or [])
        # 映射 index → file_id
        index_to_fid = {i: c["_file_id"] for i, c in enumerate(candidates) if c.get("_file_id")}

        selected = []
        # 第一轮：应用 filter_fn 取前 top_n
        for item in all_ranked:
            idx = item.get("index")
            fid = index_to_fid.get(idx)
            if not fid or fid in selected:
                continue
            f = file_by_id.get(fid, {})
            if filter_fn and not filter_fn(f):
                continue
            selected.append(fid)
            if len(selected) >= top_n:
                break

        # 第二轮：如果 filter 后不足 min_keep，从被 filter 掉的候选中按序补齐
        if filter_fn and len(selected) < min_keep:
            for item in all_ranked:
                idx = item.get("index")
                fid = index_to_fid.get(idx)
                if not fid or fid in selected:
                    continue
                selected.append(fid)
                if len(selected) >= min_keep:
                    break

        return selected

    def _prefilter_sheet_files_before_metadata(
        self,
        search_results: List[dict],
        focus_text_map: Dict[str, str],
    ) -> Tuple[Set[str], Dict[str, dict]]:
        """Filter Google Sheet files before metadata prefetch.

        Returns the allowed spreadsheet ids and the raw candidate map used for
        logging/metadata backfill. Non-native Sheets are ignored here because
        Phase B only batchFetches native Google Sheets metadata.
        """
        started = time.perf_counter()
        raw_by_id: Dict[str, dict] = {}
        focus_by_id: Dict[str, Set[str]] = {}
        query_type_by_id: Dict[str, Set[str]] = {}
        query_parts_by_id: Dict[str, List[str]] = {}
        channels_by_id: Dict[str, Set[str]] = {}
        # 每份 sheet 命中过的 sub_query 集合，以及 per-(sq, fid) 的查询/focus 文本（用于 per-sq 打分）
        sq_ids_by_id: Dict[str, Set[str]] = {}
        sq_text_by_pair: Dict[Tuple[str, str], str] = {}
        all_sq_ids: Set[str] = set()
        sq_time_filter: Dict[str, Optional[str]] = {}

        for res in search_results:
            sq = res.get("sq")
            if not sq:
                continue
            qt = _normalize_google_query_type(getattr(sq, "query_type", None))
            focus_text = focus_text_map.get(sq.sub_query_id, sq.target_focus)
            all_sq_ids.add(sq.sub_query_id)
            sq_time_filter[sq.sub_query_id] = getattr(sq, "time_filter", None)
            for f in res.get("files", []):
                if f.get("mimeType") != _MIME_SHEETS:
                    continue
                fid = f.get("id")
                if not fid:
                    continue
                raw_by_id.setdefault(fid, f)
                focus_by_id.setdefault(fid, set()).add(focus_text or "")
                query_type_by_id.setdefault(fid, set()).add(qt)
                query_parts_by_id.setdefault(fid, []).append(str(sq.query or ""))
                sq_ids_by_id.setdefault(fid, set()).add(sq.sub_query_id)
                sq_text_by_pair.setdefault(
                    (sq.sub_query_id, fid),
                    f"{focus_text or ''} {sq.query or ''}".strip(),
                )
                channels = set(f.get("_google_sheet_channels") or [])
                if f.get("_google_sheet_channel"):
                    channels.add(f.get("_google_sheet_channel"))
                channels_by_id.setdefault(fid, set()).update(channels or {"fulltext"})

        raw_count = len(raw_by_id)
        if raw_count == 0:
            return set(), raw_by_id

        # Per-sq 文件 cap（按 sq.query_type 取值）；可在 trigger 之前先汇总每个 sq 的容量
        # 用于决定是否需要 prefilter。
        sq_query_type: Dict[str, str] = {}
        for sq_id in all_sq_ids:
            qt = "structured"
            for fid in sq_ids_by_id:
                if sq_id in sq_ids_by_id[fid]:
                    qts = query_type_by_id.get(fid, set())
                    # sq's own query_type is the same across fids, so just take one
                    if qts:
                        qt = next(iter(qts))
                    break
            sq_query_type[sq_id] = qt
        per_sq_caps = {
            sq_id: _sheet_files_cap_for_sq_query_type(self.cfg, qt)
            for sq_id, qt in sq_query_type.items()
        }
        # 任一 sq cap=0 → 该 sq 不贡献文件；若所有 sq cap=0 → 全空
        if all(c <= 0 for c in per_sq_caps.values()):
            logger.info(
                "TIMING google.sheet_prefilter raw=%d selected=0 reason=cap_zero ms=%.1f",
                raw_count, _elapsed_ms(started),
            )
            return set(), raw_by_id

        trigger = int(self.cfg.google_sheet_prefilter_trigger_count)
        # 当所有 sq 的命中文件数都 ≤ 自己的 cap，或总数 ≤ trigger，pass-through。
        per_sq_counts = {
            sq_id: sum(1 for fid in sq_ids_by_id if sq_id in sq_ids_by_id[fid])
            for sq_id in all_sq_ids
        }
        all_within_cap = all(
            per_sq_counts.get(sq_id, 0) <= per_sq_caps.get(sq_id, 0)
            for sq_id in all_sq_ids
        )
        if raw_count <= trigger or all_within_cap:
            logger.info(
                "TIMING google.sheet_prefilter raw=%d selected=%d reason=below_trigger_or_within_cap ms=%.1f",
                raw_count, raw_count, _elapsed_ms(started),
            )
            return set(raw_by_id), raw_by_id

        # Per-sq 打分 + per-sq 取 top N（按 sq 各自的 query_type cap）。
        # 同一文件可能命中多 sq，被任一 sq 选中即入选 union。
        per_sq_scores: Dict[str, List[Tuple[float, str]]] = {}
        score_max_by_fid: Dict[str, float] = {}
        for sq_id in all_sq_ids:
            sq_scores: List[Tuple[float, str]] = []
            for fid, f in raw_by_id.items():
                if sq_id not in sq_ids_by_id.get(fid, set()):
                    continue
                channels = channels_by_id.get(fid, set())
                text = sq_text_by_pair.get((sq_id, fid), "")
                if not text:
                    text = " ".join(focus_by_id.get(fid, set()) | set(query_parts_by_id.get(fid, [])))
                tf = sq_time_filter.get(sq_id)
                s = _drive_file_local_score(f, text, channels, time_filter=tf)
                sq_scores.append((s, fid))
                if s > score_max_by_fid.get(fid, -1e9):
                    score_max_by_fid[fid] = s
            sq_scores.sort(key=lambda x: -x[0])
            per_sq_scores[sq_id] = sq_scores

        selected_set: Set[str] = set()
        per_sq_selected: Dict[str, List[str]] = {}
        for sq_id in all_sq_ids:
            cap = per_sq_caps.get(sq_id, 0)
            picks = [fid for _, fid in per_sq_scores.get(sq_id, [])[:cap]]
            per_sq_selected[sq_id] = picks
            selected_set.update(picks)
        local_ms = _elapsed_ms(started)

        # Filter sheets out of search_results that we did not select
        for res in search_results:
            filtered = []
            for f in res.get("files", []):
                if f.get("mimeType") == _MIME_SHEETS and f.get("id") not in selected_set:
                    continue
                filtered.append(f)
            res["files"] = filtered

        logger.info(
            "TIMING google.sheet_prefilter raw=%d selected=%d n_sqs=%d caps=%s local_ms=%.1f reason=per_sq_local selected_ids=%s",
            raw_count, len(selected_set), len(all_sq_ids),
            {sq_id: per_sq_caps.get(sq_id, 0) for sq_id in sorted(all_sq_ids)},
            local_ms,
            [fid[-8:] for fid in sorted(selected_set)],
        )
        return selected_set, raw_by_id

    def _build_plan_only_agent(self) -> Optional[LlmAgent]:
        from .google_retrieve_llm_build import build_google_plan_only_agent
        return build_google_plan_only_agent()

    def _build_eval_only_agent(self) -> LlmAgent:
        from .google_retrieve_llm_build import build_google_eval_only_agent
        return build_google_eval_only_agent(self.cfg.source_top_k)

    def _build_plan_eval_agent(self) -> Optional[LlmAgent]:
        if self.cfg.max_loops <= 1:
            return None
        from .google_retrieve_llm_build import build_google_plan_eval_agent
        return build_google_plan_eval_agent(self.cfg.source_top_k)

    def _source_query_rules_block(self) -> str:
        """为大模型注入 Google 专属的检索规划指南。"""
        from tools.core.source_query_config import get_source_query_rules
        return get_source_query_rules("google")

    def _post_process_top_chunks(
        self,
        focuses: List[Focus],
        chunks: List[ChunkEntry],
    ) -> List[ChunkEntry]:
        """Prevent stale spreadsheet rows from being promoted to critical.

        Month-only business questions need broad recall, but rows like
        "2023-3" should not become critical unless the user explicitly asked
        for that year.
        """
        focus_text = " ".join(f.text or "" for f in focuses)
        if re.search(r"\b20\d{2}\b|20\d{2}年", focus_text):
            return chunks

        current_year = date.today().year
        force_current_year = any(t in focus_text.lower() for t in ("今年", "本年", "this year", "current year"))
        stale_cutoff = current_year - 3
        out: List[ChunkEntry] = []
        for c in chunks:
            years = [int(y) for y in re.findall(r"\b(20\d{2})(?:[-年/]|$)", c.content or "")]
            if years and (
                (c.tier == "critical" and min(years) <= stale_cutoff)
                or (force_current_year and max(years) < current_year)
            ):
                logger.info(
                    "[google] demote stale chunk id=%s tier=%s years=%s current_year=%s cutoff=%s",
                    c.id, c.tier, sorted(set(years)), current_year, stale_cutoff,
                )
                out.append(ChunkEntry(
                    id=c.id,
                    source=c.source,
                    content=c.content,
                    metadata=dict(c.metadata or {}),
                    score=min(float(c.score or 0.0), 0.1),
                    sub_query=c.sub_query,
                    target_focuses=set(c.target_focuses),
                    tier="noise",
                ))
            else:
                out.append(c)
        return out

    # ── 核心：覆写 _execute_batch，实现跨 sub_query 合并读取 ────────────

    async def _execute_batch(
        self, sub_queries: List[SubQuery], chunk_pool: Dict[str, ChunkEntry],
    ) -> None:
        """跨 sub_query 合并读取：先统一搜索 → batchGet 元数据 → qwen rerank 选 sheets → 合并读取。"""
        from tools.core.kw_generalization import generate_grouped_keywords
        from google_workspace_tool.drive_search_tool import search_drive
        from google_workspace_tool.drive_content_reader import read_drive_chunks
        from google_workspace_tool.sheet_pool import SheetPool, SheetSelection
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from tools.google.google_retrieve import _get_credentials, _build_search_q

        batch_started = time.perf_counter()
        sheet_pool = self._ensure_sheet_pool()

        # ── Phase A: 每个 sub_query 先做 Drive 搜索（只搜索，不读取）────────
        async def _search_one(sq: SubQuery) -> dict:
            started = time.perf_counter()
            try:
                creds = _get_credentials()
            except FileNotFoundError:
                logger.warning("[google] credentials not available")
                return {"sq": sq, "files": [], "creds": None}

            enable_fulltext = sq.google_enable_fulltext if sq.google_enable_fulltext is not None else self.cfg.google_enable_fulltext_search
            order_by = sq.google_order_by if sq.google_order_by is not None else self.cfg.google_order_by_mode
            keyword_groups = generate_grouped_keywords(sq.query)
            keyword_groups = _expand_bilingual(keyword_groups)  # 月份/季度中英同义词扩展
            order_by_param = order_by if not enable_fulltext else None

            def _log_candidates(channel: str, files: List[dict]) -> None:
                if not files:
                    return
                top = []
                for f in files[:5]:
                    name = str(f.get("name", "") or "")[:48]
                    mime = str(f.get("mimeType", "") or "").rsplit(".", 1)[-1]
                    fid = str(f.get("id", "") or "")
                    top.append(f"{name}<{mime}>:{fid[-8:]}")
                logger.info("[google] candidates sq=%s channel=%s count=%d top=%s",
                            sq.sub_query_id, channel, len(files), top)

            def _merge_file_candidates(primary: List[dict], extra: List[dict]) -> List[dict]:
                merged = list(primary)
                index_by_id = {f.get("id"): i for i, f in enumerate(merged) if f.get("id")}
                seen = set(index_by_id)
                for f in extra:
                    fid = f.get("id")
                    if fid and fid in seen:
                        idx = index_by_id.get(fid)
                        if idx is not None:
                            existing = dict(merged[idx])
                            channels = set(existing.get("_google_sheet_channels") or [])
                            channels.update(f.get("_google_sheet_channels") or [])
                            if f.get("_google_sheet_channel"):
                                channels.add(f.get("_google_sheet_channel"))
                            existing["_google_sheet_channels"] = sorted(channels)
                            if "fulltext" in channels:
                                existing["_google_sheet_channel"] = "fulltext"
                            merged[idx] = existing
                    elif fid:
                        seen.add(fid)
                        index_by_id[fid] = len(merged)
                        merged.append(f)
                return merged

            def _title_keyword_groups() -> Optional[List[List[str]]]:
                if not keyword_groups:
                    return keyword_groups
                brand_terms = {"nubia", "redmagic", "red magic", "hypershell", "zte"}
                first = {str(t).lower() for t in keyword_groups[0]}
                if len(keyword_groups) > 2 and first & brand_terms:
                    groups = list(keyword_groups[1:])
                else:
                    groups = list(keyword_groups)
                return groups

            # ── 通道 A1/A2/B + Sheet sidecar + Gmail：5 路并发 gather ─────────
            # 家族级 nonnative gate：sq.query_type ∈ 白名单 才召回该家族的非原生文件。
            # google_native_only（lite-style 历史开关）等价于把所有家族 nonnative 关闭。
            sheet_search_enabled = bool(getattr(self.cfg, "google_enable_sheet_search", True))
            qt_norm = _normalize_google_query_type(getattr(sq, "query_type", None))
            nonnative_gate: Dict[str, Set[str]] = getattr(self.cfg, "google_nonnative_search_query_types", {}) or {}
            native_only = bool(getattr(self.cfg, "google_native_only", False))
            sheet_nonnative_ok = (not native_only) and (qt_norm in (nonnative_gate.get("sheet") or set()))
            slide_nonnative_ok = (not native_only) and (qt_norm in (nonnative_gate.get("slide") or set()))
            doc_nonnative_ok = (not native_only) and (qt_norm in (nonnative_gate.get("doc") or set()))

            doc_mimes: Set[str] = {_MIME_DOCS}
            if doc_nonnative_ok:
                doc_mimes |= {_MIME_WORD, _MIME_WORD_OLD, _MIME_TXT}
            slide_mimes: Set[str] = {_MIME_SLIDES}
            if slide_nonnative_ok:
                slide_mimes |= {_MIME_PPTX}
            if sheet_search_enabled:
                sheet_mimes: Set[str] = {_MIME_SHEETS}
                if sheet_nonnative_ok:
                    sheet_mimes |= {_MIME_EXCEL, _MIME_EXCEL_OLD, _MIME_EXCEL_MACRO, _MIME_CSV}
            else:
                sheet_mimes = set()
            q_doc = _build_search_q(
                sq.query, keyword_groups=keyword_groups, enable_fulltext=enable_fulltext,
                time_filter=sq.time_filter, author_filter=sq.author_filter,
                mime_types=doc_mimes, max_groups=4,
            )
            q_slide = _build_search_q(
                sq.query, keyword_groups=keyword_groups, enable_fulltext=enable_fulltext,
                time_filter=sq.time_filter, author_filter=sq.author_filter,
                mime_types=slide_mimes, max_groups=4,
            )
            q_sheet = None
            if sheet_search_enabled and sheet_mimes:
                q_sheet = _build_search_q(
                    sq.query, keyword_groups=keyword_groups, enable_fulltext=enable_fulltext,
                    time_filter=sq.time_filter, author_filter=sq.author_filter,
                    mime_types=sheet_mimes,
                )
            q_sheet_sidecar = None
            if sheet_search_enabled and sheet_mimes and keyword_groups:
                q_sheet_sidecar = _build_search_q(
                    sq.query, keyword_groups=_title_keyword_groups(), enable_fulltext=False,
                    time_filter=sq.time_filter, author_filter=sq.author_filter,
                    mime_types=sheet_mimes, max_groups=3,
                )

            def _drive_call(q: str, page_size: int, order_by_p):
                return search_drive(
                    q=q, page_size=page_size, max_pages=1,
                    order_by=order_by_p, supports_all_drives=True,
                    include_items_from_all_drives=True, corpora='allDrives',
                    creds=creds,
                )

            from google_workspace_tool.gmail_search_tool import search_gmail
            def _gmail_call():
                if not self.cfg.google_enable_gmail:
                    return []
                gmail_q = self._build_gmail_q(sq.query, keyword_groups)
                # fetch_snippets=True：拉 snippet+subject，让后续 snippet rerank 真正起作用
                return search_gmail(
                    q=gmail_q,
                    max_results=self.cfg.google_max_gmail_threads_per_subquery,
                    max_pages=1,
                    creds=creds,
                    fetch_snippets=True,
                )

            # 5 路并发；任何单路异常不阻塞其他路（return_exceptions=True）
            phase_a_tasks = [
                asyncio.to_thread(_drive_call, q_doc, self.cfg.google_doc_search_per_sq, order_by_param),
                asyncio.to_thread(_drive_call, q_slide, self.cfg.google_slide_search_per_sq, order_by_param),
                asyncio.to_thread(_drive_call, q_sheet, self.cfg.google_sheet_search_per_sq, order_by_param) if q_sheet else asyncio.sleep(0, result=[]),
                asyncio.to_thread(_drive_call, q_sheet_sidecar, 3, order_by) if q_sheet_sidecar else asyncio.sleep(0, result=[]),
                asyncio.to_thread(_gmail_call),
            ]
            results = await asyncio.gather(*phase_a_tasks, return_exceptions=True)

            def _unwrap(idx, name):
                r = results[idx]
                if isinstance(r, Exception):
                    logger.warning("[google] Phase A channel %s failed for sq=%s: %s", name, sq.sub_query_id, r)
                    return []
                return r or []

            files_doc = _unwrap(0, "doc")
            files_slide = _unwrap(1, "slide")
            files_sheet = _unwrap(2, "sheet")
            title_sheets_pre = _unwrap(3, "sheet_sidecar")
            gmail_msg_results = _unwrap(4, "gmail")

            def _tag_sheet_candidates(files: List[dict], channel: str) -> List[dict]:
                tagged = []
                for f in files or []:
                    nf = dict(f)
                    channels = set(nf.get("_google_sheet_channels") or [])
                    channels.add(channel)
                    nf["_google_sheet_channels"] = sorted(channels)
                    nf["_google_sheet_channel"] = channel
                    tagged.append(nf)
                return tagged

            files_sheet = _tag_sheet_candidates(files_sheet, "fulltext")
            title_sheets_pre = _tag_sheet_candidates(title_sheets_pre, "sidecar")

            def _has_non_sidecar_sheet(files: List[dict]) -> bool:
                for f in files or []:
                    channels = set(f.get("_google_sheet_channels") or [])
                    if any(ch in channels for ch in ("fulltext", "fallback", "broad_fallback")):
                        return True
                return False

            # ── Sheet sidecar 合并（已在 gather 拿到 title_sheets_pre）
            if title_sheets_pre:
                files_sheet = _merge_file_candidates(files_sheet, title_sheets_pre)
                logger.info("[google] title sidecar sheet sq=%s results=%d merged=%d",
                            sq.sub_query_id, len(title_sheets_pre), len(files_sheet))

            # ── Fallback: 0 召回的通道走 fallback1（gather 并发）─────────────
            fb1_specs = []
            if len(files_doc) == 0 and keyword_groups:
                fb1_specs.append(("doc", _build_search_q(
                    sq.query, keyword_groups=keyword_groups, enable_fulltext=enable_fulltext,
                    time_filter=sq.time_filter, author_filter=sq.author_filter,
                    mime_types=doc_mimes, max_groups=2,
                ), self.cfg.google_doc_search_per_sq))
            if len(files_slide) == 0 and keyword_groups:
                fb1_specs.append(("slide", _build_search_q(
                    sq.query, keyword_groups=keyword_groups, enable_fulltext=enable_fulltext,
                    time_filter=sq.time_filter, author_filter=sq.author_filter,
                    mime_types=slide_mimes, max_groups=2,
                ), self.cfg.google_slide_search_per_sq))
            if sheet_search_enabled and not _has_non_sidecar_sheet(files_sheet) and keyword_groups:
                fb1_specs.append(("sheet", _build_search_q(
                    sq.query, keyword_groups=keyword_groups, enable_fulltext=enable_fulltext,
                    time_filter=sq.time_filter, author_filter=sq.author_filter,
                    mime_types=sheet_mimes, max_groups=2,
                ), self.cfg.google_sheet_search_per_sq))
            if fb1_specs:
                fb1_results = await asyncio.gather(
                    *[asyncio.to_thread(_drive_call, q, ps, order_by_param) for _, q, ps in fb1_specs],
                    return_exceptions=True,
                )
                for (name, _, _), r in zip(fb1_specs, fb1_results):
                    if isinstance(r, Exception):
                        logger.warning("[google] fallback %s search failed for sq=%s: %s", name, sq.sub_query_id, r)
                        continue
                    if name == "doc":
                        files_doc = r or []
                    elif name == "slide":
                        files_slide = r or []
                    elif name == "sheet":
                        files_sheet = _merge_file_candidates(files_sheet, _tag_sheet_candidates(r or [], "fallback"))
                    logger.info("[google] fallback %s sq=%s max_groups=2 results=%d",
                                name, sq.sub_query_id, len(r) if r else 0)

            # Fallback 2: 仍空 → raw_query（同样 gather 并发）
            fb2_specs = []
            if len(files_doc) == 0:
                fb2_specs.append(("doc", _build_search_q(
                    sq.query, keyword_groups=None, enable_fulltext=enable_fulltext,
                    time_filter=sq.time_filter, author_filter=sq.author_filter,
                    mime_types=doc_mimes,
                ), self.cfg.google_doc_search_per_sq))
            if len(files_slide) == 0:
                fb2_specs.append(("slide", _build_search_q(
                    sq.query, keyword_groups=None, enable_fulltext=enable_fulltext,
                    time_filter=sq.time_filter, author_filter=sq.author_filter,
                    mime_types=slide_mimes,
                ), self.cfg.google_slide_search_per_sq))
            if sheet_search_enabled and not _has_non_sidecar_sheet(files_sheet):
                fb2_specs.append(("sheet", _build_search_q(
                    sq.query, keyword_groups=None, enable_fulltext=enable_fulltext,
                    time_filter=sq.time_filter, author_filter=sq.author_filter,
                    mime_types=sheet_mimes,
                ), self.cfg.google_sheet_search_per_sq))
            if fb2_specs:
                fb2_results = await asyncio.gather(
                    *[asyncio.to_thread(_drive_call, q, ps, order_by_param) for _, q, ps in fb2_specs],
                    return_exceptions=True,
                )
                for (name, _, _), r in zip(fb2_specs, fb2_results):
                    if isinstance(r, Exception):
                        logger.warning("[google] fallback2 %s search failed for sq=%s: %s", name, sq.sub_query_id, r)
                        continue
                    if name == "doc":
                        files_doc = r or []
                    elif name == "slide":
                        files_slide = r or []
                    elif name == "sheet":
                        files_sheet = _merge_file_candidates(files_sheet, _tag_sheet_candidates(r or [], "fallback"))
                    logger.info("[google] fallback2 %s sq=%s raw_query results=%d",
                                name, sq.sub_query_id, len(r) if r else 0)

            # ── Broader fallback: 三路总和 < 3 时启动一次广义搜索 ──────────────
            # 用最少 1 组关键词 + 大 page_size，跨 mime 一次搜，按返回 mime 分发
            total_files = len(files_doc) + len(files_slide) + len(files_sheet)
            if total_files < 3 and keyword_groups:
                broad_mimes = doc_mimes | slide_mimes | sheet_mimes
                broad_q = _build_search_q(
                    sq.query, keyword_groups=keyword_groups, enable_fulltext=enable_fulltext,
                    time_filter=sq.time_filter, author_filter=sq.author_filter,
                    mime_types=broad_mimes, max_groups=1,
                )
                try:
                    broad_files = await asyncio.to_thread(
                        _drive_call, broad_q, 20, order_by_param,
                    ) or []
                    seen_ids = {f.get("id") for f in (files_doc + files_slide + files_sheet) if f.get("id")}
                    new_doc = [f for f in broad_files if f.get("mimeType") in doc_mimes and f.get("id") not in seen_ids]
                    new_slide = [f for f in broad_files if f.get("mimeType") in slide_mimes and f.get("id") not in seen_ids]
                    new_sheet = [f for f in broad_files if f.get("mimeType") in sheet_mimes and f.get("id") not in seen_ids]
                    new_sheet = _tag_sheet_candidates(new_sheet, "broad_fallback")
                    files_doc = files_doc + new_doc
                    files_slide = files_slide + new_slide
                    files_sheet = files_sheet + new_sheet
                    logger.info(
                        "[google] broad fallback sq=%s total<3 recovered=%d (doc+%d slide+%d sheet+%d)",
                        sq.sub_query_id, len(broad_files), len(new_doc), len(new_slide), len(new_sheet),
                    )
                except Exception as e:
                    logger.warning("[google] broad fallback failed for sq=%s: %s", sq.sub_query_id, e)

            # Slide title-biased fallback（slide 仍空才触发）
            if len(files_slide) == 0 and keyword_groups:
                try:
                    files_slide = await asyncio.to_thread(
                        _drive_call,
                        _build_search_q(
                            sq.query, keyword_groups=_title_keyword_groups(), enable_fulltext=False,
                            time_filter=sq.time_filter, author_filter=sq.author_filter,
                            mime_types=slide_mimes, max_groups=3,
                        ),
                        self.cfg.google_slide_search_per_sq, order_by,
                    ) or []
                    logger.info("[google] fallback title slide sq=%s results=%d", sq.sub_query_id, len(files_slide))
                except Exception as e:
                    logger.warning("[google] fallback title slide search failed for sq=%s: %s", sq.sub_query_id, e)
            if len(files_slide) == 0:
                try:
                    files_slide = await asyncio.to_thread(
                        _drive_call,
                        _build_search_q(
                            sq.query, keyword_groups=None, enable_fulltext=False,
                            time_filter=sq.time_filter, author_filter=sq.author_filter,
                            mime_types=slide_mimes,
                        ),
                        self.cfg.google_slide_search_per_sq, order_by,
                    ) or []
                    logger.info("[google] fallback2 title slide sq=%s results=%d", sq.sub_query_id, len(files_slide))
                except Exception as e:
                    logger.warning("[google] fallback2 title slide search failed for sq=%s: %s", sq.sub_query_id, e)

            # Native ↔ non-native dedup（每家族内：标题归一相同/包含 → 丢非原生）
            if bool(getattr(self.cfg, "google_dedup_native_vs_nonnative", True)):
                pre_doc, pre_slide, pre_sheet = len(files_doc), len(files_slide), len(files_sheet)
                files_doc = _dedup_native_vs_nonnative(files_doc)
                files_slide = _dedup_native_vs_nonnative(files_slide)
                files_sheet = _dedup_native_vs_nonnative(files_sheet)
                drop_doc = pre_doc - len(files_doc)
                drop_slide = pre_slide - len(files_slide)
                drop_sheet = pre_sheet - len(files_sheet)
                if drop_doc or drop_slide or drop_sheet:
                    logger.info(
                        "[google] dedup native_vs_nonnative sq=%s doc-=%d slide-=%d sheet-=%d",
                        sq.sub_query_id, drop_doc, drop_slide, drop_sheet,
                    )

            files = files_doc + files_slide + files_sheet
            _log_candidates("doc", files_doc)
            _log_candidates("slide", files_slide)
            _log_candidates("sheet", files_sheet)
            logger.info("[google] search sq=%s query=%r doc=%d slide=%d sheet=%d",
                        sq.sub_query_id, sq.query, len(files_doc), len(files_slide), len(files_sheet))

            # ── Gmail：从已 gather 拿到的 messages 提取 thread + snippet ──────
            gmail_threads = []
            if self.cfg.google_enable_gmail:
                seen_threads = set()
                for m in gmail_msg_results:
                    tid = m.get("threadId")
                    if tid and tid not in seen_threads:
                        seen_threads.add(tid)
                        gmail_threads.append({
                            "id": tid,
                            "messageId": m.get("id"),
                            "snippet": m.get("snippet", ""),
                            "subject": m.get("subject", ""),
                            "sender": m.get("from", ""),
                            "date": m.get("date", ""),
                        })

            logger.info("TIMING google.search_one sq=%s files=%d gmail_threads=%d ms=%.1f",
                        sq.sub_query_id, len(files), len(gmail_threads), _elapsed_ms(started))
            return {"sq": sq, "files": files, "creds": creds, "gmail_threads": gmail_threads}

        search_results = await asyncio.gather(*[_search_one(sq) for sq in sub_queries])

        # Phase A 汇总
        total_drive_files = sum(len(r.get("files", [])) for r in search_results)
        total_gmail_threads = sum(len(r.get("gmail_threads", [])) for r in search_results)
        logger.info("[google] Phase A summary: sub_queries=%d total_drive_files=%d total_gmail_threads=%d",
                    len(sub_queries), total_drive_files, total_gmail_threads)

        # ── 漏斗追踪初始化：记录每个 file 从搜索到读取的流转 ──────────────
        file_read_log: Dict[str, dict] = {}  # file_id → {type, sq_ids, status, chunks, error}

        def _log_file_read(fid: str, ftype: str, sq_ids: Set[str], status: str, chunks: int = 0, error: str = ""):
            file_read_log[fid] = {
                "type": ftype, "sq_ids": sq_ids, "status": status,
                "chunks": chunks, "error": error,
            }

        def _file_size_of(fid: str, ftype: str) -> Optional[int]:
            if ftype == "sheet":
                meta = sheet_pool.get_meta(fid)
                return meta.drive_size if meta else None
            if ftype == "doc":
                meta = doc_pool.get_meta(fid) if 'doc_pool' in locals() else None
                return meta.drive_size if meta else None
            if ftype == "slide":
                meta = slide_pool.get_meta(fid) if 'slide_pool' in locals() else None
                return meta.drive_size if meta else None
            return None

        def _short_id(fid: str) -> str:
            return fid[-8:] if fid else ""

        focus_text_map = {}
        for sq in sub_queries:
            focus_obj = self._current_focus_lookup.get(sq.target_focus)
            focus_text_map[sq.sub_query_id] = focus_obj.text if focus_obj else sq.target_focus

        allowed_sheet_ids, _ = self._prefilter_sheet_files_before_metadata(
            search_results,
            focus_text_map,
        )

        # ── Phase B: 收集所有 spreadsheet file_ids，batchGet 元数据 ────────
        # SheetPool 元数据获取是同步阻塞调用，但只影响 Sheet 的后续 rerank/读取，
        # 与 Doc/Slide/Gmail 的后续处理互不等待。
        all_spreadsheet_ids: Set[str] = set()
        spreadsheet_size_map: Dict[str, Optional[int]] = {}
        spreadsheet_modified_map: Dict[str, Optional[str]] = {}
        for res in search_results:
            for f in res.get("files", []):
                if f.get("mimeType") == _MIME_SHEETS:
                    fid = f.get("id")
                    if not fid:
                        continue
                    if allowed_sheet_ids and fid not in allowed_sheet_ids:
                        continue
                    all_spreadsheet_ids.add(fid)
                    spreadsheet_size_map[fid] = _drive_file_size(f)
                    spreadsheet_modified_map[fid] = f.get("modifiedTime")

        # 取 creds（所有 search_result 的 creds 都相同）
        creds = None
        for res in search_results:
            if res.get("creds"):
                creds = res["creds"]
                break

        # SheetPool batchGet 元数据（同步，但 Doc/Slide/Gmail 不依赖它）
        if all_spreadsheet_ids and creds:
            metadata_timeout = max(
                5.0,
                min(float(self.cfg.source_timeout_s) * 0.25, float(self.cfg.google_api_timeout_s) + 5.0),
            )
            phase_b_started = time.perf_counter()
            logger.info(
                "[google] Phase B metadata_prefetch start spreadsheets=%d workers=%d batch_timeout=%.1fs api_timeout=%ss",
                len(all_spreadsheet_ids), 8, metadata_timeout, int(self.cfg.google_api_timeout_s),
            )
            try:
                sheet_pool.batch_fetch_metadata(
                    list(all_spreadsheet_ids),
                    creds,
                    native_api_timeout=int(self.cfg.google_api_timeout_s),
                    max_workers=8,
                    batch_timeout_s=metadata_timeout,
                )
            except Exception as e:
                logger.warning("[google] sheet metadata prefetch skipped after error: %s", e)
            finally:
                cached_after = sum(1 for fid in all_spreadsheet_ids if sheet_pool.get_meta(fid))
                logger.info(
                    "TIMING google.phase_b.metadata_prefetch spreadsheets=%d cached=%d missing=%d ms=%.1f",
                    len(all_spreadsheet_ids),
                    cached_after,
                    len(all_spreadsheet_ids) - cached_after,
                    _elapsed_ms(phase_b_started),
                )
            # 把 search result 中的 drive_size 回填到 SheetMeta
            for fid, size in spreadsheet_size_map.items():
                meta = sheet_pool.get_meta(fid)
                if meta and size is not None:
                    meta.drive_size = size
                if meta and spreadsheet_modified_map.get(fid):
                    meta.modified_time = spreadsheet_modified_map.get(fid)

        # ── Phase B2: 初始化 DocPool / SlidePool / GmailPool ──────────────
        # 三类 Pool 的初始化只做内存操作（从 search_results 提取元数据），
        # 与 SheetPool 的 batchGet 互不等待。
        doc_pool = self._ensure_doc_pool()
        slide_pool = self._ensure_slide_pool()
        gmail_pool = self._ensure_gmail_pool()

        # 从 search results 提取 Doc/Slide 元数据存入 pool
        for res in search_results:
            for f in res.get("files", []):
                mime = f.get("mimeType")
                fid = f.get("id")
                if not fid:
                    continue
                # doc_pool 实质上是「binary 下载路径」的元数据池：
                # - 原生 Google Doc 走 documents().get（在 read_drive_chunks 内部）
                # - DOCX/TXT/XLSX/XLS/XLSM/CSV 走 Drive files().get_media 下载
                #   并在 reader 层（excel_reader/text_reader/word_reader）解析
                # 三者读取流均由 read_drive_chunks 按 mime 路由，所以共享同一池即可。
                if mime == _MIME_DOCS or mime in (
                    _MIME_WORD, _MIME_WORD_OLD, _MIME_TXT,
                    _MIME_EXCEL, _MIME_EXCEL_OLD, _MIME_EXCEL_MACRO, _MIME_CSV,
                ):
                    from google_workspace_tool.doc_pool import DocMeta
                    doc_pool.add_meta(DocMeta(
                        file_id=fid,
                        title=f.get("name", ""),
                        url=f"https://docs.google.com/document/d/{fid}/edit" if mime == _MIME_DOCS else f"https://drive.google.com/file/d/{fid}/view",
                        mime_type=mime,
                        description=f.get("description", ""),
                        drive_size=_drive_file_size(f),
                        modified_time=f.get("modifiedTime"),
                        owner=", ".join([o.get("displayName", "") for o in f.get("owners", []) if o.get("displayName")]),
                    ))
                elif mime == _MIME_SLIDES or mime == _MIME_PPTX:
                    from google_workspace_tool.slide_pool import SlideMeta
                    slide_pool.add_meta(SlideMeta(
                        file_id=fid,
                        title=f.get("name", ""),
                        url=f"https://docs.google.com/presentation/d/{fid}/edit" if mime == _MIME_SLIDES else f"https://drive.google.com/file/d/{fid}/view",
                        mime_type=mime,
                        description=f.get("description", ""),
                        drive_size=_drive_file_size(f),
                        modified_time=f.get("modifiedTime"),
                        owner=", ".join([o.get("displayName", "") for o in f.get("owners", []) if o.get("displayName")]),
                    ))

        # 从 search results 提取 Gmail thread 元数据存入 pool
        if self.cfg.google_enable_gmail:
            for res in search_results:
                for t in res.get("gmail_threads", []):
                    tid = t.get("id")
                    if not tid:
                        continue
                    # search_gmail(fetch_snippets=True) 已在 Phase A 拉到 snippet/subject/sender/date
                    from google_workspace_tool.gmail_pool import GmailThreadMeta
                    gmail_pool.add_meta(GmailThreadMeta(
                        thread_id=tid,
                        subject=t.get("subject", ""),
                        sender=t.get("sender", ""),
                        date=t.get("date", ""),
                        snippet=t.get("snippet", ""),
                        url=f"https://mail.google.com/mail/u/0/#inbox/{tid}",
                    ))

        # ── Phase C: Per-sub_query 筛选（qwen rerank）───────────────────────

        # C1. Sheet 筛选（收紧：精准搜索后只保留 top 5 sheets）
        sq_sheet_selections: Dict[str, List[SheetSelection]] = {}
        for res in search_results:
            sq = res["sq"]
            sq_sheet_selections[sq.sub_query_id] = []
            focus_text = focus_text_map[sq.sub_query_id]
            for f in res.get("files", []):
                if f.get("mimeType") != _MIME_SHEETS:
                    continue
                fid = f.get("id")
                if not fid:
                    continue
                selected_titles = sheet_pool.select_sheets_for_sub_query(
                    file_id=fid, query=sq.query, focus_text=focus_text,
                    top_n=self.cfg.google_sheet_candidates_per_sq,
                    score_threshold=0.12, min_keep=1, skip_rerank_if_few=True,
                )
                if selected_titles:
                    sq_sheet_selections[sq.sub_query_id].append(
                        SheetSelection(file_id=fid, selected_titles=selected_titles,
                                       sub_query_id=sq.sub_query_id, focus_id=sq.target_focus))

        # C2. Doc 筛选（统一处理原生 Doc + word/docx/txt + excel/csv）
        # 注：Excel/CSV 在 Phase A 由 sheet_mimes 搜索召回（搜索语义按表格），
        # 但读取走 binary download 路径与 Word/Doc 同池，故并入 doc 候选选择层。
        # native Sheet（_MIME_SHEETS）走 C1 单独的 tab 选择路径。
        _DOC_SIZE_THRESHOLD = int(self.cfg.google_native_doc_size_gate_bytes)
        _WORD_SIZE_THRESHOLD = int(self.cfg.google_word_size_gate_bytes)
        # Excel/CSV 大小上限沿用 word_size_gate；CSV 通常 < 几 MB，实际走 download_timeout 兜底
        def _doc_filter(f: dict) -> bool:
            mime = f.get("mimeType")
            size = _drive_file_size(f)
            if mime == _MIME_DOCS and size and size > _DOC_SIZE_THRESHOLD:
                logger.warning(
                    "[google] skip oversized native doc id=%s size=%s name=%.40r",
                    f.get("id"), size, f.get("name"),
                )
                return False
            if mime in (_MIME_WORD, _MIME_WORD_OLD) and size and size > _WORD_SIZE_THRESHOLD:
                logger.warning(
                    "[google] skip oversized word/docx id=%s size=%s name=%.40r",
                    f.get("id"), size, f.get("name"),
                )
                return False
            if mime in (_MIME_EXCEL, _MIME_EXCEL_OLD, _MIME_EXCEL_MACRO) and size and size > _WORD_SIZE_THRESHOLD:
                logger.warning(
                    "[google] skip oversized excel id=%s size=%s name=%.40r",
                    f.get("id"), size, f.get("name"),
                )
                return False
            return True

        _DOC_FAMILY_MIMES = (
            _MIME_DOCS, _MIME_WORD, _MIME_WORD_OLD, _MIME_TXT,
            _MIME_EXCEL, _MIME_EXCEL_OLD, _MIME_EXCEL_MACRO, _MIME_CSV,
        )
        sq_doc_selections: Dict[str, List] = {}  # sq_id → [DocSelection]
        for res in search_results:
            sq = res["sq"]
            sq_doc_selections[sq.sub_query_id] = []
            focus_text = focus_text_map[sq.sub_query_id]
            doc_files = [f for f in res.get("files", []) if f.get("mimeType") in _DOC_FAMILY_MIMES]
            # 自适应 cap：doc 候选稀疏时不砍，丰盛时按 preset cap 砍
            adaptive_doc_top_n = _adaptive_cap(
                len(doc_files),
                abundant_cap=int(self.cfg.google_doc_candidates_per_sq),
                moderate_cap=int(self.cfg.google_doc_candidates_per_sq) + 1,
            )
            selected_docs = self._select_file_candidates(
                query=sq.query, focus_text=focus_text,
                files=doc_files, top_n=adaptive_doc_top_n,
                use_description=True,
                filter_fn=_doc_filter,
            )
            if doc_files and len(selected_docs) == 0:
                # 质量保护：放宽 rerank score 阈值，但保留 size gate（避免救回会读爆超时的大文件）
                selected_docs = self._select_file_candidates(
                    query=sq.query, focus_text=focus_text,
                    files=doc_files, top_n=min(2, self.cfg.google_doc_candidates_per_sq),
                    use_description=True,
                    filter_fn=_doc_filter,
                )
                if selected_docs:
                    logger.info("[google] doc filter fallback sq=%s restored=%d", sq.sub_query_id, len(selected_docs))
            skipped = [f.get("id", "") for f in doc_files]
            kept = set(selected_docs)
            skipped_count = sum(1 for fid in skipped if fid not in kept)
            if skipped_count:
                logger.info("[google] doc filter skipped %d oversized native docs for sq=%s, kept %d",
                            skipped_count, sq.sub_query_id, len(selected_docs))
            for fid in selected_docs:
                from google_workspace_tool.doc_pool import DocSelection
                sq_doc_selections[sq.sub_query_id].append(
                    DocSelection(file_id=fid, sub_query_id=sq.sub_query_id, focus_id=sq.target_focus))

        # C3. Slide 筛选（支持原生 slide gate + fallback）
        _PPTX_SIZE_THRESHOLD = int(self.cfg.google_pptx_size_gate_bytes)
        _NATIVE_SLIDE_SIZE_THRESHOLD = int(self.cfg.google_native_slide_size_gate_bytes)
        def _slide_filter(f: dict) -> bool:
            if f.get("mimeType") == _MIME_PPTX:
                size = _drive_file_size(f)
                if size and size > _PPTX_SIZE_THRESHOLD:
                    return False
            if self.cfg.google_enable_native_slide_size_gate and f.get("mimeType") == _MIME_SLIDES:
                size = _drive_file_size(f)
                if size and size > _NATIVE_SLIDE_SIZE_THRESHOLD:
                    return False
            return True

        sq_slide_selections: Dict[str, List] = {}
        for res in search_results:
            sq = res["sq"]
            sq_slide_selections[sq.sub_query_id] = []
            focus_text = focus_text_map[sq.sub_query_id]
            slide_files = [f for f in res.get("files", []) if f.get("mimeType") in (_MIME_SLIDES, _MIME_PPTX)]
            # 自适应 cap：slide 候选稀疏时不砍
            adaptive_slide_top_n = _adaptive_cap(
                len(slide_files),
                abundant_cap=int(self.cfg.google_slide_candidates_per_sq),
                moderate_cap=int(self.cfg.google_slide_candidates_per_sq) + 1,
            )
            selected_slides = self._select_file_candidates(
                query=sq.query, focus_text=focus_text,
                files=slide_files, top_n=adaptive_slide_top_n,
                use_description=False,
                filter_fn=_slide_filter,
            )
            if slide_files and len(selected_slides) == 0:
                # 质量保护：放宽 rerank score 阈值，但保留 size gate（避免救回会读爆超时的大文件）
                selected_slides = self._select_file_candidates(
                    query=sq.query, focus_text=focus_text,
                    files=slide_files, top_n=min(2, self.cfg.google_slide_candidates_per_sq),
                    use_description=False,
                    filter_fn=_slide_filter,
                )
                if selected_slides:
                    logger.info("[google] slide filter fallback sq=%s restored=%d", sq.sub_query_id, len(selected_slides))
            skipped = [f.get("id", "") for f in slide_files]
            kept = set(selected_slides)
            skipped_count = sum(1 for fid in skipped if fid not in kept)
            if skipped_count:
                logger.info("[google] slide filter skipped %d oversized pptx for sq=%s, kept %d",
                            skipped_count, sq.sub_query_id, len(selected_slides))
            for fid in selected_slides:
                from google_workspace_tool.slide_pool import SlideSelection
                sq_slide_selections[sq.sub_query_id].append(
                    SlideSelection(file_id=fid, sub_query_id=sq.sub_query_id, focus_id=sq.target_focus))

        # C4. Gmail Thread 筛选（snippet rerank：10 → 3）
        sq_gmail_selections: Dict[str, List] = {}
        if self.cfg.google_enable_gmail:
            for res in search_results:
                sq = res["sq"]
                sq_gmail_selections[sq.sub_query_id] = []
                focus_text = focus_text_map[sq.sub_query_id]
                threads = res.get("gmail_threads", [])
                if not threads:
                    continue
                # 自适应 cap：gmail 稀疏时不砍（保留所有 thread）
                top_n = _adaptive_cap(
                    len(threads),
                    abundant_cap=int(self.cfg.google_gmail_rerank_top_n),
                    moderate_cap=int(self.cfg.google_gmail_rerank_top_n) + 1,
                )
                if len(threads) <= top_n:
                    for t in threads:
                        from google_workspace_tool.gmail_pool import GmailSelection
                        sq_gmail_selections[sq.sub_query_id].append(
                            GmailSelection(thread_id=t["id"], sub_query_id=sq.sub_query_id, focus_id=sq.target_focus))
                    continue
                # qwen rerank 选 thread
                try:
                    from tools.core.rerank import select_search_candidates, build_focus_sub_query_question
                    candidates = []
                    for t in threads:
                        meta = gmail_pool.get_meta(t["id"])
                        subject = meta.subject if meta else ""
                        snippet = meta.snippet if meta else ""
                        candidates.append({
                            "id": t["id"],
                            "title": subject or f"Thread {t['id']}",
                            "summary": snippet[:300],
                            "_thread_id": t["id"],
                        })
                    question = build_focus_sub_query_question(focus_text, sq.query)
                    rerank_result = select_search_candidates(
                        query=question, candidates=candidates,
                        top_n=top_n, score_threshold=0.18, min_keep=1,
                        instruct_template="qa",
                    )
                    if rerank_result.get("status") == "success":
                        selected = list(rerank_result.get("selected_candidates") or [])
                    else:
                        selected = candidates[:top_n]
                    for c in selected:
                        tid = c.get("_thread_id")
                        if tid:
                            from google_workspace_tool.gmail_pool import GmailSelection
                            sq_gmail_selections[sq.sub_query_id].append(
                                GmailSelection(thread_id=tid, sub_query_id=sq.sub_query_id, focus_id=sq.target_focus))
                except Exception as e:
                    logger.warning("[google] Gmail thread rerank failed for sq=%s: %s", sq.sub_query_id, e)
                    # fallback：取前 top_n
                    for t in threads[:top_n]:
                        from google_workspace_tool.gmail_pool import GmailSelection
                        sq_gmail_selections[sq.sub_query_id].append(
                            GmailSelection(thread_id=t["id"], sub_query_id=sq.sub_query_id, focus_id=sq.target_focus))

        # ── Phase D: 合并跨 sq 的读取需求 ──────────────────────────────────
        all_sheet_selections: List[SheetSelection] = []
        for selections in sq_sheet_selections.values():
            all_sheet_selections.extend(selections)
        merged_sheets = SheetPool.merge_selections(all_sheet_selections)

        all_doc_selections = []
        for selections in sq_doc_selections.values():
            all_doc_selections.extend(selections)
        merged_docs = doc_pool.merge_selections(all_doc_selections)  # file_id → set(sq_ids)

        all_slide_selections = []
        for selections in sq_slide_selections.values():
            all_slide_selections.extend(selections)
        merged_slides = slide_pool.merge_selections(all_slide_selections)  # file_id → set(sq_ids)

        all_gmail_selections = []
        for selections in sq_gmail_selections.values():
            all_gmail_selections.extend(selections)
        merged_gmail = gmail_pool.merge_selections(all_gmail_selections)  # thread_id → set(sq_ids)

        logger.info("[google] Phase D summary: merged_sheets=%d merged_docs=%d merged_slides=%d merged_gmail=%d",
                    len(merged_sheets), len(merged_docs), len(merged_slides), len(merged_gmail))
        timeout_s = self.cfg.source_timeout_s
        logger.info(
            "[google] Phase E queue summary sheets=%d docs=%d slides=%d gmail=%d executor_workers=%d per_file_timeout=%.1fs",
            len(merged_sheets), len(merged_docs), len(merged_slides), len(merged_gmail), 12, min(20.0, timeout_s),
        )

        # ── Phase E: 并发读取（四类独立，互不等待）───────────────────────────
        chunks_by_sq: Dict[str, List[UnifiedChunk]] = {sq.sub_query_id: [] for sq in sub_queries}

        # 取 creds（提前，供闭包引用）
        creds = None
        for res in search_results:
            if res.get("creds"):
                creds = res["creds"]
                break

        # 构建反查映射：file_id → set(sq_ids)
        sheet_sq_map: Dict[str, Set[str]] = {}
        for sel in all_sheet_selections:
            sheet_sq_map.setdefault(sel.file_id, set()).add(sel.sub_query_id)

        def _resolve_query_for_sq_ids(sq_ids: Set[str]) -> str:
            """从 sq_ids 中找一个 query 用于 BM25 行筛选。"""
            for sq_id in sq_ids:
                for sq in sub_queries:
                    if sq.sub_query_id == sq_id:
                        return sq.query
            return ""

        def _read_sheet_task(fid: str, titles: List[str]) -> None:
            if not sheet_pool.mark_reading(fid):
                return
            started = time.perf_counter()
            # Sheets API A1 notation: title 含空格或单引号时需要用单引号包裹
            # title 内的单引号必须转义为两个单引号
            range_names = []
            for t in titles:
                if " " in t or "'" in t:
                    escaped_t = t.replace("'", "''")
                    range_names.append(f"'{escaped_t}'")
                else:
                    range_names.append(t)
            query = _resolve_query_for_sq_ids(sheet_sq_map.get(fid, set()))
            try:
                from tools.google.google_retrieve import _google_chunk_to_unified
                _ck = _cache_key(fid, _MIME_SHEETS, sheet_titles=tuple(range_names))
                _gc_cached = _cache_get(_ck, self.cfg.google_file_content_cache_ttl_s) if self.cfg.google_enable_file_content_cache else None
                if _gc_cached is not None:
                    gc_list = _gc_cached
                    logger.info("[google] sheet cache HIT %s", _short_id(fid))
                else:
                    sheet_meta = sheet_pool.get_meta(fid)
                    _known_meta = None
                    _known_sheet_count = None
                    if sheet_meta:
                        _known_meta = {
                            "name": sheet_meta.title,
                            "size": sheet_meta.drive_size,
                            "modifiedTime": sheet_meta.modified_time,
                            "mimeType": _MIME_SHEETS,
                        }
                        _known_sheet_count = len(sheet_meta.sheets) if sheet_meta.sheets else None
                    gc_list = read_drive_chunks(
                        file_id=fid, mime_type=_MIME_SHEETS, creds=creds,
                        max_chunk_tokens=800, native_api_timeout=int(self.cfg.google_api_timeout_s),
                        supports_all_drives=True, range_names=range_names, query=query,
                        known_title=sheet_meta.title if sheet_meta else None,
                        known_metadata=_known_meta,
                        known_sheet_count=_known_sheet_count,
                    )
                    if self.cfg.google_enable_file_content_cache and gc_list:
                        _cache_put(_ck, gc_list, int(self.cfg.google_file_content_cache_max_files))
                uc_list = [_google_chunk_to_unified(gc, query) for gc in gc_list]
                sheet_meta = sheet_pool.get_meta(fid)
                if sheet_meta:
                    for uc in uc_list:
                        uc.metadata.setdefault("modified_time", sheet_meta.modified_time)
                        uc.metadata.setdefault("drive_size", sheet_meta.drive_size)
                # Sheet BM25 收紧：>50 chunks 先截断到 50，避免 Phase G 输入爆炸
                if len(uc_list) > int(self.cfg.google_sheet_chunk_cap):
                    orig_len = len(uc_list)
                    cap = int(self.cfg.google_sheet_chunk_cap)
                    uc_list = _fallback_select(uc_list, cap)
                    logger.info("[google] sheet truncate %s: %d -> %d", fid, orig_len, cap)
                size = sheet_meta.drive_size if sheet_meta else None
                logger.info(
                    "TIMING google.file_read type=sheet file=%s size=%s selected_titles=%d gc=%d uc=%d ms=%.1f",
                    _short_id(fid), size if size is not None else "N/A", len(titles), len(gc_list), len(uc_list),
                    _elapsed_ms(started),
                )
                logger.info("[google] sheet read done %s: titles=%d range_names=%s gc=%d uc=%d size=%s",
                            fid, len(titles), range_names, len(gc_list), len(uc_list), size if size is not None else "N/A")
                status = "ok" if len(uc_list) > 0 else "zero_chunks"
                _log_file_read(fid, "sheet", sheet_sq_map.get(fid, set()), status, len(uc_list))
            except Exception as e:
                logger.warning("[google] sheet read failed %s: %s", fid, e)
                _log_file_read(fid, "sheet", sheet_sq_map.get(fid, set()), "failed", 0, str(e))
                uc_list = []
            sheet_pool.mark_done(fid, uc_list)

        def _read_doc_task(fid: str) -> None:
            if not doc_pool.mark_reading(fid):
                return
            started = time.perf_counter()
            meta = doc_pool.get_meta(fid)
            mime = meta.mime_type if meta else _MIME_DOCS
            sq_ids = merged_docs.get(fid, set())
            query = _resolve_query_for_sq_ids(sq_ids)
            try:
                from tools.google.google_retrieve import _google_chunk_to_unified
                # word/docx 用更短 timeout（默认 6s vs native 8s/10s）
                _dl_timeout = int(self.cfg.google_word_download_timeout_s) if mime in (_MIME_WORD, _MIME_WORD_OLD) else int(self.cfg.google_download_timeout_s)
                # 跨 query 缓存命中？
                _ck = _cache_key(fid, mime)
                _gc_cached = _cache_get(_ck, self.cfg.google_file_content_cache_ttl_s) if self.cfg.google_enable_file_content_cache else None
                if _gc_cached is not None:
                    gc_list = _gc_cached
                    logger.info("[google] doc cache HIT %s", _short_id(fid))
                else:
                    _known_meta = None
                    if meta:
                        _known_meta = {
                            "name": meta.title,
                            "size": meta.drive_size,
                            "modifiedTime": meta.modified_time,
                            "mimeType": mime,
                        }
                    # Excel/CSV 走与 Word/Doc 同一池路径，但需要把 enable_excel_filter
                    # 透传给 excel_reader / text_reader 用于行级 Qwen 过滤。
                    is_excel_or_csv = mime in (_MIME_EXCEL, _MIME_EXCEL_OLD, _MIME_EXCEL_MACRO, _MIME_CSV)
                    gc_list = read_drive_chunks(
                        file_id=fid, mime_type=mime, creds=creds,
                        max_chunk_tokens=800,
                        auto_outline_threshold=int(self.cfg.google_native_doc_outline_threshold_bytes) if mime == _MIME_DOCS else None,
                        download_timeout=_dl_timeout,
                        native_api_timeout=int(self.cfg.google_api_timeout_s),
                        supports_all_drives=True, query=query,
                        known_title=meta.title if meta else None,
                        known_metadata=_known_meta,
                        enable_excel_filter=bool(self.cfg.google_enable_excel_filter) if is_excel_or_csv else False,
                    )
                    if self.cfg.google_enable_file_content_cache and gc_list:
                        _cache_put(_ck, gc_list, int(self.cfg.google_file_content_cache_max_files))
                uc_list = [_google_chunk_to_unified(gc, query) for gc in gc_list]
                if len(uc_list) > int(self.cfg.google_doc_chunk_cap):
                    orig_len = len(uc_list)
                    cap = int(self.cfg.google_doc_chunk_cap)
                    uc_list = _fallback_select(uc_list, cap)
                    logger.info("[google] doc truncate %s: %d -> %d", fid, orig_len, cap)
                if meta:
                    for uc in uc_list:
                        uc.metadata.setdefault("modified_time", meta.modified_time)
                        uc.metadata.setdefault("drive_size", meta.drive_size)
                        uc.metadata.setdefault("owner", meta.owner)
                size = meta.drive_size if meta else None
                logger.info(
                    "TIMING google.file_read type=doc file=%s mime=%s size=%s gc=%d uc=%d ms=%.1f",
                    _short_id(fid), mime.rsplit(".", 1)[-1], size if size is not None else "N/A",
                    len(gc_list), len(uc_list), _elapsed_ms(started),
                )
                logger.info("[google] doc read done %s: mime=%s gc=%d uc=%d size=%s",
                            fid, mime, len(gc_list), len(uc_list), size if size is not None else "N/A")
                status = "ok" if len(uc_list) > 0 else "zero_chunks"
                _log_file_read(fid, "doc", sq_ids, status, len(uc_list))
            except Exception as e:
                logger.warning("[google] doc read failed %s: %s", fid, e)
                _log_file_read(fid, "doc", sq_ids, "failed", 0, str(e))
                uc_list = []
            doc_pool.mark_done(fid, uc_list)

        def _read_slide_task(fid: str) -> None:
            if not slide_pool.mark_reading(fid):
                return
            started = time.perf_counter()
            meta = slide_pool.get_meta(fid)
            mime = meta.mime_type if meta else _MIME_SLIDES
            query = _resolve_query_for_sq_ids(merged_slides.get(fid, set()))
            try:
                from tools.google.google_retrieve import _google_chunk_to_unified
                # pptx 用更短 timeout（默认 6s）；native slide 走 Slides API，用 api_timeout
                _dl_timeout = int(self.cfg.google_pptx_download_timeout_s) if mime == _MIME_PPTX else int(self.cfg.google_download_timeout_s)
                _ck = _cache_key(fid, mime)
                _gc_cached = _cache_get(_ck, self.cfg.google_file_content_cache_ttl_s) if self.cfg.google_enable_file_content_cache else None
                if _gc_cached is not None:
                    gc_list = _gc_cached
                    logger.info("[google] slide cache HIT %s", _short_id(fid))
                else:
                    _known_meta = None
                    if meta:
                        _known_meta = {
                            "name": meta.title,
                            "size": meta.drive_size,
                            "modifiedTime": meta.modified_time,
                            "mimeType": mime,
                        }
                    gc_list = read_drive_chunks(
                        file_id=fid, mime_type=mime, creds=creds,
                        max_chunk_tokens=800,
                        download_timeout=_dl_timeout,
                        native_api_timeout=int(self.cfg.google_api_timeout_s),
                        supports_all_drives=True, query=query,
                        known_title=meta.title if meta else None,
                        known_metadata=_known_meta,
                    )
                    if self.cfg.google_enable_file_content_cache and gc_list:
                        _cache_put(_ck, gc_list, int(self.cfg.google_file_content_cache_max_files))
                uc_list = [_google_chunk_to_unified(gc, query) for gc in gc_list]
                if len(uc_list) > int(self.cfg.google_slide_chunk_cap):
                    orig_len = len(uc_list)
                    cap = int(self.cfg.google_slide_chunk_cap)
                    uc_list = _query_aware_truncate(uc_list, query, cap)
                    logger.info("[google] slide truncate %s: %d -> %d (query-aware)", fid, orig_len, cap)
                if meta:
                    for uc in uc_list:
                        uc.metadata.setdefault("modified_time", meta.modified_time)
                        uc.metadata.setdefault("drive_size", meta.drive_size)
                        uc.metadata.setdefault("owner", meta.owner)
                size = meta.drive_size if meta else None
                logger.info(
                    "TIMING google.file_read type=slide file=%s mime=%s size=%s gc=%d uc=%d ms=%.1f",
                    _short_id(fid), mime.rsplit(".", 1)[-1], size if size is not None else "N/A",
                    len(gc_list), len(uc_list), _elapsed_ms(started),
                )
                logger.info("[google] slide read done %s: mime=%s gc=%d uc=%d size=%s",
                            fid, mime, len(gc_list), len(uc_list), size if size is not None else "N/A")
                status = "ok" if len(uc_list) > 0 else "zero_chunks"
                _log_file_read(fid, "slide", merged_slides.get(fid, set()), status, len(uc_list))
                # Slide 读取后不做文件级截断，直接 mark_done。
                # 文件级 rerank 移到 Phase F 之后（避免 executor shutdown 后丢失）
            except Exception as e:
                logger.warning("[google] slide read failed %s: %s", fid, e)
                _log_file_read(fid, "slide", merged_slides.get(fid, set()), "failed", 0, str(e))
                uc_list = []
            slide_pool.mark_done(fid, uc_list)

        def _read_gmail_task(thread_id: str) -> None:
            """读取 Gmail thread（正文 + 附件 → 统一 chunks）。"""
            if not gmail_pool.mark_reading(thread_id):
                return
            uc_list: List[UnifiedChunk] = []
            sq_ids = merged_gmail.get(thread_id, set())
            query = _resolve_query_for_sq_ids(sq_ids)
            try:
                from google_workspace_tool.gmail_rag_pipeline import (
                    ThreadFetcherTool, MessageParserTool,
                )
                from google_workspace_tool.chunkers.md_chunker import (
                    GoogleChunk, ChunkContentType, estimate_tokens,
                )
                from google_workspace_tool.drive_content_reader import read_chunks_from_bytes
                from tools.google.google_retrieve import _google_chunk_to_unified

                # 1. Fetch thread
                thread_data = ThreadFetcherTool.execute(
                    thread_id, creds,
                    enable_attachments=self.cfg.google_enable_gmail_attachments,
                )
                if not thread_data:
                    gmail_pool.mark_done(thread_id, [])
                    return

                # 2. Parse messages → 正文 chunks + 附件处理
                messages_data = thread_data.get("messages", [])
                for i, msg in enumerate(messages_data, 1):
                    parsed = MessageParserTool.execute(msg)
                    meta = parsed["metadata"]
                    subject = meta.get("Subject", "No Subject")
                    sender = meta.get("From", "Unknown")
                    date = meta.get("Date", "Unknown")
                    to = meta.get("To", "Unknown")
                    body = parsed.get("body_text", "")

                    # 2a. 正文 chunk
                    content_parts = [
                        f"**From:** {sender}",
                        f"**To:** {to}",
                        f"**Date:** {date}",
                        f"**Subject:** {subject}",
                        "",
                        body or "*(No text body)*",
                    ]
                    content = "\n".join(content_parts)
                    gc_body = GoogleChunk(
                        chunk_id=f"google:gmail:body:{thread_id}:{i}",
                        file_id=thread_id,
                        file_title=subject,
                        file_url=f"https://mail.google.com/mail/u/0/#inbox/{thread_id}",
                        heading_path=subject,
                        content_type=ChunkContentType.TEXT,
                        content=content,
                        token_count=estimate_tokens(content),
                        has_table=False,
                        metadata={"thread_id": thread_id, "message_index": i, "type": "gmail_body"},
                    )
                    uc_list.append(_google_chunk_to_unified(gc_body, query))

                    # 2b. 附件处理
                    if not self.cfg.google_enable_gmail_attachments:
                        continue
                    attachments = parsed.get("attachments", [])
                    if not attachments:
                        continue

                    # 附件 >5 个时，name rerank 筛选
                    if len(attachments) > 5:
                        try:
                            from tools.core.rerank import select_search_candidates, build_focus_sub_query_question
                            att_candidates = []
                            for att in attachments:
                                att_candidates.append({
                                    "id": att["filename"],
                                    "title": att["filename"],
                                    "summary": f"Filename: {att['filename']} | Type: {att['mimeType']}",
                                    "_att": att,
                                })
                            focus_text = ""
                            for sq_id in merged_gmail.get(thread_id, set()):
                                for sq in sub_queries:
                                    if sq.sub_query_id == sq_id:
                                        focus_text = focus_text_map.get(sq_id, sq.query)
                                        break
                            question = build_focus_sub_query_question(focus_text, query)
                            rerank_result = select_search_candidates(
                                query=question, candidates=att_candidates,
                                top_n=int(self.cfg.google_gmail_attachment_max_files), score_threshold=0.18, min_keep=1,
                                instruct_template="qa",
                            )
                            if rerank_result.get("status") == "success":
                                selected = list(rerank_result.get("selected_candidates") or [])
                                attachments = [c["_att"] for c in selected if c.get("_att")]
                            else:
                                attachments = attachments[:int(self.cfg.google_gmail_attachment_max_files)]
                        except Exception:
                            attachments = attachments[:int(self.cfg.google_gmail_attachment_max_files)]
                    else:
                        attachments = attachments[:int(self.cfg.google_gmail_attachment_max_files)]

                    # 下载附件 bytes 并用 read_chunks_from_bytes 切 chunks（复用 Drive reader 逻辑）
                    from googleapiclient.discovery import build
                    import base64
                    gmail_service = build('gmail', 'v1', credentials=creds, cache_discovery=False)
                    for att in attachments:
                        try:
                            att_data = gmail_service.users().messages().attachments().get(
                                userId='me', messageId=parsed['messageId'], id=att['attachmentId']
                            ).execute()
                            data_b64 = att_data.get('data', '')
                            if not data_b64:
                                continue
                            padding = 4 - (len(data_b64) % 4)
                            if padding != 4:
                                data_b64 += "=" * padding
                            data_b64 = data_b64.replace("-", "+").replace("_", "/")
                            raw_bytes = base64.b64decode(data_b64)
                            # 复用 Drive reader 的 chunks 切分逻辑
                            att_gc_list = read_chunks_from_bytes(
                                raw_bytes=raw_bytes,
                                mime_type=att['mimeType'],
                                title=att['filename'],
                                file_url=f"https://mail.google.com/mail/u/0/#inbox/{thread_id}",
                                source_tag="google:gmail:att",
                                max_chunk_tokens=800,
                            )
                            for gc in att_gc_list:
                                gc.metadata.update({
                                    "thread_id": thread_id,
                                    "filename": att["filename"],
                                    "mime_type": att["mimeType"],
                                    "type": "gmail_attachment",
                                })
                                uc_list.append(_google_chunk_to_unified(gc, query))
                        except Exception as e:
                            logger.warning("[google] Gmail attachment chunk failed %s: %s",
                                           att.get("filename"), e)
            except Exception as e:
                logger.warning("[google] gmail read failed %s: %s", thread_id, e)
                _log_file_read(thread_id, "gmail", sq_ids, "failed", 0, str(e))
            if uc_list:
                _log_file_read(thread_id, "gmail", sq_ids, "ok", len(uc_list))
            gmail_pool.mark_done(thread_id, uc_list)

        # ── Phase E+F: 并发启动读取任务 + 立即开始等待（不阻塞在 executor shutdown）
        # 每个文件独立 timeout 兜底：超时的立刻返回空，不串行拖累整体
        per_file_timeout = min(float(self.cfg.google_per_file_timeout_s), timeout_s)

        async def _wait_for_sq(sq: SubQuery) -> List[UnifiedChunk]:
            """并发等待该 sq 所需的所有 file 任务，任一超时即刻返回空。"""
            sq_id = sq.sub_query_id
            tasks = []

            async def _wait_one_file(fid: str, ftype: str, wait_coro, done_check) -> List[UnifiedChunk]:
                started = time.perf_counter()
                result = await wait_coro
                done = done_check(fid)
                elapsed = _elapsed_ms(started)
                if not done:
                    logger.warning(
                        "[google] wait timeout sq=%s type=%s file=%s size=%s waited_ms=%.1f",
                        sq_id, ftype, _short_id(fid), _file_size_of(fid, ftype) or "N/A", elapsed,
                    )
                else:
                    logger.info(
                        "TIMING google.wait_done sq=%s type=%s file=%s size=%s chunks=%d ms=%.1f",
                        sq_id, ftype, _short_id(fid), _file_size_of(fid, ftype) or "N/A", len(result), elapsed,
                    )
                return result

            for sel in sq_sheet_selections.get(sq_id, []):
                tasks.append(_wait_one_file(
                    sel.file_id, "sheet",
                    sheet_pool.wait_for_done(sel.file_id, timeout=per_file_timeout),
                    sheet_pool.is_done,
                ))
            for sel in sq_doc_selections.get(sq_id, []):
                tasks.append(_wait_one_file(
                    sel.file_id, "doc",
                    doc_pool.wait_for_done(sel.file_id, timeout=per_file_timeout),
                    doc_pool.is_done,
                ))
            for sel in sq_slide_selections.get(sq_id, []):
                tasks.append(_wait_one_file(
                    sel.file_id, "slide",
                    slide_pool.wait_for_done(sel.file_id, timeout=per_file_timeout),
                    slide_pool.is_done,
                ))
            if self.cfg.google_enable_gmail:
                for sel in sq_gmail_selections.get(sq_id, []):
                    tasks.append(_wait_one_file(
                        sel.thread_id, "gmail",
                        gmail_pool.wait_for_done(sel.thread_id, timeout=per_file_timeout),
                        gmail_pool.is_done,
                    ))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            collected: List[UnifiedChunk] = []
            for res in results:
                if isinstance(res, list):
                    collected.extend(res)
                elif isinstance(res, Exception):
                    logger.debug("[google] wait_for_done exception: %s", res)
            return collected

        use_type_pools = bool(self.cfg.google_enable_type_pools)
        executors: Dict[str, ThreadPoolExecutor] = {}
        if use_type_pools:
            executors["sheet"] = ThreadPoolExecutor(max_workers=int(self.cfg.google_type_pool_sheet_workers))
            executors["doc"] = ThreadPoolExecutor(max_workers=int(self.cfg.google_type_pool_doc_workers))
            executors["slide"] = ThreadPoolExecutor(max_workers=int(self.cfg.google_type_pool_slide_workers))
            executors["gmail"] = ThreadPoolExecutor(max_workers=int(self.cfg.google_type_pool_gmail_workers))
            # nonnative 池：所有 binary 下载（pptx/word/excel/csv/txt）共享，避免与原生 API 抢线程
            executors["nonnative"] = ThreadPoolExecutor(
                max_workers=max(1, int(self.cfg.google_type_pool_nonnative_workers))
            )
        else:
            # 单池模式：所有任务共享一个池
            executors["shared"] = ThreadPoolExecutor(max_workers=12)

        pool_submit_counts: Dict[str, int] = {}
        try:
            # 先注册所有 wait events，避免 wait 先于 worker mark_reading 发生时直接返回空。
            for fid in merged_sheets:
                sheet_pool.ensure_waitable(fid)
            for fid in merged_docs:
                doc_pool.ensure_waitable(fid)
            for fid in merged_slides:
                slide_pool.ensure_waitable(fid)
            if self.cfg.google_enable_gmail:
                for tid in merged_gmail:
                    gmail_pool.ensure_waitable(tid)

            def _get_executor(fid: str, channel: str, pool_meta) -> Tuple[ThreadPoolExecutor, str]:
                """Return (executor, pool_key) — native goes to per-family pool, nonnative goes to shared nonnative pool."""
                if not use_type_pools:
                    return executors["shared"], "shared"
                meta = pool_meta.get_meta(fid) if pool_meta else None
                mime = getattr(meta, "mime_type", None) if meta else None

                if channel == "sheet":
                    # SheetPool only stores native Google Sheets metadata (Phase B batch). meta=None ⇒ Excel/CSV.
                    if meta:
                        return executors["sheet"], "sheet"
                    return executors["nonnative"], "nonnative"
                elif channel == "doc":
                    if mime == _MIME_DOCS:
                        return executors["doc"], "doc"
                    return executors["nonnative"], "nonnative"
                elif channel == "slide":
                    if mime == _MIME_SLIDES:
                        return executors["slide"], "slide"
                    return executors["nonnative"], "nonnative"
                # 未知 channel：fallback 到 nonnative（不复用 native 池，避免抢线程）
                return executors["nonnative"], "nonnative"

            for fid, titles in merged_sheets.items():
                executor, pkey = _get_executor(fid, "sheet", sheet_pool)
                executor.submit(_read_sheet_task, fid, titles)
                pool_submit_counts[pkey] = pool_submit_counts.get(pkey, 0) + 1
            for fid in merged_docs:
                executor, pkey = _get_executor(fid, "doc", doc_pool)
                executor.submit(_read_doc_task, fid)
                pool_submit_counts[pkey] = pool_submit_counts.get(pkey, 0) + 1
            for fid in merged_slides:
                executor, pkey = _get_executor(fid, "slide", slide_pool)
                executor.submit(_read_slide_task, fid)
                pool_submit_counts[pkey] = pool_submit_counts.get(pkey, 0) + 1
            if self.cfg.google_enable_gmail:
                target_key = "gmail" if use_type_pools else "shared"
                for tid in merged_gmail:
                    executors[target_key].submit(_read_gmail_task, tid)
                    pool_submit_counts[target_key] = pool_submit_counts.get(target_key, 0) + 1

            logger.info(
                "[google] pool submits %s type_pools=%s",
                {k: v for k, v in sorted(pool_submit_counts.items()) if v},
                use_type_pools,
            )

            # Phase F: 并发等待（统一短超时截断）
            wait_results = await asyncio.gather(*[_wait_for_sq(sq) for sq in sub_queries])
            for sq, uc_list in zip(sub_queries, wait_results):
                chunks_by_sq[sq.sub_query_id] = uc_list

            # Phase F 汇总 + 漏斗报告
            for sq in sub_queries:
                sq_id = sq.sub_query_id
                logger.info("[google] Phase F sq=%s chunks_collected=%d", sq_id, len(chunks_by_sq.get(sq_id, [])))

                final_uc = chunks_by_sq.get(sq_id, [])
                final_sheet = sum(1 for uc in final_uc if uc.source_tool == "google:sheet")
                final_doc = sum(1 for uc in final_uc if uc.source_tool in ("google:doc", "google:word", "google:txt"))
                final_slide = sum(1 for uc in final_uc if uc.source_tool in ("google:slide", "google:pptx"))
                final_gmail = sum(1 for uc in final_uc if "gmail" in (uc.source_tool or ""))

                raw_res = [r for r in search_results if r["sq"].sub_query_id == sq_id]
                raw_sheet = sum(1 for r in raw_res for f in r.get("files", []) if f.get("mimeType") == _MIME_SHEETS)
                raw_doc = sum(1 for r in raw_res for f in r.get("files", []) if f.get("mimeType") in (_MIME_DOCS, _MIME_WORD, _MIME_WORD_OLD, _MIME_TXT))
                raw_slide = sum(1 for r in raw_res for f in r.get("files", []) if f.get("mimeType") in (_MIME_SLIDES, _MIME_PPTX))
                raw_gmail = sum(len(r.get("gmail_threads", [])) for r in raw_res)

                sel_sheet = len(sq_sheet_selections.get(sq_id, []))
                sel_doc = len(sq_doc_selections.get(sq_id, []))
                sel_slide = len(sq_slide_selections.get(sq_id, []))
                sel_gmail = len(sq_gmail_selections.get(sq_id, []))

                logger.info(
                    "[google] FUNNEL sq=%s | raw(S=%d,D=%d,SL=%d,G=%d) → selected(S=%d,D=%d,SL=%d,G=%d) → final(S=%d,D=%d,SL=%d,G=%d)",
                    sq_id, raw_sheet, raw_doc, raw_slide, raw_gmail,
                    sel_sheet, sel_doc, sel_slide, sel_gmail,
                    final_sheet, final_doc, final_slide, final_gmail,
                )

            # 文件级读取结果追踪
            failed_reads = [(fid, info) for fid, info in file_read_log.items() if info["status"] in ("failed", "zero_chunks")]
            if failed_reads:
                logger.info("[google] FILE_READ_ISSUES count=%d", len(failed_reads))
                for fid, info in failed_reads[:10]:
                    logger.info("[google] FILE_READ_ISSUE %s type=%s status=%s chunks=%d error=%.100s sqs=%s",
                                fid, info["type"], info["status"], info["chunks"], info.get("error", ""), info["sq_ids"])

            # ── Phase F-G: Slide/Sheet 文件级 qwen rerank（并行，已读取好的继续处理）
            async def _rerank_one_file(sq: SubQuery, fid: str, file_ucs: List[UnifiedChunk]):
                started = time.perf_counter()
                if len(file_ucs) > 80:
                    file_ucs = _query_aware_truncate(file_ucs, sq.query, 80)
                selected = await asyncio.to_thread(
                    _select_chunks_for_sub_query,
                    query=sq.query, focus_text=sq.query, query_type="concept",
                    uc_list=file_ucs, top_n=5,
                )
                return sq.sub_query_id, fid, selected, len(file_ucs), _elapsed_ms(started)

            rerank_tasks = []
            rerank_targets: List[tuple] = []
            for sq in sub_queries:
                sq_id = sq.sub_query_id
                uc_list = chunks_by_sq.get(sq_id, [])
                if not uc_list:
                    continue
                by_file: Dict[str, List[UnifiedChunk]] = {}
                for uc in uc_list:
                    fid = (uc.metadata or {}).get("file_id", "")
                    by_file.setdefault(fid, []).append(uc)
                for fid, file_ucs in by_file.items():
                    is_slide = any(uc.source_tool in ("google:slide", "google:pptx") for uc in file_ucs)
                    is_sheet = any(uc.source_tool == "google:sheet" for uc in file_ucs)
                    rerank_threshold = 5
                    if self.cfg.google_enable_conditional_file_rerank:
                        rerank_threshold = (
                            int(self.cfg.google_file_rerank_min_chunks_slide) if is_slide
                            else int(self.cfg.google_file_rerank_min_chunks_sheet)
                        )
                    if (is_slide or is_sheet) and len(file_ucs) > rerank_threshold:
                        rerank_tasks.append(_rerank_one_file(sq, fid, file_ucs))
                        rerank_targets.append((sq_id, fid))

            if rerank_tasks:
                results = await asyncio.gather(*rerank_tasks)
                replace_map: Dict[tuple, List[UnifiedChunk]] = {}
                for (sq_id, fid, selected, orig_len, rerank_ms), target in zip(results, rerank_targets):
                    replace_map[target] = selected
                    source_tool = file_ucs[0].source_tool if False else ""
                    matched_ucs = []
                    for uc in chunks_by_sq.get(sq_id, []):
                        if (uc.metadata or {}).get("file_id", "") == fid:
                            matched_ucs.append(uc)
                    source_tool = matched_ucs[0].source_tool if matched_ucs else ""
                    ftype = "sheet" if source_tool == "google:sheet" else "slide"
                    logger.info(
                        "[google] file-level rerank sq=%s type=%s file=%s size=%s %d->%d ms=%.1f",
                        sq_id, ftype, _short_id(fid), _file_size_of(fid, ftype) or "N/A",
                        orig_len, len(selected), rerank_ms,
                    )

                for sq in sub_queries:
                    sq_id = sq.sub_query_id
                    uc_list = chunks_by_sq.get(sq_id, [])
                    if not uc_list:
                        continue
                    by_file = {}
                    for uc in uc_list:
                        fid = (uc.metadata or {}).get("file_id", "")
                        by_file.setdefault(fid, []).append(uc)
                    new_uc_list: List[UnifiedChunk] = []
                    for fid, file_ucs in by_file.items():
                        new_uc_list.extend(replace_map.get((sq_id, fid), file_ucs))
                    chunks_by_sq[sq_id] = new_uc_list

            # ── Phase G: Per-sub_query qwen rerank（chunk 级）──
            # 注：之前尝试过 eval_only preview 流水线（与本段并行），但 preview pool 是 pre-rerank
            # 的 reader_score 粗排，LLM 偶尔判 0 选（Q44 实测），引入语义不稳定性。已回滚。
            #
            # 若未来再启 preview pipeline，需用 final chunk_pool 的同构超集（含 sub-query rerank score），
            # 而不是 chunks_by_sq 的 raw reader_score chunks。
            for sq in sub_queries:
                uc_list = chunks_by_sq.get(sq.sub_query_id, [])
                if not uc_list:
                    continue

                focus_text = focus_text_map[sq.sub_query_id]
                query_type = _normalize_google_query_type(sq.query_type)

                selected = _select_chunks_for_sub_query(
                    query=sq.query, focus_text=focus_text, query_type=query_type,
                    uc_list=uc_list, top_n=self.cfg.sub_query_top_n,
                )

                for uc in selected:
                    entry = _unified_to_chunk_entry(uc, sq.query, sq.target_focus)
                    existing = chunk_pool.get(entry.id)
                    chunk_pool[entry.id] = existing.merge(entry) if existing else entry

                logger.info("[google] Phase G sq=%s chunk_rerank input=%d output=%d", sq.sub_query_id, len(uc_list), len(selected))

            # ── Phase G-3: cross-sq pre-eval Qwen rerank（X1 收口）─────────
            # 触发条件：chunk_pool > threshold（默认 source_top_k × 2 = 10）
            # 目的：把 chunk_pool 收紧到 source_top_k × 2，让 eval_only LLM 输入减半
            # 收益：eval_only -1~1.5s；新增 rerank ~0.3-0.5s；净 -0.7~1.0s
            if (self.cfg.google_enable_cross_sq_pre_filter
                    and len(chunk_pool) > int(self.cfg.google_cross_sq_pre_filter_threshold)):
                try:
                    pre_filter_started = time.perf_counter()
                    target_n = max(int(self.cfg.source_top_k) * 2, int(self.cfg.source_top_k) + 2)
                    # 用 focus 全文集合作为 query（跨 sq 评估）
                    seen = set()
                    focus_texts = []
                    for sq in sub_queries:
                        f = self._current_focus_lookup.get(sq.target_focus)
                        if f and f.focus_id not in seen:
                            seen.add(f.focus_id)
                            focus_texts.append(f.text)
                    combined_focus = " | ".join(focus_texts) if focus_texts else sub_queries[0].query
                    # ChunkEntry → UnifiedChunk（带回查 entry id）
                    pool_uc = []
                    pool_entry_ids = []
                    for entry in chunk_pool.values():
                        emeta = dict(entry.metadata or {})
                        emeta["_chunk_pool_entry_id"] = entry.id
                        pool_uc.append(UnifiedChunk(
                            text=entry.content,
                            source_name=emeta.get("source_name", ""),
                            source_url=emeta.get("source_url", ""),
                            source_tool=entry.source,
                            heading_path=emeta.get("heading_path", ""),
                            score=float(entry.score or 0.0),
                            confidence_tier=entry.tier or "",
                            metadata=emeta,
                        ))
                        pool_entry_ids.append(entry.id)
                    # 跨 sq rerank
                    selected_uc = _select_chunks_for_sub_query(
                        query=combined_focus, focus_text=combined_focus, query_type="concept",
                        uc_list=pool_uc, top_n=target_n,
                    )
                    # 提取保留的 entry_ids
                    keep_ids = set()
                    for uc in selected_uc:
                        eid = (uc.metadata or {}).get("_chunk_pool_entry_id")
                        if eid:
                            keep_ids.add(eid)
                    # 安全保护：rerank 异常时 keep_ids 为空 → 不缩 pool
                    if keep_ids:
                        orig_size = len(chunk_pool)
                        new_pool = {eid: chunk_pool[eid] for eid in keep_ids if eid in chunk_pool}
                        chunk_pool.clear()
                        chunk_pool.update(new_pool)
                        logger.info(
                            "TIMING google.cross_sq_pre_filter chunk_pool %d->%d ms=%.1f",
                            orig_size, len(chunk_pool), _elapsed_ms(pre_filter_started),
                        )
                    else:
                        logger.warning("[google] cross-sq pre-filter returned empty; chunk_pool unchanged")
                except Exception as e:
                    logger.warning("[google] cross-sq pre-filter error: %s; chunk_pool unchanged", e)

            logger.info("TIMING google.batch_total sub_queries=%d chunk_pool=%d ms=%.1f",
                         len(sub_queries), len(chunk_pool), _elapsed_ms(batch_started))

        finally:
            for executor in executors.values():
                executor.shutdown(wait=False)

    # ── 保留旧入口（兼容单 sq 调用）─────────────────────────────────────

    async def execute_sub_query_full(self, sq: SubQuery) -> List[ChunkEntry]:
        """v2 执行入口：接收完整 SubQuery，执行检索并进行跨文档的 chunk 重排。

        注意：在 _execute_batch 覆写后，此方法不再被基类直接调用，
        但保留作为独立调用的兼容入口。
        """
        from tools.core.kw_generalization import generate_grouped_keywords
        from tools.google.google_retrieve import google_retrieve

        enable_fulltext = sq.google_enable_fulltext if sq.google_enable_fulltext is not None else self.cfg.google_enable_fulltext_search
        order_by = sq.google_order_by if sq.google_order_by is not None else self.cfg.google_order_by_mode
        keyword_groups = generate_grouped_keywords(sq.query)

        logger.info(
            "[google] execute sub_query: %r, keyword_groups: %s, enable_fulltext: %s, order_by: %s",
            sq.query, keyword_groups, enable_fulltext, order_by
        )

        uc_list = await google_retrieve(
            query=sq.query,
            keyword_groups=keyword_groups,
            download_timeout=int(self.cfg.google_download_timeout_s),
            native_api_timeout=int(self.cfg.google_api_timeout_s),
            enable_gmail=self.cfg.google_enable_gmail,
            enable_gmail_attachments=self.cfg.google_enable_gmail_attachments,
            enable_sheet_filter=self.cfg.google_enable_sheet_filter,
            enable_excel_filter=self.cfg.google_enable_excel_filter,
            enable_fulltext_search=enable_fulltext,
            order_by_mode=order_by,
        )
        if not uc_list:
            return []

        focus_obj = self._current_focus_lookup.get(sq.target_focus)
        focus_text = focus_obj.text if focus_obj else sq.target_focus
        query_type = sq.query_type or "concept"

        selected = _select_chunks_for_sub_query(
            query=sq.query,
            focus_text=focus_text,
            query_type=query_type,
            uc_list=uc_list,
            top_n=self.cfg.sub_query_top_n,
        )

        return [_unified_to_chunk_entry(uc, sq.query, sq.target_focus) for uc in selected]
