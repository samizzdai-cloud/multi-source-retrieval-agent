"""NotionSourceAgent——Notion 工作空间源适配器（骨架）。

Phase 4 骨架版：
- 继承 ``SourceAgent`` 通用骨架；单/多 loop 由 ``SourceAgentConfig`` 决定
- ``execute_sub_query`` 调 ``tools/notion_retrieve.py``（当前为 stub，返回空列表）
- 真实 SDK 接入、token 注入留待后续实装
"""

from __future__ import annotations

import hashlib
import logging
from typing import List, Optional

from tools.notion.notion_retrieve import notion_retrieve
from tools.core.models import ChunkEntry, SourceAgentConfig, UnifiedChunk
from tools.core.source_profiles import get_profile

from agents.source_agent_base import SourceAgent
from google.adk.agents.llm_agent import LlmAgent


logger = logging.getLogger(__name__)


def _unified_to_chunk_entry(
    uc: UnifiedChunk, sub_query: str, target_focus: str,
) -> ChunkEntry:
    """UnifiedChunk → ChunkEntry。id 设计见 spec_plan §3。

    Notion 以 page_id 作为稳定主键，block_id 作为 node 级粒度。
    """
    meta = uc.metadata or {}
    doc_id = (
        meta.get("page_id")
        or meta.get("doc_id")
        or uc.source_url
        or uc.source_name
        or "unknown"
    )
    node_id = (
        meta.get("block_id")
        or meta.get("chunk_id")
        or hashlib.md5((uc.text or "").encode("utf-8")).hexdigest()[:10]
    )
    cid = f"notion:{doc_id}:{node_id}"

    merged_meta = {
        **meta,
        "source_name": uc.source_name,
        "source_url": uc.source_url,
        "heading_path": uc.heading_path,
        "source_tool": uc.source_tool or "notion",
    }

    return ChunkEntry(
        id=cid,
        source="notion",
        content=uc.text or "",
        metadata=merged_meta,
        score=uc.score,
        sub_query=sub_query,
        target_focuses={target_focus},
        tier=None,
    )


class NotionSourceAgent(SourceAgent):
    """Notion SourceAgent（骨架，底层 tool 未实装）。"""

    source = "notion"
    DEFAULT_EXECUTOR_WORKERS: int = 4

    def __init__(self, cfg: Optional[SourceAgentConfig] = None, executor_workers: Optional[int] = None):
        cfg = cfg or SourceAgentConfig()
        profile = get_profile("notion")
        super().__init__(
            profile=profile,
            cfg=cfg,
            executor_workers=executor_workers,
        )

    def _build_plan_only_agent(self) -> Optional[LlmAgent]:
        from .notion_retrieve_llm_build import build_notion_plan_only_agent
        return build_notion_plan_only_agent()

    def _build_eval_only_agent(self) -> LlmAgent:
        from .notion_retrieve_llm_build import build_notion_eval_only_agent
        return build_notion_eval_only_agent(self.cfg.source_top_k)

    def _build_plan_eval_agent(self) -> Optional[LlmAgent]:
        if self.cfg.max_loops <= 1:
            return None
        from .notion_retrieve_llm_build import build_notion_plan_eval_agent
        return build_notion_plan_eval_agent(self.cfg.source_top_k)

    async def execute_sub_query(
        self, sub_query: str, target_focus: str,
    ) -> List[ChunkEntry]:
        """走 notion_retrieve（目前是 stub，返回空列表）。"""
        uc_list = await notion_retrieve(query=sub_query)
        if not uc_list:
            return []

        uc_list = sorted(uc_list, key=lambda x: x.score or 0.0, reverse=True)
        uc_list = uc_list[: self.cfg.sub_query_top_n]
        return [_unified_to_chunk_entry(uc, sub_query, target_focus) for uc in uc_list]
