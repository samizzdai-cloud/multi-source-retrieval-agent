"""FeishuSourceAgent (v2)——飞书源适配器，恢复 plan_only LLM。

v2 架构（2026-04-20）：
- Global Planner 只出 ``focus_groups[{focuses, targets}]``
- FeishuSourceAgent 的 plan_only LLM 基于 focus.text + 注入的 feishu 专业规则自主产出：
    · query（feishu 风格：2-3 词 AND 精确匹配，中文国家名，5-15 字）
    · time_filter（"last_N_days" 相对时间 / null）
- Tool 层 (``feishu_retrieve``) 自带 zero-retry 截词容错。
"""

from __future__ import annotations

import hashlib
import logging
from typing import List, Optional

from tools.feishu_retrieve import feishu_retrieve
from tools.models import ChunkEntry, SourceAgentConfig, UnifiedChunk
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


def _strip_feishu_prefix(s: str) -> str:
    """移除字符串开头可能存在的 'feishu:' 前缀（单次 strip，非递归）。

    用于 ``_unified_to_chunk_entry`` 的 doc_id fallback 链——某些上游路径
    （table_pool cache / reader 二次包装）会把 ``feishu:`` 混进 metadata 的
    ``doc_token`` / ``doc_id`` 字段，若不剥离会拼出 ``feishu:feishu:...`` 这种
    双前缀 id，导致 eval_only LLM 回抄时命中不到 chunk_pool。
    """
    if not s:
        return s
    return s[len(_FEISHU_ID_PREFIX):] if s.startswith(_FEISHU_ID_PREFIX) else s


def _unified_to_chunk_entry(
    uc: UnifiedChunk, sub_query: str, target_focus: str,
) -> ChunkEntry:
    """UnifiedChunk → ChunkEntry。

    Feishu 不同 reader 产出的 metadata 字段名不完全统一，这里只挑最关键的几个：
    - doc 级：``doc_token`` / ``doc_id`` / ``source_url``
    - block 级：``block_id`` / ``chunk_id`` / ``row_id``（表格）
    - 兜底：对内容做 md5 取前 10 位

    所有 doc 级字段在拼接前都要 strip 掉潜在的 ``feishu:`` 前缀，保证
    ``cid = f"feishu:{doc_id}:{node_id}"`` 不会出现 ``feishu:feishu:...`` 双前缀
    （spec_plan §16-P1 / §17-S1.1）。
    """
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
        or hashlib.md5((uc.text or "").encode("utf-8")).hexdigest()[:10]
    )
    cid = f"feishu:{doc_id}:{node_id}"

    merged_meta = {
        **meta,
        "source_name": uc.source_name,
        "source_url": uc.source_url,
        "heading_path": uc.heading_path,
        "source_tool": uc.source_tool or "feishu",
    }

    return ChunkEntry(
        id=cid,
        source="feishu",
        content=uc.text or "",
        metadata=merged_meta,
        score=uc.score,
        sub_query=sub_query,
        target_focuses={target_focus},
        tier=None,
    )


class FeishuSourceAgent(SourceAgent):
    """飞书 SourceAgent（v2：LLM plan_only 自主产 query / time_filter）。"""

    source = "feishu"

    def __init__(
        self,
        cfg: Optional[SourceAgentConfig] = None,
        uat: str = "",
        query_type: str = "mixed",
        doc_pool: Optional[object] = None,
        table_pool: Optional[object] = None,
    ):
        cfg = cfg or SourceAgentConfig()
        profile = get_profile("feishu")
        super().__init__(
            profile=profile,
            cfg=cfg,
            plan_only_agent=build_plan_only_agent(name="feishu_plan_only"),
            eval_only_agent=build_eval_only_agent(
                source_top_k=cfg.source_top_k, name="feishu_eval_only",
            ),
            plan_eval_agent=build_plan_eval_agent(
                source_top_k=cfg.source_top_k, name="feishu_plan_eval",
            ) if cfg.max_loops > 1 else None,
        )
        self._uat = uat
        self._query_type = query_type
        self._doc_pool = doc_pool
        self._table_pool = table_pool

    # ── 注入 feishu 专业规则到 plan_only prompt ──────────────────
    def _source_query_rules_block(self) -> str:
        return (
            SOURCE_QUERY_RULES["feishu"]
            + "\n\n提醒：tags / indexes 必须留空 []；time_filter 仅相对时间用 "
            + "\"last_N_days\"，季度/月份等具体时间点请并入 query 本身或留 null。"
        )

    # ── v2 执行入口：消费 SubQuery.time_filter ──────────────────
    async def execute_sub_query_full(self, sq: SubQuery) -> List[ChunkEntry]:
        doc_pool_timeout = max(8.0, self.cfg.source_timeout_s * 0.8)

        logger.info(
            "[feishu] focus=%s time_filter=%s query=%r",
            sq.target_focus, sq.time_filter, sq.query,
        )

        uc_list = await feishu_retrieve(
            query=sq.query,
            time_filter=sq.time_filter,
            uat=self._uat,
            query_type=self._query_type,
            doc_pool=self._doc_pool,
            table_pool=self._table_pool,
            doc_pool_timeout=doc_pool_timeout,
        )
        if not uc_list:
            return []

        uc_list = sorted(uc_list, key=lambda x: x.score or 0.0, reverse=True)
        uc_list = uc_list[: self.cfg.sub_query_top_n]
        return [_unified_to_chunk_entry(uc, sq.query, sq.target_focus) for uc in uc_list]
