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
import time
from typing import Dict, List, Optional, Set

from tools.core.models import ChunkEntry, SourceAgentConfig, UnifiedChunk
from tools.core.source_profiles import get_profile
from tools.core.rerank import (
    build_focus_sub_query_question,
    get_chunk_selection_config,
    select_reranked_chunks,
)

from agents.source_agent_base import SourceAgent, SubQuery
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


def _uc_to_rerank_payload(uc: UnifiedChunk) -> dict:
    return {
        "text": uc.text or "",
        "source_name": uc.source_name or "",
        "heading_path": uc.heading_path or "",
    }


def _fallback_select(uc_list: List[UnifiedChunk], top_n: int) -> List[UnifiedChunk]:
    return sorted(uc_list, key=lambda x: float(x.score or 0.0), reverse=True)[:top_n]


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
    ) -> List[str]:
        """对 file 列表做 qwen rerank，返回选中的 file_id 列表。

        Args:
            query: sub_query 文本
            focus_text: focus 文本
            files: Drive search 返回的 file dict 列表（含 name, description, owners 等）
            top_n: 最多返回几个 file
            use_description: 是否使用 file.description 作为 summary
        """
        if not files:
            return []
        if len(files) <= top_n:
            return [f.get("id", "") for f in files if f.get("id")]

        try:
            from tools.core.rerank import select_search_candidates, build_focus_sub_query_question
        except ImportError:
            return [f.get("id", "") for f in files[:top_n]]

        candidates = []
        for f in files:
            parts = [f"Title: {f.get('name', '')}"]
            if use_description and f.get("description"):
                parts.append(f"Desc: {f.get('description', '')}")
            owners = ", ".join([o.get("displayName", "") for o in f.get("owners", []) if o.get("displayName")])
            if owners:
                parts.append(f"Owner: {owners}")
            if f.get("modifiedTime"):
                parts.append(f"Modified: {f.get('modifiedTime', '')}")
            candidates.append({
                "id": f.get("id", ""),
                "title": f.get("name", ""),
                "summary": " | ".join(parts),
                "_file_id": f.get("id", ""),
            })

        question = build_focus_sub_query_question(focus_text, query)
        rerank_result = select_search_candidates(
            query=question,
            candidates=candidates,
            top_n=top_n,
            score_threshold=score_threshold,
            min_keep=min_keep,
            instruct_template="qa",
        )

        if rerank_result.get("status") == "success":
            selected = list(rerank_result.get("selected_candidates") or [])
            return [c.get("_file_id", "") for c in selected if c.get("_file_id")]
        else:
            return [f.get("id", "") for f in files[:top_n]]

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
            order_by_param = order_by if not enable_fulltext else None

            # ── 通道 A: Doc + Presentation 搜索（宽召回：前 4 个关键词组，每个 sq 召回 12 个）
            doc_slide_mimes = {
                _MIME_DOCS, _MIME_SLIDES, _MIME_WORD, _MIME_WORD_OLD, _MIME_PPTX, _MIME_TXT,
            }
            q_doc_slide = _build_search_q(
                sq.query, keyword_groups=keyword_groups, enable_fulltext=enable_fulltext,
                mime_types=doc_slide_mimes, max_groups=4,
            )
            try:
                files_doc_slide = search_drive(
                    q=q_doc_slide, page_size=12, max_pages=1,
                    order_by=order_by_param, supports_all_drives=True,
                    include_items_from_all_drives=True, corpora='allDrives',
                    creds=creds,
                )
            except Exception as e:
                logger.warning("[google] Drive doc/slide search failed for sq=%s: %s", sq.sub_query_id, e)
                files_doc_slide = []

            # ── 通道 B: Sheet 搜索（精准召回：全部关键词组，每个 sq 召回 5 个）
            sheet_mimes = {
                _MIME_SHEETS, _MIME_EXCEL, _MIME_EXCEL_OLD, _MIME_EXCEL_MACRO, _MIME_CSV,
            }
            q_sheet = _build_search_q(
                sq.query, keyword_groups=keyword_groups, enable_fulltext=enable_fulltext,
                mime_types=sheet_mimes,
            )
            try:
                files_sheet = search_drive(
                    q=q_sheet, page_size=5, max_pages=1,
                    order_by=order_by_param, supports_all_drives=True,
                    include_items_from_all_drives=True, corpora='allDrives',
                    creds=creds,
                )
            except Exception as e:
                logger.warning("[google] Drive sheet search failed for sq=%s: %s", sq.sub_query_id, e)
                files_sheet = []

            files = files_doc_slide + files_sheet
            logger.info("[google] search sq=%s query=%r doc_slide=%d sheet=%d",
                        sq.sub_query_id, sq.query, len(files_doc_slide), len(files_sheet))

            # ── Gmail 搜索（独立，不等待 Drive）─────────────────────────────
            gmail_threads = []
            if self.cfg.google_enable_gmail:
                try:
                    from google_workspace_tool.gmail_search_tool import search_gmail
                    gmail_q = self._build_gmail_q(sq.query, keyword_groups)
                    gmail_results = search_gmail(
                        q=gmail_q,
                        max_results=self.cfg.google_max_gmail_threads_per_subquery,
                        max_pages=1,
                        creds=creds,
                    )
                    # search_gmail 返回 message 列表，按 threadId 去重
                    seen_threads = set()
                    for m in gmail_results:
                        tid = m.get("threadId")
                        if tid and tid not in seen_threads:
                            seen_threads.add(tid)
                            gmail_threads.append({"id": tid, "messageId": m.get("id")})
                except Exception as e:
                    logger.warning("[google] Gmail search failed for sq=%s: %s", sq.sub_query_id, e)

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

        # ── Phase B: 收集所有 spreadsheet file_ids，batchGet 元数据 ────────
        # SheetPool 元数据获取是同步阻塞调用，但只影响 Sheet 的后续 rerank/读取，
        # 与 Doc/Slide/Gmail 的后续处理互不等待。
        all_spreadsheet_ids: Set[str] = set()
        for res in search_results:
            for f in res.get("files", []):
                if f.get("mimeType") == _MIME_SHEETS:
                    all_spreadsheet_ids.add(f.get("id"))

        # 取 creds（所有 search_result 的 creds 都相同）
        creds = None
        for res in search_results:
            if res.get("creds"):
                creds = res["creds"]
                break

        # SheetPool batchGet 元数据（同步，但 Doc/Slide/Gmail 不依赖它）
        if all_spreadsheet_ids and creds:
            sheet_pool.batch_fetch_metadata(
                list(all_spreadsheet_ids),
                creds,
                native_api_timeout=int(self.cfg.google_api_timeout_s),
            )

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
                if mime == _MIME_DOCS or mime in (_MIME_WORD, _MIME_WORD_OLD, _MIME_TXT):
                    from google_workspace_tool.doc_pool import DocMeta
                    doc_pool.add_meta(DocMeta(
                        file_id=fid,
                        title=f.get("name", ""),
                        url=f"https://docs.google.com/document/d/{fid}/edit" if mime == _MIME_DOCS else f"https://drive.google.com/file/d/{fid}/view",
                        mime_type=mime,
                        description=f.get("description", ""),
                        drive_size=f.get("size"),
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
                        drive_size=f.get("size"),
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
                    # 用首封邮件的 snippet 作为 thread 的摘要（先占位，后续 fetch 时更新）
                    from google_workspace_tool.gmail_pool import GmailThreadMeta
                    gmail_pool.add_meta(GmailThreadMeta(
                        thread_id=tid,
                        subject="",
                        sender="",
                        date="",
                        snippet="",
                        url=f"https://mail.google.com/mail/u/0/#inbox/{tid}",
                    ))

        # ── Phase C: Per-sub_query 筛选（qwen rerank）───────────────────────
        focus_text_map = {}
        for sq in sub_queries:
            focus_obj = self._current_focus_lookup.get(sq.target_focus)
            focus_text_map[sq.sub_query_id] = focus_obj.text if focus_obj else sq.target_focus

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

        # C2. Doc 筛选
        sq_doc_selections: Dict[str, List] = {}  # sq_id → [DocSelection]
        for res in search_results:
            sq = res["sq"]
            sq_doc_selections[sq.sub_query_id] = []
            focus_text = focus_text_map[sq.sub_query_id]
            doc_files = [f for f in res.get("files", []) if f.get("mimeType") in (_MIME_DOCS, _MIME_WORD, _MIME_WORD_OLD, _MIME_TXT)]
            selected_docs = self._select_file_candidates(
                query=sq.query, focus_text=focus_text,
                files=doc_files, top_n=self.cfg.google_doc_candidates_per_sq,
                use_description=True,
            )
            for fid in selected_docs:
                from google_workspace_tool.doc_pool import DocSelection
                sq_doc_selections[sq.sub_query_id].append(
                    DocSelection(file_id=fid, sub_query_id=sq.sub_query_id, focus_id=sq.target_focus))

        # C3. Slide 筛选
        sq_slide_selections: Dict[str, List] = {}
        for res in search_results:
            sq = res["sq"]
            sq_slide_selections[sq.sub_query_id] = []
            focus_text = focus_text_map[sq.sub_query_id]
            slide_files = [f for f in res.get("files", []) if f.get("mimeType") in (_MIME_SLIDES, _MIME_PPTX)]
            selected_slides = self._select_file_candidates(
                query=sq.query, focus_text=focus_text,
                files=slide_files, top_n=self.cfg.google_slide_candidates_per_sq,
                use_description=False,
            )
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
                # 如果 thread 数 ≤ top_n，直接全量返回
                top_n = self.cfg.google_gmail_rerank_top_n
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

        # ── Phase E: 并发读取（四类独立，互不等待）───────────────────────────
        chunks_by_sq: Dict[str, List[UnifiedChunk]] = {sq.sub_query_id: [] for sq in sub_queries}
        timeout_s = self.cfg.source_timeout_s

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
                gc_list = read_drive_chunks(
                    file_id=fid, mime_type=_MIME_SHEETS, creds=creds,
                    max_chunk_tokens=800, native_api_timeout=int(self.cfg.google_api_timeout_s),
                    supports_all_drives=True, range_names=range_names, query=query,
                )
                uc_list = [_google_chunk_to_unified(gc, query) for gc in gc_list]
                # Sheet BM25 收紧：>50 chunks 先截断到 50，避免 Phase G 输入爆炸
                if len(uc_list) > 50:
                    orig_len = len(uc_list)
                    uc_list = _fallback_select(uc_list, 50)
                    logger.info("[google] sheet truncate %s: %d -> 50", fid, orig_len)
                logger.info("[google] sheet read done %s: titles=%d range_names=%s gc=%d uc=%d",
                            fid, len(titles), range_names, len(gc_list), len(uc_list))
            except Exception as e:
                logger.warning("[google] sheet read failed %s: %s", fid, e)
                _log_file_read(fid, "sheet", sheet_sq_map.get(fid, set()), "failed", 0, str(e))
                uc_list = []
            sheet_pool.mark_done(fid, uc_list)

        def _read_doc_task(fid: str) -> None:
            if not doc_pool.mark_reading(fid):
                return
            meta = doc_pool.get_meta(fid)
            mime = meta.mime_type if meta else _MIME_DOCS
            sq_ids = merged_docs.get(fid, set())
            query = _resolve_query_for_sq_ids(sq_ids)
            try:
                from tools.google.google_retrieve import _google_chunk_to_unified
                gc_list = read_drive_chunks(
                    file_id=fid, mime_type=mime, creds=creds,
                    max_chunk_tokens=800,
                    download_timeout=int(self.cfg.google_download_timeout_s),
                    native_api_timeout=int(self.cfg.google_api_timeout_s),
                    supports_all_drives=True, query=query,
                )
                uc_list = [_google_chunk_to_unified(gc, query) for gc in gc_list]
                logger.info("[google] doc read done %s: mime=%s gc=%d uc=%d",
                            fid, mime, len(gc_list), len(uc_list))
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
            meta = slide_pool.get_meta(fid)
            mime = meta.mime_type if meta else _MIME_SLIDES
            query = _resolve_query_for_sq_ids(merged_slides.get(fid, set()))
            try:
                from tools.google.google_retrieve import _google_chunk_to_unified
                gc_list = read_drive_chunks(
                    file_id=fid, mime_type=mime, creds=creds,
                    max_chunk_tokens=800,
                    download_timeout=int(self.cfg.google_download_timeout_s),
                    native_api_timeout=int(self.cfg.google_api_timeout_s),
                    supports_all_drives=True, query=query,
                )
                uc_list = [_google_chunk_to_unified(gc, query) for gc in gc_list]
                logger.info("[google] slide read done %s: mime=%s gc=%d uc=%d",
                            fid, mime, len(gc_list), len(uc_list))
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
                                top_n=5, score_threshold=0.18, min_keep=1,
                                instruct_template="qa",
                            )
                            if rerank_result.get("status") == "success":
                                selected = list(rerank_result.get("selected_candidates") or [])
                                attachments = [c["_att"] for c in selected if c.get("_att")]
                            else:
                                attachments = attachments[:5]
                        except Exception:
                            attachments = attachments[:5]

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
        # 每个文件独立 12s 硬上限：超时的立刻返回空，不串行拖累整体
        per_file_timeout = min(12.0, timeout_s)

        async def _wait_for_sq(sq: SubQuery) -> List[UnifiedChunk]:
            """并发等待该 sq 所需的所有 file 任务，任一超时即刻返回空。"""
            sq_id = sq.sub_query_id
            tasks = []
            for sel in sq_sheet_selections.get(sq_id, []):
                tasks.append(sheet_pool.wait_for_done(sel.file_id, timeout=per_file_timeout))
            for sel in sq_doc_selections.get(sq_id, []):
                tasks.append(doc_pool.wait_for_done(sel.file_id, timeout=per_file_timeout))
            for sel in sq_slide_selections.get(sq_id, []):
                tasks.append(slide_pool.wait_for_done(sel.file_id, timeout=per_file_timeout))
            if self.cfg.google_enable_gmail:
                for sel in sq_gmail_selections.get(sq_id, []):
                    tasks.append(gmail_pool.wait_for_done(sel.thread_id, timeout=per_file_timeout))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            collected: List[UnifiedChunk] = []
            for res in results:
                if isinstance(res, list):
                    collected.extend(res)
                elif isinstance(res, Exception):
                    logger.debug("[google] wait_for_done exception: %s", res)
            return collected

        executor = ThreadPoolExecutor(max_workers=12)
        try:
            for fid, titles in merged_sheets.items():
                executor.submit(_read_sheet_task, fid, titles)
            for fid in merged_docs:
                executor.submit(_read_doc_task, fid)
            for fid in merged_slides:
                executor.submit(_read_slide_task, fid)
            if self.cfg.google_enable_gmail:
                for tid in merged_gmail:
                    executor.submit(_read_gmail_task, tid)

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
                if len(file_ucs) > 80:
                    file_ucs = _fallback_select(file_ucs, 80)
                selected = await asyncio.to_thread(
                    _select_chunks_for_sub_query,
                    query=sq.query, focus_text=sq.query, query_type="concept",
                    uc_list=file_ucs, top_n=5,
                )
                return sq.sub_query_id, fid, selected, len(file_ucs)

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
                    if (is_slide or is_sheet) and len(file_ucs) > 5:
                        rerank_tasks.append(_rerank_one_file(sq, fid, file_ucs))
                        rerank_targets.append((sq_id, fid))

            if rerank_tasks:
                results = await asyncio.gather(*rerank_tasks)
                replace_map: Dict[tuple, List[UnifiedChunk]] = {}
                for (sq_id, fid, selected, orig_len), target in zip(results, rerank_targets):
                    replace_map[target] = selected
                    logger.info("[google] file-level rerank sq=%s file=%s %d->%d", sq_id, fid[-8:], orig_len, len(selected))

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

            # ── Phase G: Per-sub_query qwen rerank（chunk 级）───────────────────
            for sq in sub_queries:
                uc_list = chunks_by_sq.get(sq.sub_query_id, [])
                if not uc_list:
                    continue

                focus_text = focus_text_map[sq.sub_query_id]
                query_type = sq.query_type or "concept"

                selected = _select_chunks_for_sub_query(
                    query=sq.query, focus_text=focus_text, query_type=query_type,
                    uc_list=uc_list, top_n=self.cfg.sub_query_top_n,
                )

                for uc in selected:
                    entry = _unified_to_chunk_entry(uc, sq.query, sq.target_focus)
                    existing = chunk_pool.get(entry.id)
                    chunk_pool[entry.id] = existing.merge(entry) if existing else entry

                logger.info("[google] Phase G sq=%s chunk_rerank input=%d output=%d", sq.sub_query_id, len(uc_list), len(selected))

            logger.info("TIMING google.batch_total sub_queries=%d chunk_pool=%d ms=%.1f",
                         len(sub_queries), len(chunk_pool), _elapsed_ms(batch_started))

        finally:
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
