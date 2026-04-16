"""Sub-Query Efficiency Grader — Analyze sub_query recall effectiveness.

Categorizes sub_query results into:
1. EFFECTIVE: High yield (>= threshold chunks)
2. LOW_YIELD: Returns chunks but below threshold
3. ZERO_RETURN: No chunks returned
   - SOURCE_GAP: Source likely doesn't have relevant content
   - QUERY_ISSUE: Query formulation problem (can be optimized)

Usage:
    from eval.graders.sq_efficiency_grader import grade_sq_efficiency
    report = grade_sq_efficiency(trace)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─── Configuration ───────────────────────────────────────────────────────────

# Thresholds for classification
EFFECTIVE_THRESHOLD = 5  # >= 5 chunks = effective
LOW_YIELD_THRESHOLD = 1  # 1-4 chunks = low yield, 0 = zero return

# Keywords that suggest source might not have data (vs query formulation issue)
# These patterns suggest the query is asking for niche/specific content
SOURCE_GAP_INDICATORS = {
    "feishu": [
        "客户画像", "复购率", "用户画像", "客户分析",  # Advanced analytics
        "品牌故事", "核心卖点", "差异化", "定位",  # Brand strategy
        "竞品", "对手", "竞争",  # Competitor analysis
        "2025", "去年",  # Historical data (may not exist)
    ],
    "bailian": [
        # Bailian is expected to have broad coverage, rarely source gap
    ],
}

# Query patterns that suggest formulation issues (can be fixed)
QUERY_ISSUE_PATTERNS = [
    # English abbreviations in feishu queries
    ("feishu", ["UK", "US", "EU", "MX", "SG", "KR", "JP", "DE", "FR"]),
    # Too short queries (< 3 chars effective content)
    ("*", []),  # Handled separately
]


@dataclass
class SQResult:
    """Single sub_query result with classification."""
    sq_query: str
    target: str
    fg_idx: int
    sq_idx: int
    tags: List[str]
    chunk_count: int
    classification: str  # "EFFECTIVE", "LOW_YIELD", "ZERO_RETURN"
    zero_reason: Optional[str] = None  # "SOURCE_GAP" or "QUERY_ISSUE" if ZERO_RETURN


@dataclass
class SQEfficiencyReport:
    """Aggregated sub_query efficiency report."""
    query_id: str
    query: str
    total_sq: int
    effective_count: int
    low_yield_count: int
    zero_return_count: int
    
    # Breakdown of zero returns
    source_gap_count: int
    query_issue_count: int
    
    # Per-source stats
    source_stats: Dict[str, Dict[str, int]] = field(default_factory=dict)
    
    # Individual results
    sq_results: List[SQResult] = field(default_factory=list)
    
    # Recommendations
    recommendations: List[str] = field(default_factory=list)
    
    @property
    def efficiency_rate(self) -> float:
        """Rate of effective sub_queries."""
        return self.effective_count / max(1, self.total_sq)
    
    @property
    def zero_rate(self) -> float:
        """Rate of zero-return sub_queries."""
        return self.zero_return_count / max(1, self.total_sq)
    
    @property
    def actionable_zero_rate(self) -> float:
        """Rate of zero-returns that can be fixed (query_issue)."""
        return self.query_issue_count / max(1, self.total_sq)


def _classify_zero_return(sq_query: str, target: str) -> str:
    """Classify a zero-return sub_query as SOURCE_GAP or QUERY_ISSUE."""
    # Check for source gap indicators
    indicators = SOURCE_GAP_INDICATORS.get(target, [])
    for indicator in indicators:
        if indicator in sq_query:
            return "SOURCE_GAP"
    
    # Check for query formulation issues
    for pattern_target, patterns in QUERY_ISSUE_PATTERNS:
        if pattern_target == "*" or pattern_target == target:
            for pattern in patterns:
                if pattern in sq_query:
                    return "QUERY_ISSUE"
    
    # Check for too short query (heuristic: less than 4 meaningful chars)
    effective_chars = sum(1 for c in sq_query if c.isalnum())
    if effective_chars < 8:
        return "QUERY_ISSUE"
    
    # Default: assume source gap for feishu, query issue for bailian
    if target == "feishu":
        return "SOURCE_GAP"  # Feishu has narrower coverage
    return "QUERY_ISSUE"  # Bailian should have most content


def grade_sq_efficiency(trace: Dict[str, Any]) -> SQEfficiencyReport:
    """Grade sub_query efficiency from a trace.
    
    Args:
        trace: Full trace dict with retrieval.sq_results
        
    Returns:
        SQEfficiencyReport with classification and recommendations
    """
    query_id = str(trace.get("query_id", ""))
    query = trace.get("query", "")
    sq_results_raw = trace.get("retrieval", {}).get("sq_results", [])
    
    if not sq_results_raw:
        # Old trace without sq_results - return empty report
        return SQEfficiencyReport(
            query_id=query_id,
            query=query,
            total_sq=0,
            effective_count=0,
            low_yield_count=0,
            zero_return_count=0,
            source_gap_count=0,
            query_issue_count=0,
            recommendations=["Trace missing sq_results - re-run with updated eval runner"],
        )
    
    # Classify each sub_query
    sq_results: List[SQResult] = []
    source_stats: Dict[str, Dict[str, int]] = {}
    
    effective_count = 0
    low_yield_count = 0
    zero_return_count = 0
    source_gap_count = 0
    query_issue_count = 0
    
    for sq_raw in sq_results_raw:
        chunk_count = sq_raw.get("chunk_count", 0)
        target = sq_raw.get("target", "unknown")
        sq_query = sq_raw.get("sq_query", "")
        
        # Classify
        if chunk_count >= EFFECTIVE_THRESHOLD:
            classification = "EFFECTIVE"
            effective_count += 1
        elif chunk_count >= LOW_YIELD_THRESHOLD:
            classification = "LOW_YIELD"
            low_yield_count += 1
        else:
            classification = "ZERO_RETURN"
            zero_return_count += 1
        
        # Determine zero return reason
        zero_reason = None
        if classification == "ZERO_RETURN":
            zero_reason = _classify_zero_return(sq_query, target)
            if zero_reason == "SOURCE_GAP":
                source_gap_count += 1
            else:
                query_issue_count += 1
        
        sq_result = SQResult(
            sq_query=sq_query,
            target=target,
            fg_idx=sq_raw.get("fg_idx", 0),
            sq_idx=sq_raw.get("sq_idx", 0),
            tags=sq_raw.get("tags", []),
            chunk_count=chunk_count,
            classification=classification,
            zero_reason=zero_reason,
        )
        sq_results.append(sq_result)
        
        # Update source stats
        if target not in source_stats:
            source_stats[target] = {"effective": 0, "low_yield": 0, "zero_return": 0}
        source_stats[target][classification.lower()] = source_stats[target].get(classification.lower(), 0) + 1
    
    # Generate recommendations
    recommendations = []
    
    if query_issue_count > 0:
        recommendations.append(
            f"🔧 {query_issue_count} sub_queries have formulation issues (QUERY_ISSUE) - consider prompt tuning"
        )
    
    # Check feishu-specific issues
    feishu_stats = source_stats.get("feishu", {})
    if feishu_stats.get("zero_return", 0) > 0:
        feishu_zero_sqs = [sq for sq in sq_results if sq.target == "feishu" and sq.classification == "ZERO_RETURN"]
        if any(sq.zero_reason == "QUERY_ISSUE" for sq in feishu_zero_sqs):
            recommendations.append(
                "🔍 Feishu queries may need keyword expansion (check country names, abbreviations)"
            )
    
    # Check bailian efficiency
    bailian_stats = source_stats.get("bailian", {})
    if bailian_stats.get("effective", 0) == 0 and bailian_stats.get("low_yield", 0) > 0:
        recommendations.append(
            "📊 Bailian returning low chunks - consider tag refinement or query restructuring"
        )
    
    if not recommendations:
        recommendations.append("✅ Sub_query efficiency looks good")
    
    return SQEfficiencyReport(
        query_id=query_id,
        query=query,
        total_sq=len(sq_results),
        effective_count=effective_count,
        low_yield_count=low_yield_count,
        zero_return_count=zero_return_count,
        source_gap_count=source_gap_count,
        query_issue_count=query_issue_count,
        source_stats=source_stats,
        sq_results=sq_results,
        recommendations=recommendations,
    )


def grade_batch(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Grade multiple traces and produce aggregate stats.
    
    Returns:
        {
            "total_queries": N,
            "total_sq": M,
            "aggregate": {
                "efficiency_rate": float,
                "zero_rate": float,
                "actionable_zero_rate": float,
            },
            "per_source": {
                "feishu": {"effective": N, "zero_return": M, ...},
                "bailian": {...},
            },
            "query_reports": [SQEfficiencyReport, ...],
        }
    """
    reports = [grade_sq_efficiency(trace) for trace in traces]
    
    total_sq = sum(r.total_sq for r in reports)
    total_effective = sum(r.effective_count for r in reports)
    total_zero = sum(r.zero_return_count for r in reports)
    total_query_issue = sum(r.query_issue_count for r in reports)
    
    # Aggregate per-source stats
    per_source: Dict[str, Dict[str, int]] = {}
    for r in reports:
        for src, stats in r.source_stats.items():
            if src not in per_source:
                per_source[src] = {"effective": 0, "low_yield": 0, "zero_return": 0}
            for k, v in stats.items():
                per_source[src][k] = per_source[src].get(k, 0) + v
    
    return {
        "total_queries": len(reports),
        "total_sq": total_sq,
        "aggregate": {
            "efficiency_rate": round(total_effective / max(1, total_sq), 3),
            "zero_rate": round(total_zero / max(1, total_sq), 3),
            "actionable_zero_rate": round(total_query_issue / max(1, total_sq), 3),
        },
        "per_source": per_source,
        "query_reports": reports,
    }
