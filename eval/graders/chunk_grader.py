"""Chunk Grader — LLM-based per-chunk per-focus relevance scoring.

LLM-judged 0-3 relevance scores per (chunk, focus) pair (替代早期启发式
_is_relevant 启动器)。

Derived metrics (computed from scores, not by LLM):
  - nDCG@K: normalized discounted cumulative gain
  - precision@K: fraction of relevant chunks in top-K
  - per_focus_recall: ≥1 chunk with score≥2 per focus?
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Dict, List, Optional

from tools.llm_client import call_llm, parse_json_response
from eval.config import GRADER_MODEL

logger = logging.getLogger(__name__)

_CHUNK_GRADE_PROMPT = """\
你是一个 RAG 检索结果质量评估专家。

## 任务
对下方每个检索结果 chunk，逐个评估它与每个 focus（检索意图维度）的相关性。

## 原始 Query
"{query}"

## Focus 列表
{focuses_json}

## Chunks（已按 rerank 排序）
{chunks_json}

## 评分标准（每个 chunk × 每个 focus）
- 0 = 完全不相关（与该 focus 无关）
- 1 = 边缘相关（提到了相关实体/主题，但没有实质信息）
- 2 = 相关（包含对该 focus 有用的信息，可参考但不完整）
- 3 = 高度相关（直接且较完整地回答了该 focus 的需求）

## 输出格式（严格 JSON）
{{
  "grades": [
    {{
      "chunk_idx": 0,
      "focus_scores": {{
        "<focus_text>": {{"score": <0-3>, "reason": "<3-5字关键词>"}},
        ...
      }},
      "max_score": <该chunk在所有focus中的最高分>
    }},
    ...
  ]
}}

每个 chunk 必须对每个 focus 都给出评分。Return JSON only."""


def grade_chunks(trace: Dict[str, Any], timeout: float = 90.0) -> Dict[str, Any]:
    """Grade chunks for a single query from its trace.

    Returns:
        {
            "query_id": ...,
            "grades": [...],          # per-chunk per-focus scores
            "metrics": {              # derived metrics
                "ndcg_at_5": float,
                "ndcg_at_10": float,
                "precision_at_5": float,
                "precision_at_10": float,
                "per_focus_recall": {focus: bool},
                "mean_max_score": float,
            }
        }
    """
    plan = trace.get("plan", {})
    focuses = [f for fg in plan.get("focus_groups", []) for f in fg.get("focuses", [])]
    chunks = trace.get("final_chunks", [])

    if not focuses or not chunks:
        return {
            "query_id": trace.get("query_id"),
            "grades": [],
            "metrics": {},
            "warning": "no focuses or chunks",
        }

    # Prepare chunk text for LLM (limit to top 15 to control token usage)
    max_chunks = 15
    chunks_for_eval = chunks[:max_chunks]
    chunks_display = [
        {"idx": c["idx"], "text": c["text"][:250], "source": c.get("source_tool", ""),
         "heading": c.get("heading_path", "")}
        for c in chunks_for_eval
    ]

    prompt = _CHUNK_GRADE_PROMPT.format(
        query=trace.get("query", ""),
        focuses_json=json.dumps(focuses, ensure_ascii=False, indent=2),
        chunks_json=json.dumps(chunks_display, ensure_ascii=False, indent=2),
    )

    try:
        resp = call_llm(
            prompt,
            max_tokens=8192,
            timeout=timeout,
            _caller_tag="chunk_grader",
            model=GRADER_MODEL,
        )
        result = parse_json_response(resp)
    except Exception as e:
        logger.error("chunk_grader failed for query_id=%s: %s", trace.get("query_id"), e)
        return {"query_id": trace.get("query_id"), "error": str(e)}

    grades = result.get("grades", [])

    # ── Compute derived metrics ──────────────────────────────────────────
    metrics = _compute_metrics(grades, focuses, len(chunks_for_eval))

    return {
        "query_id": trace.get("query_id"),
        "grades": grades,
        "metrics": metrics,
    }


def _compute_metrics(
    grades: List[Dict[str, Any]],
    focuses: List[str],
    total_chunks: int,
) -> Dict[str, Any]:
    """Compute nDCG, precision, per-focus recall from LLM grades."""
    # Extract max score per chunk (highest across all focuses)
    max_scores: List[float] = []
    for g in grades:
        focus_scores = g.get("focus_scores", {})
        scores = [v.get("score", 0) if isinstance(v, dict) else 0
                  for v in focus_scores.values()]
        max_scores.append(max(scores) if scores else 0)

    # nDCG@K
    def _dcg(scores: List[float], k: int) -> float:
        return sum(s / math.log2(i + 2) for i, s in enumerate(scores[:k]))

    def _ndcg(scores: List[float], k: int) -> float:
        dcg = _dcg(scores, k)
        ideal = _dcg(sorted(scores, reverse=True), k)
        return dcg / ideal if ideal > 0 else 0.0

    # precision@K (score >= 2 counts as relevant)
    def _precision(scores: List[float], k: int) -> float:
        relevant = sum(1 for s in scores[:k] if s >= 2)
        return relevant / min(k, len(scores)) if scores else 0.0

    # per-focus recall: does each focus have ≥1 chunk with score ≥ 2?
    focus_recall = {}
    for f in focuses:
        has_relevant = False
        for g in grades:
            fs = g.get("focus_scores", {})
            entry = fs.get(f, {})
            score = entry.get("score", 0) if isinstance(entry, dict) else 0
            if score >= 2:
                has_relevant = True
                break
        focus_recall[f] = has_relevant

    return {
        "ndcg_at_5": round(_ndcg(max_scores, 5), 4),
        "ndcg_at_10": round(_ndcg(max_scores, 10), 4),
        "precision_at_5": round(_precision(max_scores, 5), 4),
        "precision_at_10": round(_precision(max_scores, 10), 4),
        "per_focus_recall": focus_recall,
        "focus_recall_rate": round(
            sum(focus_recall.values()) / max(1, len(focus_recall)), 3
        ),
        "mean_max_score": round(
            sum(max_scores) / max(1, len(max_scores)), 2
        ),
    }


def grade_chunks_batch(
    traces: List[Dict[str, Any]], timeout: float = 90.0
) -> List[Dict[str, Any]]:
    """Grade chunks for multiple queries sequentially."""
    return [grade_chunks(t, timeout=timeout) for t in traces if "final_chunks" in t]
