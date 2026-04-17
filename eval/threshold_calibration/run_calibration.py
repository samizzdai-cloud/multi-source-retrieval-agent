"""
Step A2-A3: Run wide_recall on 24 queries, collect traces with full raw_chunks.

Usage:
    python -m eval.threshold_calibration.run_calibration

Output:
    .eval_output/threshold_calibration/traces/q{id}.json  — per-query traces
    .eval_output/threshold_calibration/calibration_dataset.json — (query, focus, chunk, scores) tuples
"""

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv
load_dotenv(override=True)

from eval.config import WIDE_RECALL, ExperimentConfig
from eval.dataset import load_queries
from eval.runner import run_experiment

# ── Query selection: 8 data + 8 concept + 8 mixed (balanced difficulty/project) ──

CALIBRATION_QUERY_IDS = {
    # data: 6 (2 easy + 2 medium + 2 hard, multi-project)
    "data": [1, 13, 5, 8, 10, 27],
    # concept: 6 (2 easy + 2 medium + 2 hard, multi-project)
    "concept": [3, 22, 28, 30, 7, 48],
    # mixed: 6 (1 easy + 5 medium)
    "mixed": [61, 6, 25, 31, 35, 58],
}

ALL_IDS = sorted(
    CALIBRATION_QUERY_IDS["data"]
    + CALIBRATION_QUERY_IDS["concept"]
    + CALIBRATION_QUERY_IDS["mixed"]
)

OUTPUT_DIR = ROOT / ".eval_output" / "threshold_calibration"


def build_calibration_dataset(traces_dir: Path) -> list:
    """From wide_recall traces, extract (query, focus, chunk, scores) for top-50% chunks."""
    dataset = []
    for trace_file in sorted(traces_dir.glob("q*.json")):
        trace = json.loads(trace_file.read_text())
        query = trace["query"]
        query_id = trace["query_id"]
        query_type = trace["plan"]["query_type"]
        focuses = [f for fg in trace["plan"]["focus_groups"] for f in fg["focuses"] if f]
        raw_chunks = trace.get("raw_chunks_all", [])

        if not raw_chunks:
            continue

        # Sort by score descending, take top 50%
        raw_chunks.sort(key=lambda c: c.get("score", 0), reverse=True)
        cutoff = max(1, len(raw_chunks) // 2)
        top_chunks = raw_chunks[:cutoff]

        for chunk in top_chunks:
            # Build one evaluation entry per (query, focus, chunk) triple
            for focus in focuses:
                dataset.append({
                    "query_id": query_id,
                    "query": query,
                    "query_type": query_type,
                    "focus": focus,
                    "chunk_idx": chunk["idx"],
                    "chunk_text": chunk["text"],  # first 300 chars from trace
                    "chunk_score": chunk["score"],
                    "source_tool": chunk["source_tool"],
                    "heading_path": chunk.get("heading_path", ""),
                    "metadata": chunk.get("metadata_subset", {}),
                })

    return dataset


async def main():
    print(f"=== Threshold Calibration: {len(ALL_IDS)} queries ===")
    print(f"  data:    {CALIBRATION_QUERY_IDS['data']}")
    print(f"  concept: {CALIBRATION_QUERY_IDS['concept']}")
    print(f"  mixed:   {CALIBRATION_QUERY_IDS['mixed']}")
    print()

    # Use WIDE_RECALL config (max recall, no early-stop)
    # [v4] Wide recall + pool_timeout=55s (aligned with hard_timeout_seconds=55s)
    wide_overrides = dict(WIDE_RECALL.retrieval_overrides)
    wide_overrides["feishu_pool_timeout"] = 55.0           # [v4] full recall window
    wide_overrides["feishu_skip_preflight"] = True          # preflight kills valid feishu results
    wide_overrides["feishu_pool_noise_threshold"] = 0.0     # collect ALL chunks, let grader decide quality

    config = ExperimentConfig(
        name="threshold_calibration_v2",
        description="Wide recall + full DocPool + full chunk text — 18 queries",
        eval_config=WIDE_RECALL.eval_config,
        retrieval_overrides=wide_overrides,
        planner_overrides=WIDE_RECALL.planner_overrides,
    )

    # Load query objects from dataset
    queries = load_queries(ids=ALL_IDS)
    print(f"Loaded {len(queries)} queries from eval dataset")

    # Run experiment
    output = OUTPUT_DIR
    output.mkdir(parents=True, exist_ok=True)
    await run_experiment(config, queries=queries, output_dir=output)

    # Build calibration dataset
    traces_dir = output / "traces"
    print("\nBuilding calibration dataset from traces...")
    dataset = build_calibration_dataset(traces_dir)

    dataset_path = output / "calibration_dataset.json"
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2))
    print(f"Saved {len(dataset)} (query, focus, chunk) tuples → {dataset_path}")

    # Summary stats
    by_type = {}
    for d in dataset:
        qt = d["query_type"]
        by_type.setdefault(qt, []).append(d["chunk_score"])
    for qt, scores in sorted(by_type.items()):
        print(f"  {qt}: {len(scores)} entries, score range [{min(scores):.3f}, {max(scores):.3f}], "
              f"mean={sum(scores)/len(scores):.3f}")


if __name__ == "__main__":
    asyncio.run(main())
