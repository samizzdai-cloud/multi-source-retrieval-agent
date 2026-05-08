"""RetrieveAgent 端到端测试：3 queries × lite|balanced × bailian+feishu+google。

Usage:
    python3 test_retrieve_3q_e2e.py [--presets lite,balanced] [--sources bailian,feishu,google]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/Users/javendai/Documents/workplace/Multi-source_RAG_Agent")

try:
    from dotenv import load_dotenv
    load_dotenv("/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/.env")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("e2e_3q")

ROOT = Path("/Users/javendai/Documents/workplace/Multi-source_RAG_Agent")
REPORT_JSON = ROOT / "test_3q_e2e_report.json"
REPORT_MD   = ROOT / "test_3q_e2e_report.md"

QUERIES = [
    {"id": 44, "query": "REDMAGIC 11 Pro 各站点首发销量和DTC渠道表现"},
    {"id": 51, "query": "nubia 的产品和销售业务和销售额等的背景信息（近6个月）"},
    {"id": 48, "query": "REDMAGIC 品牌在游戏手机赛道的竞品格局和用户认知度"},
]


@dataclass
class QueryResult:
    query_id: int
    query: str
    preset: str
    sources_enabled: List[str]
    elapsed_s: float = 0.0
    n_focuses: int = 0
    final_top_k: int = 0
    n_chunks_emitted: int = 0
    merged_count: int = 0
    chunks_per_source: Dict[str, int] = field(default_factory=dict)
    chunks_per_tier: Dict[str, int] = field(default_factory=dict)
    angle_tag_distribution: Dict[str, int] = field(default_factory=dict)
    angle_redundancy_max: int = 0
    focus_board_final: Dict[str, Any] = field(default_factory=dict)
    has_focus_query_field: bool = False
    has_score_field_leak: bool = False
    has_metadata_field_leak: bool = False
    schema_field_count: int = 0
    sample_chunks: List[str] = field(default_factory=list)   # first 5 content previews
    error: str = ""


async def run_one(qinfo: dict, preset: str, sources: List[str]) -> QueryResult:
    from tools.core.models import AgentConfig
    from tools.core.focus_board import FocusBoard
    from tools.core.eval_layer import build_eval_layer
    from tools.core.final_assembler import assemble_final_chunks, render_final_payload
    from retrieve_agent import (
        build_retrieve_agent_from_config,
        _prepare_focuses,
        _dispatch_sources,
        _merge_across_sources,
    )

    res = QueryResult(
        query_id=qinfo["id"],
        query=qinfo["query"],
        preset=preset,
        sources_enabled=list(sources),
    )
    started = time.perf_counter()

    try:
        cfg = AgentConfig(preset=preset, sources=sources)
        agent = build_retrieve_agent_from_config(cfg)
        enabled = list(agent.sources.keys())

        # pre-fetch: bailian allowed indexes
        bailian_allowed = None
        if "bailian" in agent.sources:
            bailian_allowed = getattr(agent.sources["bailian"], "_allowed_indexes", None)

        # Google drive spaces 改为懒加载（仅 personal_recent 类 sub_query 触发），
        # 此处不再预取，与生产 retrieve_agent._run_async_impl 保持一致。

        # feishu open_id injection
        if "feishu" in agent.sources:
            oid = agent.source_config.get("feishu_open_id", "FEISHU_OPEN_ID")
            if oid:
                agent.sources["feishu"]._current_open_id = oid

        # ── plan ──────────────────────────────────────────────────────
        focuses = await asyncio.to_thread(
            _prepare_focuses, qinfo["query"], enabled, bailian_allowed,
        )
        res.n_focuses = len(focuses)
        logger.info("[%s/Q%d] %d focuses: %s", preset, qinfo["id"], len(focuses),
                    [f.text[:40] for f in focuses])

        # ── dispatch ──────────────────────────────────────────────────
        focus_board = FocusBoard.from_focuses(focuses, quota=agent.config.focus_quota)
        per_source = await _dispatch_sources(agent.sources, focuses, focus_board=focus_board)
        for fid in focus_board.unsatisfied_focus_ids():
            focus_board.mark_terminal(fid, status="noise_only")
        res.focus_board_final = focus_board.snapshot()

        # ── merge ─────────────────────────────────────────────────────
        merged = _merge_across_sources(per_source)
        res.merged_count = len(merged)

        # ── assemble ──────────────────────────────────────────────────
        final_top_k = agent.config.final_top_k(len(focuses))
        res.final_top_k = final_top_k

        if agent.config.eval_layer_active:
            eval_layer = build_eval_layer(agent.config.eval_layer_effective_mode)
            selected = eval_layer.rank(merged, focuses, final_top_k)
        else:
            selected = await assemble_final_chunks(
                chunks=merged,
                focuses=focuses,
                final_top_k=final_top_k,
                focus_quota=agent.config.focus_quota,
                preset=preset,
                query=qinfo["query"],
                llm_config=None,
                llm_invoker=None,
            )

        payload = render_final_payload(qinfo["query"], selected, focuses)
        res.elapsed_s = round(time.perf_counter() - started, 2)

        # ── output analysis ───────────────────────────────────────────
        chunks_out = payload.get("chunks") or []
        res.n_chunks_emitted = len(chunks_out)

        if chunks_out:
            keys = set(chunks_out[0].keys())
            res.schema_field_count    = len(keys)
            res.has_focus_query_field = "focus_query" in keys
            res.has_score_field_leak  = "score" in keys
            res.has_metadata_field_leak = "metadata" in keys

        for c in chunks_out:
            tier = c.get("confidence_tier") or "(empty)"
            res.chunks_per_tier[tier] = res.chunks_per_tier.get(tier, 0) + 1
            src = c.get("source_tool") or "(empty)"
            res.chunks_per_source[src] = res.chunks_per_source.get(src, 0) + 1

        # angle_tag from selected ChunkEntry objects
        angle_per_focus: Dict[tuple, int] = {}
        for c in selected:
            angle = ((c.metadata or {}).get("angle_tag") or "").lower() or "(none)"
            res.angle_tag_distribution[angle] = res.angle_tag_distribution.get(angle, 0) + 1
            for fid in (c.target_focuses or set()):
                k = (fid, angle)
                angle_per_focus[k] = angle_per_focus.get(k, 0) + 1
        res.angle_redundancy_max = max(angle_per_focus.values()) if angle_per_focus else 0

        # content previews for spot-check
        res.sample_chunks = [
            (c.get("source_name") or "") + " | " + (c.get("text") or "")[:120]
            for c in chunks_out[:5]
        ]

        logger.info(
            "[%s/Q%d] done elapsed=%.2fs emitted=%d merged=%d C/I/N=%d/%d/%d",
            preset, qinfo["id"], res.elapsed_s, res.n_chunks_emitted, res.merged_count,
            res.chunks_per_tier.get("critical", 0),
            res.chunks_per_tier.get("important", 0),
            res.chunks_per_tier.get("noise", 0),
        )

    except Exception as exc:
        res.error = f"{type(exc).__name__}: {exc}"
        res.elapsed_s = round(time.perf_counter() - started, 2)
        logger.exception("[%s/Q%d] FAILED", preset, qinfo["id"])

    return res


def _summarize(results: List[QueryResult]) -> str:
    lines = ["# RetrieveAgent 3-Query E2E Report", ""]
    by_preset: Dict[str, List[QueryResult]] = {}
    for r in results:
        by_preset.setdefault(r.preset, []).append(r)

    for preset, rs in by_preset.items():
        lines.append(f"## {preset}")
        lines.append("")
        lines.append(
            "| Q | Query | Latency | Focuses | TopK | Emitted | Merged "
            "| C/I/Noise | Sources | Angle MaxRedund | Schema | Error |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for r in rs:
            tier_str = (
                f"{r.chunks_per_tier.get('critical',0)}/"
                f"{r.chunks_per_tier.get('important',0)}/"
                f"{r.chunks_per_tier.get('noise',0)}"
            )
            src_str = ",".join(f"{k}:{v}" for k, v in sorted(r.chunks_per_source.items()))
            schema_ok = r.has_focus_query_field and not r.has_score_field_leak and not r.has_metadata_field_leak
            q_short = r.query[:28] + "…" if len(r.query) > 28 else r.query
            lines.append(
                f"| Q{r.query_id} | {q_short} | {r.elapsed_s:.2f}s | {r.n_focuses} | {r.final_top_k} | "
                f"{r.n_chunks_emitted} | {r.merged_count} | {tier_str} | {src_str} | "
                f"{r.angle_redundancy_max} | {'✅' if schema_ok else '⚠️'} | {r.error or '-'} |"
            )
        lines.append("")

        # per-query detail
        for r in rs:
            lines.append(f"### {preset} / Q{r.query_id}: {r.query}")
            lines.append("")
            lines.append(f"**focus_board**")
            lines.append("```json")
            lines.append(json.dumps(r.focus_board_final, ensure_ascii=False, indent=2))
            lines.append("```")
            lines.append(f"angle_tag distribution: {dict(sorted(r.angle_tag_distribution.items(), key=lambda x: -x[1]))}")
            if r.sample_chunks:
                lines.append("")
                lines.append("**Sample chunks (top 5)**")
                for i, s in enumerate(r.sample_chunks, 1):
                    lines.append(f"{i}. {s}")
            lines.append("")

    return "\n".join(lines)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--presets", default="lite,balanced")
    ap.add_argument("--sources", default="bailian,feishu,google")
    ap.add_argument("--queries", default="44,51,48")
    args = ap.parse_args()

    presets = [s.strip() for s in args.presets.split(",") if s.strip()]
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    qids    = {int(x) for x in args.queries.split(",") if x.strip()}
    run_qs  = [q for q in QUERIES if q["id"] in qids]

    results: List[QueryResult] = []
    for preset in presets:
        for q in run_qs:
            logger.info("=" * 90)
            logger.info("[run] preset=%s Q%d '%s' sources=%s", preset, q["id"], q["query"], sources)
            r = await run_one(q, preset, sources)
            results.append(r)

    REPORT_JSON.write_text(
        json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    REPORT_MD.write_text(_summarize(results), encoding="utf-8")
    logger.info("wrote %s", REPORT_JSON)
    logger.info("wrote %s", REPORT_MD)
    print("\n" + _summarize(results))


if __name__ == "__main__":
    asyncio.run(main())
