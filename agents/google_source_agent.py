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
from typing import List, Optional

from tools.google_retrieve import google_retrieve
from tools.models import ChunkEntry, SourceAgentConfig, UnifiedChunk
from tools.source_profiles import get_profile

from agents.source_agent_base import SourceAgent
from agents.sub_agents import (
    build_eval_only_agent,
    build_plan_eval_agent,
    build_plan_only_agent,
)


logger = logging.getLogger(__name__)


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

    async def execute_sub_query(
        self, sub_query: str, target_focus: str,
    ) -> List[ChunkEntry]:
        """走 google_retrieve（Drive 全文搜索 + 分块读取）。"""
        uc_list = await google_retrieve(query=sub_query)
        if not uc_list:
            return []

        uc_list = sorted(uc_list, key=lambda x: x.score or 0.0, reverse=True)
        uc_list = uc_list[: self.cfg.sub_query_top_n]
        return [_unified_to_chunk_entry(uc, sub_query, target_focus) for uc in uc_list]
