"""
Step A4: Use Gemini 3.1 Pro Preview to evaluate each (query, focus, chunk) triple.

Grading scale:
  5 = Directly and completely answers the focus question
  4 = Partially answers — contains key data/concepts but incomplete
  3 = Relevant context — useful background but doesn't answer directly
  2 = Weakly related — mentions topic but no actionable info
  1 = Irrelevant — no meaningful connection to the focus

Also extracts: semantic_relevance (1-5) and keyword_relevance (1-5) separately.

Usage:
    python -m eval.threshold_calibration.threshold_grader

Input:  .eval_output/threshold_calibration/calibration_dataset.json
Output: .eval_output/threshold_calibration/graded_dataset.json
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv
load_dotenv(override=True)

OUTPUT_DIR = ROOT / ".eval_output" / "threshold_calibration"
DATASET_PATH = OUTPUT_DIR / "calibration_dataset.json"
GRADED_PATH = OUTPUT_DIR / "graded_dataset.json"

GRADER_MODEL = "gemini-3-flash-preview"
MAX_CONCURRENT = 10
RETRY = 2


GRADING_PROMPT = """You are evaluating whether a retrieved text chunk can answer a specific focus question derived from a user query.

## User Query
{query}

## Focus (specific retrieval intent)
{focus}

## Query Type
{query_type}
(data = seeking specific numbers/metrics; concept = seeking strategies/analysis/explanations; mixed = both)

## Retrieved Chunk
Source: {source_tool} | Heading: {heading_path}
---
{chunk_text}
---

## Your Task
Evaluate this chunk on THREE dimensions. Return ONLY a JSON object (no other text):

{{
  "overall_grade": <1-5>,
  "semantic_relevance": <1-5>,
  "keyword_relevance": <1-5>,
  "reasoning": "<1-2 sentence explanation>"
}}

### Grading Scale

**overall_grade** (does this chunk answer the focus?):
- 5: Directly and completely answers the focus — contains the exact data/explanation needed
- 4: Partially answers — has key information but incomplete (e.g. missing time range, partial metrics)
- 3: Relevant context — useful background for understanding the answer, but doesn't directly answer
- 2: Weakly related — mentions the topic area but no actionable information for the focus
- 1: Irrelevant — no meaningful connection to the focus question

**semantic_relevance** (is the chunk semantically about the same topic?):
- 5: Exact topic match — the chunk discusses precisely what the focus asks about
- 4: Close topic — discusses the same domain/entity with minor topic drift
- 3: Related domain — same business area but different specific topic
- 2: Tangentially related — shares some keywords but different intent
- 1: Unrelated — different topic entirely

**keyword_relevance** (does the chunk contain the key entities/terms from the focus?):
- 5: All key entities and terms present (brand names, metrics, time periods, channels)
- 4: Most key terms present, missing 1-2 secondary terms
- 3: Some key terms present but missing important ones
- 2: Few matching terms — mostly generic language
- 1: No key terms from the focus found in the chunk

### Query-Type Specific Guidance
- **data** queries: grade higher if chunk contains actual numbers, tables, specific metrics. A chunk with "sales increased" without numbers is at most grade 3.
- **concept** queries: grade higher if chunk contains strategic reasoning, analysis frameworks, comparisons. Raw data tables without analysis are at most grade 3.
- **mixed** queries: balance both — data + analysis context expected.

Return ONLY the JSON object."""


async def _grade_one(entry: dict, sem: asyncio.Semaphore) -> dict:
    """Grade a single (query, focus, chunk) triple."""
    from tools.llm_client import call_llm

    prompt = GRADING_PROMPT.format(
        query=entry["query"],
        focus=entry["focus"],
        query_type=entry["query_type"],
        source_tool=entry["source_tool"],
        heading_path=entry.get("heading_path", ""),
        chunk_text=entry["chunk_text"],
    )

    result = dict(entry)
    for attempt in range(RETRY + 1):
        async with sem:
            try:
                response = await asyncio.to_thread(
                    call_llm, prompt,
                    model=GRADER_MODEL,
                    max_tokens=256,
                    _caller_tag="threshold_grader",
                )
                # Parse JSON from response
                clean = response.strip()
                if clean.startswith("```"):
                    import re
                    clean = re.sub(r'^```(?:json)?\s*', '', clean)
                    clean = re.sub(r'\s*```$', '', clean)
                parsed = json.loads(clean.strip())

                result["overall_grade"] = parsed.get("overall_grade", 0)
                result["semantic_relevance"] = parsed.get("semantic_relevance", 0)
                result["keyword_relevance"] = parsed.get("keyword_relevance", 0)
                result["reasoning"] = parsed.get("reasoning", "")
                result["grader_error"] = None
                return result
            except Exception as e:
                if attempt < RETRY:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                result["overall_grade"] = 0
                result["semantic_relevance"] = 0
                result["keyword_relevance"] = 0
                result["reasoning"] = ""
                result["grader_error"] = str(e)[:100]
                return result
    return result


async def main():
    if not DATASET_PATH.exists():
        sys.exit(f"ERROR: {DATASET_PATH} not found. Run run_calibration.py first.")

    dataset = json.loads(DATASET_PATH.read_text())
    print(f"Loaded {len(dataset)} entries from {DATASET_PATH}")

    # Check for existing progress (resume support)
    graded = []
    graded_ids = set()
    if GRADED_PATH.exists():
        graded = json.loads(GRADED_PATH.read_text())
        graded_ids = {(g["query_id"], g["focus"], g["chunk_idx"]) for g in graded}
        print(f"  Resuming: {len(graded)} already graded, {len(dataset) - len(graded_ids)} remaining")

    remaining = [e for e in dataset
                 if (e["query_id"], e["focus"], e["chunk_idx"]) not in graded_ids]

    if not remaining:
        print("All entries already graded!")
    else:
        sem = asyncio.Semaphore(MAX_CONCURRENT)
        t0 = time.monotonic()
        total = len(remaining)
        done = 0

        # Process in batches for progress reporting
        BATCH = 20
        for i in range(0, total, BATCH):
            batch = remaining[i:i + BATCH]
            results = await asyncio.gather(*[_grade_one(e, sem) for e in batch])
            graded.extend(results)
            done += len(batch)

            # Save progress after each batch
            GRADED_PATH.write_text(json.dumps(graded, ensure_ascii=False, indent=2))

            elapsed = time.monotonic() - t0
            rate = done / elapsed if elapsed > 0 else 0
            eta = (total - done) / rate if rate > 0 else 0
            errors = sum(1 for r in results if r.get("grader_error"))
            print(f"  [{done}/{total}] {rate:.1f}/s, ETA {eta:.0f}s, errors={errors}")

    # Final save
    GRADED_PATH.write_text(json.dumps(graded, ensure_ascii=False, indent=2))
    print(f"\nSaved {len(graded)} graded entries → {GRADED_PATH}")

    # Quick summary
    valid = [g for g in graded if g.get("overall_grade", 0) > 0]
    if valid:
        avg = sum(g["overall_grade"] for g in valid) / len(valid)
        avg_sem = sum(g["semantic_relevance"] for g in valid) / len(valid)
        avg_kw = sum(g["keyword_relevance"] for g in valid) / len(valid)
        print(f"  overall_grade mean: {avg:.2f}")
        print(f"  semantic_relevance mean: {avg_sem:.2f}")
        print(f"  keyword_relevance mean: {avg_kw:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
