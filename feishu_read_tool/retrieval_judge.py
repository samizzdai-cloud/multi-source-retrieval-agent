# tools/feishu/retrieval_judge.py
# Stage 2: document filtering + chunk sufficiency

import json
import logging
from typing import List

from tools.llm_client import call_llm, parse_json_response
from .pipeline_models import (
    ChunkResult, SubQuery, OrchestratorConfig,
    JudgeAndFilterResult, ChunkSufficiencyResult,
)

logger = logging.getLogger(__name__)


# ── Legacy filter (migrated from llm_client.py, kept as reference) ────────

_LEGACY_FILTER_PROMPT_TEMPLATE = """You are a document relevance filter assistant.
User query: "{query}"

Candidate documents (title + summary excerpt):
{candidates_json}

Select the top {max_candidates} most relevant documents and return them sorted by relevance (most relevant first).
Return a JSON array only, no other text:
[{{"id": "...", "relevant": true, "reason": "one-line reason"}}]

Rules:
- Only include documents clearly relevant to the query
- Sort by relevance to the query, most relevant first
- Return at most {max_candidates} documents
- Omit documents that are clearly unrelated
"""


def _legacy_filter_candidates(
    query: str,
    candidates: List[dict],
    max_candidates: int = 10,
) -> List[str]:
    """Legacy Stage 2 filter. Superseded by judge_and_filter()."""
    all_ids = [c["id"] for c in candidates]
    if not candidates:
        return []
    try:
        candidates_json = json.dumps(
            [{"id": c["id"], "title": c.get("title", ""), "summary": c.get("summary", "")}
             for c in candidates],
            ensure_ascii=False,
        )
        prompt = _LEGACY_FILTER_PROMPT_TEMPLATE.format(
            query=query,
            candidates_json=candidates_json,
            max_candidates=max_candidates,
        )
        response_text = call_llm(prompt, max_tokens=8192, timeout=60.0, _caller_tag="filter")
        results = parse_json_response(response_text)
        kept = [item["id"] for item in results if item.get("relevant", True)]
        seen = set()
        final = []
        for doc_id in kept:
            if doc_id not in seen and doc_id in set(all_ids):
                seen.add(doc_id)
                final.append(doc_id)
                if len(final) >= max_candidates:
                    break
        return final if final else all_ids
    except Exception as e:
        logger.warning("Legacy filter failed, keeping all candidates: %s", e)
        return all_ids


# ── judge_and_filter ──────────────────────────────────────────────


def judge_and_filter(
    query: str,
    sub_queries: List[SubQuery],
    candidates: List[dict],
    cfg: OrchestratorConfig,
) -> JudgeAndFilterResult:
    """Evaluate candidate coverage and filter documents in a single LLM call.

    Merges the old Checkpoint A (coverage assessment) and Stage 2 (document filter)
    into one call, reducing latency and eliminating verdict contradictions.

    Args:
        query: Original user query.
        sub_queries: Current round's sub-queries.
        candidates: Raw search result dicts with id/title/summary.
        cfg: OrchestratorConfig.

    Returns:
        JudgeAndFilterResult with coverage assessment and filtered doc IDs.
    """
    # Rule pre-check: too few candidates → directly insufficient
    if len(candidates) < cfg.min_candidates_to_llm:
        return JudgeAndFilterResult(
            coverage_score=0.0,
            covered_aspects=[],
            missing_aspects=["候选文档不足，无法有效判断"],
            verdict="insufficient",
            gap_reason=f"仅找到 {len(candidates)} 篇候选文档，数量不足",
            filtered_doc_ids=[c.get("id", "") for c in candidates],
        )

    sub_queries_json = json.dumps(
        [{"query": sq.query, "focus": sq.focus} for sq in sub_queries],
        ensure_ascii=False,
    )
    candidates_json = json.dumps(
        [{"id": c.get("id", ""), "title": c.get("title", ""),
          "summary": c.get("summary", "")[:200]}
         for c in candidates],
        ensure_ascii=False,
    )
    max_candidates = cfg.pipeline_cfg.max_candidates

    prompt = f"""你是文档检索质量评估助手。
用户 query："{query}"

本次检索使用的子 query：
{sub_queries_json}

找到的候选文档列表（title + summary）：
{candidates_json}

任务（一次完成两件事）：
1. 评估这批候选文档是否能充分覆盖 query 的主要信息需求
2. 从候选文档中过滤出值得深度读取的文档

Return JSON only:
{{
  "coverage_score": 0.00-1.00,
  "covered_aspects": ["已覆盖的信息维度列表"],
  "missing_aspects": ["仍缺失的信息维度列表"],
  "verdict": "sufficient" | "insufficient",
  "gap_reason": "不足时的原因说明，充分时为空字符串",
  "filtered_doc_ids": ["doc_id_1", "doc_id_2"]
}}

评分标准：
- coverage_score >= 0.5 判为 sufficient
- filtered_doc_ids：只保留与 query 明确相关的文档，最多 {max_candidates} 篇
- verdict=insufficient 时 filtered_doc_ids 仍需填写（保留最相关的几篇，供强制读取时使用）"""

    # Valid candidate IDs for post-validation
    valid_ids = {c.get("id", "") for c in candidates}

    try:
        response_text = call_llm(prompt, max_tokens=2048, timeout=60.0, _caller_tag="judge_filter")
        result = parse_json_response(response_text)

        # Post-validate: only keep IDs that exist in original candidates
        raw_filtered = result.get("filtered_doc_ids", [])
        filtered_doc_ids = [fid for fid in raw_filtered if fid in valid_ids]

        return JudgeAndFilterResult(
            coverage_score=min(1.0, max(0.0, float(result.get("coverage_score", 0.5)))),
            covered_aspects=result.get("covered_aspects", []),
            missing_aspects=result.get("missing_aspects", []),
            verdict=result.get("verdict", "sufficient"),
            gap_reason=result.get("gap_reason", ""),
            filtered_doc_ids=filtered_doc_ids,
        )

    except Exception as e:
        logger.warning("judge_and_filter failed, using fallback (sufficient): %s", e)
        return JudgeAndFilterResult(
            coverage_score=0.5,
            covered_aspects=[],
            missing_aspects=[],
            verdict="sufficient",
            gap_reason="",
            filtered_doc_ids=[c.get("id", "") for c in candidates[:max_candidates]],
        )


# ── judge_chunk_sufficiency ───────────────────────────────────────


def judge_chunk_sufficiency(
    query: str,
    scored_chunks: List[ChunkResult],
    current_round: int,
    max_rounds: int,
    already_searched: List[str],
) -> ChunkSufficiencyResult:
    """Judge whether scored chunks sufficiently cover the query.

    Args:
        query: Original user query.
        scored_chunks: ChunkResult list from Stage 5 (sorted by score desc).
        current_round: Current round number (1-based).
        max_rounds: Maximum allowed rounds.
        already_searched: All previously searched query strings.

    Returns:
        ChunkSufficiencyResult with verdict and selected chunk IDs.
    """
    # Rule pre-check: last round → force sufficient
    if current_round >= max_rounds:
        return ChunkSufficiencyResult(
            verdict="sufficient",
            sufficiency_score=1.0,
            selected_chunk_ids=[c.chunk_id for c in scored_chunks],
            missing_aspects=[],
            expand_suggestion="",
        )

    chunks_json = json.dumps(
        [{"chunk_id": c.chunk_id, "doc_title": c.file_name,
          "heading_path": c.heading_path, "summary": c.summary,
          "score": round(c.relevance_score, 2)}
         for c in scored_chunks],
        ensure_ascii=False,
        indent=2,
    )

    prompt = f"""你是检索结果充分性评估助手。
用户 query："{query}"
当前轮次：第 {current_round} 轮（最多 {max_rounds} 轮）

当前获取的内容 chunks：
{chunks_json}

每个 chunk 格式：
{{"chunk_id": "...", "doc_title": "...", "heading_path": "...", "summary": "...", "score": 0.00}}

任务：
1. 判断这些 chunks 是否充分覆盖了 query 的主要信息需求
2. 从中选出最有价值的 chunk 子集（去掉冗余和低价值）
3. 若不充分，给出扩展建议

Return JSON only:
{{
  "sufficiency_score": 0.00-1.00,
  "verdict": "sufficient" | "insufficient",
  "selected_chunk_ids": ["chunk_id_1", "chunk_id_2"],
  "missing_aspects": ["缺失的信息维度"],
  "expand_suggestion": "建议的扩展搜索关键词（不充分时），充分时为空字符串"
}}

选择规则：
- sufficiency_score >= 0.6 判为 sufficient
- selected_chunk_ids 只包含真正有价值的 chunk（去掉重复内容和低于 0.5 分的）
- 保留顺序与原列表一致"""

    # Valid chunk IDs for post-validation
    valid_ids = {c.chunk_id for c in scored_chunks}

    try:
        response_text = call_llm(prompt, max_tokens=2048, timeout=30.0, _caller_tag="sufficiency")
        result = parse_json_response(response_text)

        # Post-validate: only keep IDs that exist in scored_chunks
        raw_selected = result.get("selected_chunk_ids", [])
        selected_ids = [cid for cid in raw_selected if cid in valid_ids]

        return ChunkSufficiencyResult(
            verdict=result.get("verdict", "sufficient"),
            sufficiency_score=min(1.0, max(0.0, float(result.get("sufficiency_score", 0.6)))),
            selected_chunk_ids=selected_ids,
            missing_aspects=result.get("missing_aspects", []),
            expand_suggestion=result.get("expand_suggestion", ""),
        )

    except Exception as e:
        logger.warning("judge_chunk_sufficiency failed, fallback to sufficient: %s", e)
        return ChunkSufficiencyResult(
            verdict="sufficient",
            sufficiency_score=0.6,
            selected_chunk_ids=[c.chunk_id for c in scored_chunks],
            missing_aspects=[],
            expand_suggestion="",
        )
