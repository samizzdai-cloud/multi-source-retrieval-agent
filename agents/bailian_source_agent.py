"""BailianSourceAgent (v2)——百炼源适配器，恢复 plan_only LLM。

v2.1 架构（2026-04-21）：
- Global Planner 只出扁平 ``focuses[{text, targets}]``，不再下发 tags/indexes/time
- BailianSourceAgent 的 plan_only LLM 基于 focus.text + 注入的专业规则自主产出：
    · query（百炼风格：完整语义化句子，含国家三元组展开）
    · tags（L2 ≤1 + 时效 ≤1，硬约束兜底 ``enforce_tag_caps``）
    · indexes（∩ allowed_indexes 白名单）
- Tool 层 (``bailian_retrieve_unified``) 继续承担 3 层容错：
    1) tag 分组语义（同 dimension OR + 跨 dimension AND，见 ``_build_search_filters``）
    2) 空结果自动去 tag 回退（``needs_tag_fallback``）
    3) 有时效 tag 时并行补召 ``last_long`` 长期文档
  SourceAgent 只管「选好参数」，回退/并行/去重全交给 tool 层。
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import List, Optional

from tools.bailian.bailian_retrieve import bailian_retrieve_unified
from tools.core.models import ChunkEntry, SourceAgentConfig, UnifiedChunk
from tools.core.source_profiles import get_profile
from tools.core.source_query_config import (
    BAILIAN_L2_TAGS,
    COUNTRY_ABBREV_RULE,
    SOURCE_QUERY_RULES,
    enforce_tag_caps,
)

from agents.source_agent_base import Focus, SourceAgent, SubQuery
from agents.sub_agents import (
    build_eval_only_agent,
    build_plan_eval_agent,
    build_plan_only_agent,
)


logger = logging.getLogger(__name__)


def _unified_to_chunk_entry(
    uc: UnifiedChunk, sub_query: str, target_focus: str,
) -> ChunkEntry:
    """UnifiedChunk → ChunkEntry（稳定主键 ``bailian:{index}:{doc}:{node}``）。"""
    meta = uc.metadata or {}
    index_name = meta.get("index_name", "default")
    doc_id = meta.get("doc_id") or meta.get("doc_name") or uc.source_name or "unknown"
    node_id = (
        meta.get("node_id")
        or meta.get("chunk_id")
        or hashlib.md5((uc.text or "").encode("utf-8")).hexdigest()[:10]
    )
    cid = f"bailian:{index_name}:{doc_id}:{node_id}"

    merged_meta = {
        **meta,
        "source_name": uc.source_name,
        "source_url": uc.source_url,
        "heading_path": uc.heading_path,
        "source_tool": uc.source_tool,
    }

    return ChunkEntry(
        id=cid,
        source="bailian",
        content=uc.text or "",
        metadata=merged_meta,
        score=uc.score,
        sub_query=sub_query,
        target_focuses={target_focus},
        tier=None,
    )


class BailianSourceAgent(SourceAgent):
    """百炼 SourceAgent（v2：LLM plan_only 自主产 tags/indexes）。"""

    source = "bailian"

    def __init__(
        self,
        cfg: Optional[SourceAgentConfig] = None,
        allowed_indexes: Optional[List[str]] = None,
    ):
        """构造 BailianSourceAgent。

        Args:
            cfg: SourceAgentConfig
            allowed_indexes: 用户允许检索的 bailian index 白名单（如 ["nubia", "hypershell"]）。
                空/None → 查所有已配置 index。非空 → plan_only LLM 必须从该集合选 indexes，
                execute 时再做一次 ∩ 兜底。
        """
        cfg = cfg or SourceAgentConfig()
        profile = get_profile("bailian")
        super().__init__(
            profile=profile,
            cfg=cfg,
            plan_only_agent=build_plan_only_agent(name="bailian_plan_only"),
            eval_only_agent=build_eval_only_agent(
                source_top_k=cfg.source_top_k, name="bailian_eval_only",
            ),
            plan_eval_agent=build_plan_eval_agent(
                source_top_k=cfg.source_top_k, name="bailian_plan_eval",
            ) if cfg.max_loops > 1 else None,
        )
        self._allowed_indexes = list(allowed_indexes or [])

    # ── 注入百炼专业规则到 plan_only prompt ────────────────────
    def _source_query_rules_block(self) -> str:
        allowed_indexes_txt = (
            json.dumps(self._allowed_indexes, ensure_ascii=False)
            if self._allowed_indexes else "[]（未限制）"
        )
        l2_enum = ", ".join(BAILIAN_L2_TAGS)
        parts = [
            SOURCE_QUERY_RULES["bailian"],
            "",
            COUNTRY_ABBREV_RULE,
            "",
            f"百炼可用 L2 业务分类枚举：{l2_enum}",
            f"允许的 indexes（必须从中选，不能为空/非法；单个 sub_query 通常只指定 1 个实体）：{allowed_indexes_txt}",
            "",
            "约束回顾（硬要求）：",
            "  - 每个 sub_query 的 tags 至多包含 1 个 L2 + 1 个时效（两者可选），总数 ≤ 2",
            "  - indexes 从 allowed_indexes 中选；找不到对应实体时可留空，不要瞎填",
            "  - time_filter 始终留空（time 信息走 tags 里的时效 tag）",
        ]
        return "\n".join(parts)

    # ── v2 执行入口：消费 SubQuery.tags / .indexes ──────────────
    async def execute_sub_query_full(self, sq: SubQuery) -> List[ChunkEntry]:
        # tags 兜底硬约束（L2 ≤1 + 时效 ≤1），防 LLM 越权
        tags = enforce_tag_caps(sq.tags)

        # indexes ∩ allowed_indexes 兜底
        if self._allowed_indexes:
            allowed_lower = {i.lower(): i for i in self._allowed_indexes}
            indexes = [allowed_lower[i.lower()] for i in sq.indexes if i.lower() in allowed_lower]
            if not indexes:
                # LLM 没给出有效 indexes → 退回 allowed_indexes 全量（工具层按全部配置 index 查）
                indexes = list(self._allowed_indexes)
        else:
            indexes = list(sq.indexes) if sq.indexes else None

        logger.info(
            "[bailian] focus=%s tags=%s indexes=%s query=%r",
            sq.target_focus, tags, indexes, sq.query,
        )

        uc_list = await bailian_retrieve_unified(
            query=sq.query,
            tags=tags or None,
            indexes=indexes if indexes else None,
            rerank_top_n_override=self.cfg.sub_query_top_n,
        )
        if not uc_list:
            return []

        uc_list = sorted(uc_list, key=lambda x: x.score, reverse=True)[: self.cfg.sub_query_top_n]
        return [_unified_to_chunk_entry(uc, sq.query, sq.target_focus) for uc in uc_list]
