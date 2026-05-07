"""Google SourceAgent — unified family framework regression test.

Runs 4 baseline queries × {balanced, lite} and captures latency + tier counts
to compare against the codex baseline.

Usage:
    python3 test_google_unified_e2e.py [--scenarios balanced,lite] [--queries 1,4,44,48]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, "/Users/javendai/Documents/workplace/Multi-source_RAG_Agent")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("e2e")

ROOT = Path("/Users/javendai/Documents/workplace/Multi-source_RAG_Agent")
REPORT_JSON = ROOT / "test_google_unified_report.json"
REPORT_MD = ROOT / "test_google_unified_report.md"

QUERIES = [
    {"id": 1, "query": "nubia 英国站3月份DTC渠道的GMV和订单量"},
    {"id": 4, "query": "nubia 日本市场的KOL合作名单和已确认的博主合作进度"},
    {"id": 44, "query": "REDMAGIC 11 Pro 各站点首发销量和DTC渠道表现"},
    {"id": 48, "query": "REDMAGIC 品牌在游戏手机赛道的竞品格局和用户认知度"},
]


@dataclass
class QueryResult:
    query_id: int
    query: str
    scenario: str
    elapsed_s: float = 0.0
    chunks_critical: int = 0
    chunks_important: int = 0
    chunks_noise: int = 0
    error: str = ""
    pool_submits: Dict[str, int] = field(default_factory=dict)
    sheet_prefilter_raw: int = 0
    sheet_prefilter_selected: int = 0
    dedup_drops: Dict[str, int] = field(default_factory=dict)


def _build_cfg(scenario: str):
    from tools.core.models import RetrieveConfig
    rc = RetrieveConfig(preset=scenario, enabled_sources=["google"])
    return rc.effective_config_for("google")


async def run_one(qinfo: dict, scenario: str) -> QueryResult:
    from agents.source_agent_base import Focus
    from agents.google.google_source_agent import GoogleSourceAgent

    res = QueryResult(query_id=qinfo["id"], query=qinfo["query"], scenario=scenario)
    cfg = _build_cfg(scenario)
    agent = GoogleSourceAgent(cfg=cfg)
    focus = Focus(focus_id="f1", text=qinfo["query"], targets=["google"])

    started = time.perf_counter()
    try:
        out = await agent.run([focus])
        res.elapsed_s = round(time.perf_counter() - started, 2)
        for ch in (out.top_chunks or []):
            tier = (ch.tier or "").lower()
            if tier == "critical":
                res.chunks_critical += 1
            elif tier == "important":
                res.chunks_important += 1
            else:
                res.chunks_noise += 1
    except Exception as e:
        res.error = f"{type(e).__name__}: {e}"
        res.elapsed_s = round(time.perf_counter() - started, 2)
        logger.exception("query failed: %s", qinfo["id"])
    return res


def _summarize(results: List[QueryResult]) -> str:
    by_scenario: Dict[str, List[QueryResult]] = {}
    for r in results:
        by_scenario.setdefault(r.scenario, []).append(r)
    lines = ["# Google Unified Framework — Regression Report", ""]
    for sc, rs in by_scenario.items():
        lines.append(f"## {sc}")
        lines.append("")
        lines.append("| Q | Latency | Critical | Important | Noise | Error |")
        lines.append("|---|---|---|---|---|---|")
        total_lat = 0.0
        total_c = total_i = total_n = 0
        for r in rs:
            lines.append(f"| Q{r.query_id} | {r.elapsed_s:.2f}s | {r.chunks_critical} | {r.chunks_important} | {r.chunks_noise} | {r.error or '-'} |")
            total_lat += r.elapsed_s
            total_c += r.chunks_critical
            total_i += r.chunks_important
            total_n += r.chunks_noise
        avg = total_lat / max(1, len(rs))
        lines.append(f"| **avg** | **{avg:.2f}s** | **{total_c}** | **{total_i}** | **{total_n}** | |")
        lines.append("")
    return "\n".join(lines)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", default="balanced,lite")
    ap.add_argument("--queries", default="1,4,44,48")
    args = ap.parse_args()

    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    qids = {int(x) for x in args.queries.split(",") if x.strip()}
    selected = [q for q in QUERIES if q["id"] in qids]

    results: List[QueryResult] = []
    for sc in scenarios:
        for q in selected:
            logger.info("=" * 80)
            logger.info("[run] scenario=%s Q%s", sc, q["id"])
            r = await run_one(q, sc)
            results.append(r)
            logger.info("[done] Q%s scenario=%s elapsed=%.2fs C=%d I=%d N=%d err=%s",
                        q["id"], sc, r.elapsed_s, r.chunks_critical, r.chunks_important,
                        r.chunks_noise, r.error or "-")

    REPORT_JSON.write_text(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_MD.write_text(_summarize(results), encoding="utf-8")
    logger.info("wrote %s", REPORT_JSON)
    logger.info("wrote %s", REPORT_MD)
    print("\n" + _summarize(results))


if __name__ == "__main__":
    asyncio.run(main())
