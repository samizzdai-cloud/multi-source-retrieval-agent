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
from google.adk.agents.llm_agent import LlmAgent


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
        )

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
        return (
            "【Google Workspace 专属规划指南】\n"
            "Google 源支持强大的全文与标题检索切换，你需要根据用户的时效性诉求做出精准调度：\n"
            "1. 默认情况下，保持 `google_enable_fulltext = true` 且 `google_order_by = null`，以获取最深度的全文匹配（由 Google 算法按相关度评分）。\n"
            "2. 如果用户在寻找近期刚刚编写、修改、阅读的文件（如：'最新的预算'、'刚看完的材料'），你应该设置 `google_enable_fulltext = false`（退化为标题检索），"
            "并开启强制排序 `google_order_by = 'viewedByMeTime desc'` (最近阅读) 或 `'modifiedTime desc'` (最近修改)。\n"
            "3. 即使开启了 false，query 也要足够精确，不要带 '最新' 等口语化修饰词。\n"
            "4. tags/indexes/time_filter/query_type 字段对 Google 源无效，始终留空。"
        )

    async def execute_sub_query_full(self, sq: SubQuery) -> List[ChunkEntry]:
        """v2 执行入口：接收完整 SubQuery，执行检索并进行跨文档的 chunk 重排。"""
        
        # 优先使用大模型在单条 SubQuery 中生成的动态配置，如果为 None 则使用 Agent 全局默认配置
        enable_fulltext = sq.google_enable_fulltext if sq.google_enable_fulltext is not None else self.cfg.google_enable_fulltext_search
        order_by = sq.google_order_by if sq.google_order_by is not None else self.cfg.google_order_by_mode

        logger.info(
            "[google] execute sub_query: %r, enable_fulltext: %s, order_by: %s", 
            sq.query, enable_fulltext, order_by
        )

        uc_list = await google_retrieve(
            query=sq.query,
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
