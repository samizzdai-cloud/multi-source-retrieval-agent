"""Preset latency + quality benchmark — v2.2 production pipeline.

Runs 4 user queries × 3 presets (lite / balanced / deep_dive) through the live
RetrieveAgent and prints a structured report.

Usage:
    python eval_preset_bench.py                        # all presets, all queries
    python eval_preset_bench.py --presets lite,balanced
    python eval_preset_bench.py --queries 1,3          # subset of queries by 1-based index
    python eval_preset_bench.py --output results.json  # also save raw JSON
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── env ───────────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=False)
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%H:%M:%S",
)
# suppress noisy sub-loggers by default
for _noisy in ("httpx", "httpcore", "urllib3", "google.auth"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger("bench")

# ── queries ────────────────────────────────────────────────────────────────────

QUERIES: List[Dict[str, str]] = [
    {
        "id": "Q1",
        "label": "nubia 2026 产品计划 + 营销策略",
        "text": "nubia 2026年的待上市的产品计划，有哪些产品？营销策略区别？",
    },
    {
        "id": "Q2",
        "label": "REDMAGIC 11 Pro KOL & 媒体评测",
        "text": "REDMAGIC 11 Pro 首发KOL合作进度和媒体评测发布情况",
    },
    {
        "id": "Q3",
        "label": "nubia VOC / DTC 服务问题",
        "text": "nubia 品牌最近的VOC情况，哪一些是关于其DTC产品的服务问题？",
    },
    {
        "id": "Q4",
        "label": "REDMAGIC 新品预热社媒传播策略 & 关键节点",
        "text": "REDMAGIC 新品预热期的社媒传播策略和关键节点",
    },
    {
        "id": "Q5",
        "label": "nubia 日本市场 KOL 名单 & 博主合作进度",
        "text": "nubia 日本市场的KOL合作名单和已确认的博主合作进度",
    },
]

PRESETS = ["lite", "balanced", "deep_dive"]


# ── result data class ──────────────────────────────────────────────────────────

@dataclass
class StageTimings:
    plan_ms: Optional[int] = None
    dispatch_ms: Optional[int] = None
    merge_ms: Optional[int] = None
    assemble_ms: Optional[int] = None
    total_ms: Optional[int] = None


@dataclass
class SourceBreakdown:
    source: str
    chunks: int


@dataclass
class ChunkSummary:
    rank: int
    source: str
    tier: Optional[str]
    score: Optional[float]
    heading: str
    preview: str   # first 120 chars of content


@dataclass
class LoopEvent:
    source: str
    loop_n: int
    decision: str   # "stop" | "continue" | "enforced_stop" | "skip_eval" | "0chunks"

@dataclass
class SourceAdoption:
    source: str
    produced: int    # after SourceAgent eval_only
    selected: int    # in final picked list
    adoption_pct: float

@dataclass
class QueryResult:
    query_id: str
    query_label: str
    preset: str
    timings: StageTimings = field(default_factory=StageTimings)
    focuses: int = 0
    assemble_path: str = ""
    chunks_picked: int = 0
    chunks_cap: int = 0
    source_breakdown: List[SourceBreakdown] = field(default_factory=list)
    source_adoption: List[SourceAdoption] = field(default_factory=list)
    loop_events: List[LoopEvent] = field(default_factory=list)
    chunk_summaries: List[ChunkSummary] = field(default_factory=list)
    raw_payload: Optional[Dict[str, Any]] = None
    errors: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def timing_label(self) -> str:
        t = self.timings
        parts = []
        if t.plan_ms is not None:
            parts.append(f"plan={t.plan_ms}ms")
        if t.dispatch_ms is not None:
            parts.append(f"dispatch={t.dispatch_ms}ms")
        if t.merge_ms is not None:
            parts.append(f"merge={t.merge_ms}ms")
        if t.assemble_ms is not None:
            parts.append(f"assemble={t.assemble_ms}ms")
        if t.total_ms is not None:
            parts.append(f"total={t.total_ms}ms")
        return "  ".join(parts)


# ── event text parsing ─────────────────────────────────────────────────────────

_RE_PLAN      = re.compile(r"\[retrieve\] plan focuses=(\d+) total_ms=(\d+)")
_RE_DISPATCH  = re.compile(r"\[retrieve\] dispatch sources=\d+ chunks=\(([^)]*)\) total_ms=(\d+)")
_RE_MERGE     = re.compile(r"\[retrieve\] merge merged_chunks=\d+ total_ms=(\d+)")
_RE_ASSEMBLE  = re.compile(r"\[retrieve\] assemble path=(\S+) picked=(\d+) cap=(\d+) total_ms=(\d+)")
_RE_DONE      = re.compile(r"\[retrieve\] done chunks=(\d+) elapsed_ms=(\d+)")


def _parse_event_text(text: str, result: QueryResult, stage_starts: Dict[str, float]) -> None:
    """Extract timing and metrics from retrieve text events."""
    now_ms = int(time.monotonic() * 1000)

    m = _RE_PLAN.search(text)
    if m:
        result.focuses = int(m.group(1))
        prev = stage_starts.get("plan_start")
        if prev is not None:
            result.timings.plan_ms = int((time.monotonic() - prev) * 1000)
        stage_starts["dispatch_start"] = time.monotonic()
        return

    m = _RE_DISPATCH.search(text)
    if m:
        chunks_str = m.group(1)  # e.g. "feishu:3, bailian:5"
        result.source_breakdown = []
        for part in chunks_str.split(","):
            part = part.strip()
            if ":" in part:
                src, n = part.split(":", 1)
                result.source_breakdown.append(SourceBreakdown(source=src.strip(), chunks=int(n.strip())))
        prev = stage_starts.get("dispatch_start")
        if prev is not None:
            result.timings.dispatch_ms = int((time.monotonic() - prev) * 1000)
        stage_starts["merge_start"] = time.monotonic()
        return

    m = _RE_MERGE.search(text)
    if m:
        prev = stage_starts.get("merge_start")
        if prev is not None:
            result.timings.merge_ms = int((time.monotonic() - prev) * 1000)
        stage_starts["assemble_start"] = time.monotonic()
        return

    m = _RE_ASSEMBLE.search(text)
    if m:
        result.assemble_path = m.group(1)
        result.chunks_picked = int(m.group(2))
        result.chunks_cap = int(m.group(3))
        prev = stage_starts.get("assemble_start")
        if prev is not None:
            result.timings.assemble_ms = int((time.monotonic() - prev) * 1000)
        return

    m = _RE_DONE.search(text)
    if m:
        result.timings.total_ms = int(m.group(2))
        return


def _parse_payload(payload: Dict[str, Any], result: QueryResult) -> None:
    """Extract chunk summaries from the final retrieval_result payload."""
    chunks = payload.get("chunks") or []
    summaries: List[ChunkSummary] = []
    for i, c in enumerate(chunks[:20]):
        summaries.append(ChunkSummary(
            rank=i + 1,
            source=c.get("source_name", ""),
            tier=c.get("confidence_tier") or c.get("tier"),
            score=c.get("score"),
            heading=str(c.get("heading_path", "") or "")[:80],
            preview=str(c.get("text", "") or "")[:120].replace("\n", " "),
        ))
    result.chunk_summaries = summaries


# ── single-query run ───────────────────────────────────────────────────────────

async def run_one(
    query: Dict[str, str],
    preset: str,
) -> QueryResult:
    from google.adk.runners import InMemoryRunner
    from google.genai import types as genai_types
    from root_agent_config import build_root_retrieve_agent

    result = QueryResult(
        query_id=query["id"],
        query_label=query["label"],
        preset=preset,
    )

    # Build agent with the requested preset
    os.environ["RAG_PRESET"] = preset
    try:
        agent = build_root_retrieve_agent(name=f"retrieve_{preset}_{uuid.uuid4().hex[:6]}")
    except Exception as e:
        result.error = f"build failed: {e}"
        logger.error("[%s/%s] agent build failed: %s", query["id"], preset, e)
        return result

    runner = InMemoryRunner(agent=agent, app_name="bench")
    uid = f"bench_{uuid.uuid4().hex[:8]}"
    session = await runner.session_service.create_session(app_name="bench", user_id=uid)

    content = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=query["text"])],
    )

    stage_starts: Dict[str, float] = {"plan_start": time.monotonic()}
    global_start = time.monotonic()

    logger.info(">>> [%s / %s] start: %s", query["id"], preset, query["text"][:60])

    try:
        async for event in runner.run_async(
            user_id=uid,
            session_id=session.id,
            new_message=content,
        ):
            # text events carry timing markers
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        _parse_event_text(part.text, result, stage_starts)

            # final event carries retrieval_result in state_delta
            if event.actions and event.actions.state_delta:
                raw_json = event.actions.state_delta.get("retrieval_result")
                if raw_json:
                    try:
                        payload = json.loads(raw_json)
                        result.raw_payload = payload
                        _parse_payload(payload, result)
                    except Exception as e:
                        logger.warning("[%s/%s] payload parse error: %s", query["id"], preset, e)

    except Exception as e:
        result.error = str(e)
        logger.exception("[%s/%s] run failed: %s", query["id"], preset, e)

    if result.timings.total_ms is None:
        result.timings.total_ms = int((time.monotonic() - global_start) * 1000)

    logger.info(
        "<<< [%s / %s] done  picked=%d  %s",
        query["id"], preset, result.chunks_picked, result.timing_label(),
    )
    return result


# ── report printing ────────────────────────────────────────────────────────────

def _bar(value: Optional[int], max_val: int, width: int = 20) -> str:
    if value is None or max_val == 0:
        return " " * width
    filled = min(width, round((value / max_val) * width))
    return "█" * filled + "░" * (width - filled)


def print_report(results: List[QueryResult]) -> None:
    print("\n" + "=" * 90)
    print("  PRESET BENCHMARK — TIMING & QUALITY SUMMARY")
    print("=" * 90)

    # ── timing table ──────────────────────────────────────────────────────────
    print("\n[TIMING]  (ms)")
    print(f"  {'Query':<8}  {'Preset':<12}  {'Plan':>6}  {'Dispatch':>9}  {'Merge':>6}  {'Assemble':>9}  {'Total':>7}  {'Focused':>8}  {'Picked':>7}")
    print("  " + "-" * 85)

    totals_by_preset: Dict[str, List[int]] = {p: [] for p in PRESETS}
    for r in results:
        if r.error:
            print(f"  {r.query_id:<8}  {r.preset:<12}  ERROR: {r.error}")
            continue
        t = r.timings
        row = (
            f"  {r.query_id:<8}  {r.preset:<12}"
            f"  {(t.plan_ms or 0):>6}"
            f"  {(t.dispatch_ms or 0):>9}"
            f"  {(t.merge_ms or 0):>6}"
            f"  {(t.assemble_ms or 0):>9}"
            f"  {(t.total_ms or 0):>7}"
            f"  {r.focuses:>8}"
            f"  {r.chunks_picked:>7}"
        )
        print(row)
        if t.total_ms:
            totals_by_preset[r.preset].append(t.total_ms)

    # ── preset means ──────────────────────────────────────────────────────────
    print("\n  Preset averages (total_ms):")
    for preset in PRESETS:
        vals = totals_by_preset[preset]
        if vals:
            avg = sum(vals) / len(vals)
            print(f"    {preset:<12}  avg={avg:.0f}ms  min={min(vals)}ms  max={max(vals)}ms")

    # ── source breakdown per preset ───────────────────────────────────────────
    print("\n[SOURCE BREAKDOWN]")
    for qobj in QUERIES:
        qid = qobj["id"]
        print(f"\n  {qid}: {qobj['label']}")
        for preset in PRESETS:
            row = next((r for r in results if r.query_id == qid and r.preset == preset), None)
            if not row or row.error:
                print(f"    {preset:<12}  —")
                continue
            src_parts = "  ".join(f"{s.source}:{s.chunks}" for s in row.source_breakdown) or "—"
            print(f"    {preset:<12}  {src_parts}")

    # ── chunk quality per query (balanced) ────────────────────────────────────
    print("\n[CHUNK QUALITY — balanced preset, top-10 chunks]")
    for qobj in QUERIES:
        qid = qobj["id"]
        row = next((r for r in results if r.query_id == qid and r.preset == "balanced"), None)
        if not row or row.error or not row.chunk_summaries:
            print(f"\n  {qid}: no results")
            continue
        print(f"\n  {qid}: {qobj['label']}")
        print(f"  {'#':>3}  {'Tier':<10}  {'Score':>6}  {'Source':<30}  Preview")
        print("  " + "-" * 80)
        for c in row.chunk_summaries[:10]:
            tier_str = (c.tier or "—")[:10]
            score_str = f"{c.score:.3f}" if c.score is not None else "  —  "
            src_str = (c.source or c.heading or "")[:30]
            print(f"  {c.rank:>3}  {tier_str:<10}  {score_str:>6}  {src_str:<30}  {c.preview}")

    print("\n" + "=" * 90)


# ── main ───────────────────────────────────────────────────────────────────────

async def main(presets: List[str], query_indices: Optional[List[int]], output: Optional[str]) -> None:
    queries = QUERIES
    if query_indices:
        queries = [QUERIES[i - 1] for i in query_indices if 1 <= i <= len(QUERIES)]

    total = len(queries) * len(presets)
    print(f"\nBenchmark: {len(queries)} queries × {len(presets)} presets = {total} runs")
    print(f"Presets:  {presets}")
    print(f"Queries:  {[q['id'] for q in queries]}\n")

    results: List[QueryResult] = []

    # Run sequentially (each query is internally parallel per source).
    # Outer loop is sequential to avoid saturating API rate limits.
    for preset in presets:
        for query in queries:
            r = await run_one(query, preset)
            results.append(r)

    print_report(results)

    if output:
        out = [asdict(r) for r in results]
        Path(output).write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str))
        print(f"\nRaw JSON saved to {output}")


def _parse_args():
    p = argparse.ArgumentParser(description="Preset latency + quality benchmark")
    p.add_argument("--presets", default=",".join(PRESETS),
                   help="Comma-separated presets to run (default: all)")
    p.add_argument("--queries", default=None,
                   help="Comma-separated 1-based query indices (default: all)")
    p.add_argument("--output", default=None, help="Save raw JSON results to this path")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    presets = [p.strip() for p in args.presets.split(",") if p.strip()]
    query_indices = ([int(x.strip()) for x in args.queries.split(",") if x.strip()]
                     if args.queries else None)
    asyncio.run(main(presets, query_indices, args.output))
