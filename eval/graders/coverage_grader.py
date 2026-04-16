"""Coverage Grader — LLM-based holistic assessment of retrieval sufficiency.

Answers the ultimate question: "Can AnswerAgent produce a complete answer
from these chunks?"

Dimensions:
  1. information_sufficiency (1-5) — overall completeness
  2. per_focus_coverage — covered / partial / missing per focus
  3. gap_analysis — what's missing and why (plan / source / reranker blame)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from tools.llm_client import call_llm, parse_json_response
from eval.config import GRADER_MODEL

logger = logging.getLogger(__name__)

_COVERAGE_PROMPT = """\
你是一个 RAG 检索质量评审专家，负责评估检索结果的信息完整性。

## 任务
假设一个 AnswerAgent 将基于下方的 chunks 来回答用户的原始 query。
评估这些 chunks 是否提供了足够的信息来生成完整、准确的回答。

## 原始 Query
"{query}"

## Focus 列表（检索意图维度）
{focuses_json}

## 检索返回的 Chunks（已 rerank，按相关性排序）
{chunks_text}

## 背景信息
- Query 类型: {query_type}
- 检索源统计: {source_stats}
- Focus 命中统计: {focus_hits}

## 评估维度

### 1. information_sufficiency (1-5)
- 5=chunks 完全足够，AnswerAgent 可生成全面回答
- 4=大部分足够，可能有个别细节缺失
- 3=基本够用，但有明显信息缺口
- 2=不足，只能回答部分方面
- 1=严重不足，几乎无法回答

### 2. per_focus_coverage
对每个 focus 标注: "covered"(有充分信息) / "partial"(有但不完整) / "missing"(无相关信息)

### 3. gap_analysis
对每个 missing 或 partial 的 focus，分析原因:
- "plan_issue": 查询计划没有为该 focus 生成合适的 sub_query
- "source_issue": 数据源可能不包含该信息
- "reranker_issue": 信息可能被检索到但在 rerank 阶段被过滤
- "coverage_gap": 信息确实不在知识库中

## 输出格式（严格 JSON）
{{
  "information_sufficiency": <1-5>,
  "sufficiency_evidence": "<说明为什么给这个分数，引用具体 chunk 内容>",
  "per_focus_coverage": {{
    "<focus_text>": {{
      "status": "covered" | "partial" | "missing",
      "evidence": "<说明依据>"
    }},
    ...
  }},
  "gap_analysis": [
    {{
      "focus": "<受影响的 focus>",
      "gap_type": "plan_issue" | "source_issue" | "reranker_issue" | "coverage_gap",
      "description": "<具体说明缺什么、为什么缺>"
    }}
  ],
  "coverage_rate": <0.0-1.0, covered占比>,
  "answer_feasibility": "<complete|partial|insufficient>"
}}

Return JSON only."""


def grade_coverage(trace: Dict[str, Any], timeout: float = 60.0) -> Dict[str, Any]:
    """Assess overall retrieval coverage for a single query.

    Args:
        trace: single-query trace from runner

    Returns:
        CoverageGrade dict
    """
    plan = trace.get("plan", {})
    focuses = [f for fg in plan.get("focus_groups", []) for f in fg.get("focuses", [])]
    chunks = trace.get("final_chunks", [])
    retrieval = trace.get("retrieval", {})

    if not focuses:
        return {
            "query_id": trace.get("query_id"),
            "warning": "no focuses in plan",
            "information_sufficiency": 0,
        }

    # Build chunk text summary for LLM (limit tokens)
    chunk_lines = []
    for i, c in enumerate(chunks[:12]):
        src = c.get("source_tool", "?")
        text = c.get("text", "")[:200].replace("\n", " ")
        heading = c.get("heading_path", "")
        chunk_lines.append(f"[{i}] ({src}) {heading}\n{text}")
    chunks_text = "\n---\n".join(chunk_lines) if chunk_lines else "(无 chunks)"

    prompt = _COVERAGE_PROMPT.format(
        query=trace.get("query", ""),
        focuses_json=json.dumps(focuses, ensure_ascii=False, indent=2),
        chunks_text=chunks_text,
        query_type=plan.get("query_type", "unknown"),
        source_stats=json.dumps(retrieval.get("source_stats", {})),
        focus_hits=json.dumps(retrieval.get("focus_hits", {})),
    )

    try:
        resp = call_llm(
            prompt,
            max_tokens=2048,
            timeout=timeout,
            _caller_tag="coverage_grader",
            model=GRADER_MODEL,
        )
        result = parse_json_response(resp)
    except Exception as e:
        logger.error("coverage_grader failed for query_id=%s: %s", trace.get("query_id"), e)
        return {"query_id": trace.get("query_id"), "error": str(e)}

    result["query_id"] = trace.get("query_id")
    return result


def grade_coverage_batch(
    traces: List[Dict[str, Any]], timeout: float = 60.0
) -> List[Dict[str, Any]]:
    """Grade coverage for multiple queries sequentially."""
    return [grade_coverage(t, timeout=timeout) for t in traces if "plan" in t]
