"""GoogleSourceAgent——Google Workspace / 搜索源适配器（骨架）。

Phase 4 骨架版：
- 继承 ``SourceAgent`` 通用骨架；单/多 loop 由 ``SourceAgentConfig`` 决定
- ``execute_sub_query`` 调 ``tools/google_retrieve.py``（当前为 stub，返回空列表）
- 真实 SDK 接入、认证、time_filter 等运行时参数留待后续实装
- 即便 stub 返回空，agent 仍可被纳入 enabled_sources 做联调（返回 0 chunk 不会报错）
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import List, Optional

from tools.google.google_retrieve import google_retrieve
from tools.core.models import ChunkEntry, SourceAgentConfig, UnifiedChunk
from tools.core.source_profiles import get_profile
from tools.core.rerank import (
    build_focus_sub_query_question,
    get_chunk_selection_config,
    select_reranked_chunks,
)

from agents.source_agent_base import SourceAgent, SubQuery
from agents.sub_agents import (
    build_eval_only_agent,
    build_plan_eval_agent,
    build_plan_only_agent,
)


logger = logging.getLogger(__name__)


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
    """UnifiedChunk → ChunkEntry。id 设计见 spec_plan §3。

    Google 结果通常以 URL 作为稳定主键，node_id 对长文档可取锚点 / 段落 hash。
    """
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


class GoogleSourceAgent(SourceAgent):
    """Google SourceAgent（骨架，底层 tool 未实装）。"""

    source = "google"

    def __init__(self, cfg: Optional[SourceAgentConfig] = None):
        cfg = cfg or SourceAgentConfig()
        profile = get_profile("google")
        super().__init__(
            profile=profile,
            cfg=cfg,
            plan_only_agent=build_plan_only_agent(name="google_plan_only"),
            eval_only_agent=build_eval_only_agent(
                source_top_k=cfg.source_top_k, name="google_eval_only",
            ),
            plan_eval_agent=build_plan_eval_agent(
                source_top_k=cfg.source_top_k, name="google_plan_eval",
            ) if cfg.max_loops > 1 else None,
        )

    async def execute_sub_query_full(self, sq: SubQuery) -> List[ChunkEntry]:
        """v2 执行入口：接收完整 SubQuery，执行检索并进行跨文档的 chunk 重排。"""
        uc_list = await google_retrieve(
            query=sq.query,
            download_timeout=int(self.cfg.google_download_timeout_s),
            native_api_timeout=int(self.cfg.google_api_timeout_s),
            enable_gmail=self.cfg.google_enable_gmail,
            enable_gmail_attachments=self.cfg.google_enable_gmail_attachments,
            enable_sheet_filter=self.cfg.google_enable_sheet_filter,
            enable_excel_filter=self.cfg.google_enable_excel_filter,
        )
        if not uc_list:
            return []

        # 确定 focus_text，用于 rerank 打分
        focus_obj = self._current_focus_lookup.get(sq.target_focus)
        focus_text = focus_obj.text if focus_obj else sq.target_focus
        query_type = sq.query_type or "concept"

        # 使用语义重排从所有候选 chunks 中提纯
        selected = _select_chunks_for_sub_query(
            query=sq.query,
            focus_text=focus_text,
            query_type=query_type,
            uc_list=uc_list,
            top_n=self.cfg.sub_query_top_n,
        )
        
        return [_unified_to_chunk_entry(uc, sq.query, sq.target_focus) for uc in selected]
