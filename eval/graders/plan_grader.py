"""Plan Grader — LLM-based evaluation of QueryPlan quality.

Dimensions (each 1-5):
  1. focus_completeness  — do focuses cover all aspects of the query?
  2. focus_granularity   — is each focus specific enough to be evaluable?
  3. sub_query_quality   — are feishu keywords short/precise? bailian semantic?
  4. tag_accuracy        — are bailian l2 tags and time tags correct?
  5. query_type_accuracy — does classification match ground truth?
  6. entity_handling     — (if multi-entity) correct group splitting?
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from tools.core.llm_client import call_llm, parse_json_response
from eval.config import GRADER_MODEL

logger = logging.getLogger(__name__)

_PLAN_GRADE_PROMPT = """\
你是一个检索系统的 Query Plan 质量评审专家。

## 任务
评估下方的 QueryPlan 是否为给定 query 生成了高质量的检索计划。

## 原始 Query
"{query}"

## 标注信息
- 预期 query_type: {query_type_expected}
- 预期覆盖主题: {expected_focus_themes}
- 核心实体: {expected_entities}
- 难度: {difficulty}

## 生成的 QueryPlan
```json
{plan_json}
```

## 评分维度（每维 1-5 分）
1. **focus_completeness**: focuses 是否覆盖了 query 的所有需求方面？有无遗漏？
   - 5=完整覆盖, 3=覆盖主要方面但有遗漏, 1=严重遗漏
2. **focus_granularity**: 每个 focus 是否具体到可评估？
   - 5=每个 focus 精准可评估, 3=部分过粗, 1=大多过粗如"获取数据"
3. **sub_query_quality**: feishu query 是否简短精准（2-3词）？bailian 是否语义丰富？同一 focus 的不同源 query 是否互补？
   - 5=双源 query 质量均高, 3=一个源的 query 有问题, 1=query 质量差
4. **tag_accuracy**: bailian tags（l2分类 + 时效）选择是否准确？
   - 5=tag 精准, 3=基本对但有冗余/遗漏, 1=tag 错误
5. **query_type_accuracy**: query_type 分类是否与标注一致？
   - 5=完全一致, 3=可争议但合理, 1=明显错误
6. **entity_handling**: 多实体场景下 focus_groups 拆分是否正确？维度是否对称？
   - 5=拆分合理且对称, 3=拆了但不完整, 1=未拆分或错误拆分
   - 单实体 query 默认 5 分

## 输出格式（严格 JSON）
{{
  "scores": {{
    "focus_completeness": <1-5>,
    "focus_granularity": <1-5>,
    "sub_query_quality": <1-5>,
    "tag_accuracy": <1-5>,
    "query_type_accuracy": <1-5>,
    "entity_handling": <1-5>
  }},
  "evidence": {{
    "focus_completeness": "<具体说明覆盖/遗漏了什么>",
    "focus_granularity": "<引用 focus 文本说明粒度>",
    "sub_query_quality": "<引用 sub_query 文本，分析 feishu/bailian 质量>",
    "tag_accuracy": "<说明 tag 选择理由>",
    "query_type_accuracy": "<说明分类对错>",
    "entity_handling": "<说明拆分情况>"
  }},
  "improvement_suggestions": ["<建议1>", "<建议2>"],
  "composite_score": <1-5 float, 六维加权平均>
}}

Return JSON only, no markdown wrapping."""


def grade_plan(trace: Dict[str, Any], timeout: float = 60.0) -> Dict[str, Any]:
    """Grade a single query's plan from its trace.

    Args:
        trace: single-query trace from runner (contains 'plan' and 'annotations')

    Returns:
        PlanGrade dict with scores, evidence, suggestions, composite_score
    """
    plan = trace.get("plan", {})
    ann = trace.get("annotations", {})

    prompt = _PLAN_GRADE_PROMPT.format(
        query=trace.get("query", ""),
        query_type_expected=ann.get("query_type_expected", "unknown"),
        expected_focus_themes=json.dumps(ann.get("expected_focus_themes", []), ensure_ascii=False),
        expected_entities=json.dumps(ann.get("expected_entities", []), ensure_ascii=False),
        difficulty=ann.get("difficulty", "unknown"),
        plan_json=json.dumps(plan, indent=2, ensure_ascii=False),
    )

    try:
        resp = call_llm(
            prompt,
            max_tokens=2048,
            timeout=timeout,
            _caller_tag="plan_grader",
            model=GRADER_MODEL,
        )
        result = parse_json_response(resp)
    except Exception as e:
        logger.error("plan_grader failed for query_id=%s: %s", trace.get("query_id"), e)
        return {"error": str(e), "query_id": trace.get("query_id")}

    result["query_id"] = trace.get("query_id")
    # Compute composite if not provided by LLM
    if "composite_score" not in result:
        scores = result.get("scores", {})
        vals = [v for v in scores.values() if isinstance(v, (int, float))]
        result["composite_score"] = round(sum(vals) / max(1, len(vals)), 2)

    return result


def grade_plans_batch(traces: List[Dict[str, Any]], timeout: float = 60.0) -> List[Dict[str, Any]]:
    """Grade plans for multiple queries sequentially."""
    return [grade_plan(t, timeout=timeout) for t in traces if "plan" in t]
