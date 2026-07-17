"""Experiment configuration — bundles all tuneable parameters for A/B testing.

ExperimentConfig wraps EvalConfig (early-stop / evidence / embedding) and
adds planner overrides plus metadata so each experiment run is
fully reproducible.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from tools.evaluator import EvalConfig

# Grader LLM — separate from the pipeline LLM (gemini-3-flash-preview)
GRADER_MODEL = "gemini-3.1-pro-preview"


@dataclass
class ExperimentConfig:
    """One complete, reproducible experiment configuration."""

    # ── identity ─────────────────────────────────────────────────
    name: str = "baseline"
    description: str = ""

    # ── pipeline params (wraps existing EvalConfig) ──────────────
    eval_config: EvalConfig = field(default_factory=EvalConfig)

    # ── planner overrides (passed through to plan_query) ─────────
    planner_overrides: Dict[str, Any] = field(default_factory=dict)
    #   e.g. {"max_sub_queries": 6}

    # ── retrieval overrides (passed through to source retrievers) ─
    retrieval_overrides: Dict[str, Any] = field(default_factory=dict)
    #   feishu doc_pool:
    #     feishu_top_n_docs: int           — PoolConfig.top_n_docs
    #     feishu_max_per_doc: int          — PoolConfig.max_per_doc
    #     feishu_max_output: int           — PoolConfig.max_output
    #     feishu_search_page_size: int     — search count param
    #   feishu table_pool:
    #     feishu_table_budget: int         — max sheets/bitables per focus

    # ── grader settings ──────────────────────────────────────────
    grader_model: str = GRADER_MODEL
    grade_timeout: float = 60.0

    # ── runner settings ──────────────────────────────────────────
    repeat: int = 1          # times to repeat each query for timing stability
    available_sources: list = field(default_factory=lambda: ["feishu", "bailian"])

    # ── serialisation ────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # EvalConfig is nested dataclass — asdict handles it
        return d

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False))

    @classmethod
    def load(cls, path: Path) -> "ExperimentConfig":
        raw = json.loads(path.read_text())
        ec_raw = raw.pop("eval_config", {})
        eval_config = EvalConfig(**ec_raw)
        return cls(eval_config=eval_config, **raw)


# ── Pre-built configs ────────────────────────────────────────────

BASELINE = ExperimentConfig(
    name="baseline",
    description="Production defaults — current EvalConfig snapshot",
)

AGGRESSIVE_EARLY_STOP = ExperimentConfig(
    name="aggressive_early_stop",
    description="Lower early-stop thresholds for faster retrieval",
    eval_config=EvalConfig(
        min_good_chunks=3,
        hard_timeout_seconds=8.0,
        min_per_focus=1,
    ),
)

HIGH_RECALL = ExperimentConfig(
    name="high_recall",
    description="Raise limits for maximum recall",
    eval_config=EvalConfig(
        min_good_chunks=8,
        hard_timeout_seconds=18.0,
        top_k_chunks=15,
        min_per_focus_select=3,
    ),
)

# Phase 1: Wide Recall — maximize candidate pool for quality iteration
WIDE_RECALL = ExperimentConfig(
    name="wide_recall",
    description="Phase 1: maximize recall at every stage — disable early-stop, widen all bottlenecks",
    eval_config=EvalConfig(
        min_good_chunks=999,         # effectively disable early-stop
        hard_timeout_seconds=60.0,   # generous timeout, wait for everything
        min_sources=1,
        top_k_chunks=25,             # wider final output
        min_per_focus_select=4,      # more representatives per focus
        min_per_focus=1,
    ),
    retrieval_overrides={
        # feishu doc_pool — loosen bottlenecks B3+B4
        "feishu_top_n_docs": 10,
        "feishu_max_per_doc": 8,
        "feishu_max_output": 25,
        "feishu_search_page_size": 20,
        # feishu table_pool
        "feishu_table_budget": 5,
    },
)

# Phase 2: Speed Tuning — tighten params while monitoring quality delta
SPEED_TUNING = ExperimentConfig(
    name="speed_tuning",
    description="Phase 2: converge params for production speed while checking quality loss",
    eval_config=EvalConfig(
        min_good_chunks=5,
        hard_timeout_seconds=15.0,
        min_sources=1,
        top_k_chunks=12,
        min_per_focus_select=2,
    ),
    retrieval_overrides={
        "feishu_top_n_docs": 6,
        "feishu_max_per_doc": 5,
        "feishu_max_output": 15,
        "feishu_search_page_size": 20,
        "feishu_table_budget": 3,
    },
)

# Phase 2b: Balanced Coverage — reasonable recall boost without extreme timeout
BALANCED_COVERAGE = ExperimentConfig(
    name="balanced_coverage",
    description="Phase 2b: balanced recall — moderate early-stop relaxation, reasonable timeout",
    eval_config=EvalConfig(
        min_good_chunks=8,           # relaxed but not disabled
        hard_timeout_seconds=25.0,   # moderate timeout
        min_sources=1,
        top_k_chunks=18,             # larger output
        min_per_focus_select=3,      # more per focus
        min_per_focus=1,
    ),
    retrieval_overrides={
        "feishu_top_n_docs": 8,
        "feishu_max_per_doc": 6,
        "feishu_max_output": 20,
        "feishu_search_page_size": 20,
        "feishu_table_budget": 4,
    },
)

# ── Phase 2: Focus-driven early-stop configs ────────────────────

# P2 Conservative: 10s protection window, high score threshold
# Goal: validate focus-driven early-stop quality vs wide_recall
P2_CONSERVATIVE = ExperimentConfig(
    name="p2_conservative",
    description="P2.6: periodic early-stop, min_sources=2, min_elapsed=0, hard=25s",
    eval_config=EvalConfig(
        min_good_chunks=999,             # [P2] disabled — early-stop purely per-focus
        min_relevance_score=0.45,        # [P2] higher bar
        hard_timeout_seconds=25.0,       # moderate hard timeout
        min_sources=2,                   # [P2.6] 要求飞书+百炼都至少返回过结果才允许早停
        min_per_focus=2,                 # each focus needs ≥2 high-quality hits
        early_stop_min_elapsed_s=0.0,    # [P2.6] 不再用时间门槛，改用 min_sources=2 保护飞书
        top_k_chunks=25,
        min_per_focus_select=3,
    ),
    retrieval_overrides={
        # keep wide_recall retrieval params — only change early-stop behavior
        "feishu_top_n_docs": 10,
        "feishu_max_per_doc": 8,
        "feishu_max_output": 25,
        "feishu_search_page_size": 20,
        "feishu_table_budget": 5,
    },
)

# P2 Balanced: 8s protection window — faster, validate quality delta
P2_BALANCED = ExperimentConfig(
    name="p2_balanced",
    description="P2.3: aggressive early-stop, score≥0.45, min_elapsed=8s, hard=20s",
    eval_config=EvalConfig(
        min_good_chunks=999,             # [P2] disabled
        min_relevance_score=0.45,        # [P2] keep at 0.45
        hard_timeout_seconds=20.0,       # tighter hard timeout
        min_sources=1,
        min_per_focus=2,
        early_stop_min_elapsed_s=8.0,    # [P2] moderate protection window
        top_k_chunks=25,
        min_per_focus_select=3,
    ),
    retrieval_overrides={
        "feishu_top_n_docs": 10,
        "feishu_max_per_doc": 8,
        "feishu_max_output": 25,
        "feishu_search_page_size": 20,
        "feishu_table_budget": 5,
    },
)

# P3 Single-source: same as p2_conservative but min_sources=1
# Allows early-stop as soon as ANY one source satisfies all focuses
P3_SINGLE_SOURCE = ExperimentConfig(
    name="p3_single_source",
    description="P3: same as p2_conservative but min_sources=1 — early-stop on single source",
    eval_config=EvalConfig(
        min_good_chunks=999,
        min_relevance_score=0.45,
        hard_timeout_seconds=25.0,
        min_sources=1,                   # ← only difference: allow single-source early-stop
        min_per_focus=2,
        early_stop_min_elapsed_s=0.0,
        top_k_chunks=25,
        min_per_focus_select=3,
    ),
    retrieval_overrides={
        "feishu_top_n_docs": 10,
        "feishu_max_per_doc": 8,
        "feishu_max_output": 25,
        "feishu_search_page_size": 20,
        "feishu_table_budget": 5,
    },
)

PRESETS: Dict[str, ExperimentConfig] = {
    "baseline": BASELINE,
    "aggressive_early_stop": AGGRESSIVE_EARLY_STOP,
    "high_recall": HIGH_RECALL,
    "wide_recall": WIDE_RECALL,
    "speed_tuning": SPEED_TUNING,
    "balanced_coverage": BALANCED_COVERAGE,
    "p2_conservative": P2_CONSERVATIVE,
    "p2_balanced": P2_BALANCED,
    "p3_single_source": P3_SINGLE_SOURCE,
}
