"""Multi-source e2e: bailian + feishu + google × 3 presets × 2 queries.

Drives the full retrieve_agent pipeline (dispatch + merge + assembler +
focus_board + cooperative cancel) and captures latency / chunks quality /
process compliance for the P1-P5 changes.

Usage:
    python3 test_multi_source_p1_p5_e2e.py [--presets lite,balanced,deep_dive] [--queries 50,51]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/Users/javendai/Documents/workplace/Multi-source_RAG_Agent")

# Load .env so credentials are visible to source agents
try:
    from dotenv import load_dotenv
    load_dotenv("/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/.env")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("p1p5_e2e")

ROOT = Path("/Users/javendai/Documents/workplace/Multi-source_RAG_Agent")
REPORT_JSON = ROOT / "test_p1_p5_e2e_report.json"
REPORT_MD = ROOT / "test_p1_p5_e2e_report.md"

QUERIES = [
    {"id": 50, "query": 'redmagic 2025年度的"净销售额(原货币）"净销售额 (USD)"订单数"客单价(USD)"访客数"加购数 转化率"'},
    {"id": 51, "query": "nubia 的产品和销售业务和销售额等的背景信息（近6个月）"},
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
    chunks_per_focus: Dict[str, int] = field(default_factory=dict)
    chunks_per_source: Dict[str, int] = field(default_factory=dict)
    chunks_per_tier: Dict[str, int] = field(default_factory=dict)
    angle_tag_distribution: Dict[str, int] = field(default_factory=dict)
    angle_redundancy_max: int = 0           # 同 focus 内同 angle 最多多少条
    focus_board_final: Dict[str, Any] = field(default_factory=dict)
    merged_count: int = 0                   # assembler 输入候选数
    has_focus_query_field: bool = False
    has_score_field_leak: bool = False      # 出口不应有 score
    has_metadata_field_leak: bool = False   # 出口不应有 metadata
    schema_field_count: int = 0
    error: str = ""


async def run_one(qinfo: dict, preset: str, sources: List[str]) -> QueryResult:
    """Drive the full retrieve_agent pipeline (mirror _run_async_impl)."""
    from tools.core.models import AgentConfig
    from tools.core.focus_board import FocusBoard
    from tools.core.final_assembler import (
        LlmAssemblerConfig, assemble_final_chunks, render_final_payload,
    )
    from retrieve_agent import (
        build_retrieve_agent_from_config,
        _prepare_focuses,
        _dispatch_sources,
        _merge_across_sources,
    )
    from agents.source_agent_base import _run_llm_agent
    from tools.core.final_assembler import build_final_assembler_agent

    res = QueryResult(
        query_id=qinfo["id"], query=qinfo["query"], preset=preset, sources_enabled=list(sources),
    )

    started = time.perf_counter()
    try:
        cfg = AgentConfig(
            preset=preset,
            sources=sources,
        )
        agent = build_retrieve_agent_from_config(cfg)
        enabled = list(agent.sources.keys())

        # Pre-fetch context (mirror retrieve_agent._run_async_impl)
        bailian_allowed = None
        if "bailian" in agent.sources:
            bailian_allowed = getattr(agent.sources["bailian"], "_allowed_indexes", None)

        google_spaces: Optional[List[str]] = None
        if "google" in agent.sources:
            try:
                from google_workspace_tool.auth_utils import get_credentials
                from tools.google.google_retrieve import _SCOPES
                from google_workspace_tool.drive_space_lister import list_drive_spaces_async
                creds = get_credentials(scopes=_SCOPES)
                spaces_info = await list_drive_spaces_async(
                    creds=creds, include_shared_drives=True,
                    include_shared_folders=True, include_my_drive_folders=True,
                )
                google_spaces = [s["name"] for s in spaces_info if s.get("name")]
                logger.info("[%s/Q%d] fetched %d google spaces", preset, qinfo["id"], len(google_spaces))
            except Exception as e:
                logger.warning("[%s/Q%d] google_spaces fetch failed: %s", preset, qinfo["id"], e)

        # feishu open_id injection
        if "feishu" in agent.sources:
            configured_open_id = agent.source_config.get("feishu_open_id", "FEISHU_OPEN_ID")
            if configured_open_id:
                agent.sources["feishu"]._current_open_id = configured_open_id

        focuses = await asyncio.to_thread(
            _prepare_focuses, qinfo["query"], enabled, bailian_allowed, google_spaces,
        )
        res.n_focuses = len(focuses)
        logger.info("[%s/Q%d] %d focuses produced", preset, qinfo["id"], len(focuses))

        # Phase 4: focus_board
        focus_board = FocusBoard.from_focuses(focuses, quota=agent.config.focus_quota)
        per_source = await _dispatch_sources(agent.sources, focuses, focus_board=focus_board)
        for fid in focus_board.unsatisfied_focus_ids():
            focus_board.mark_terminal(fid, status="noise_only")
        res.focus_board_final = focus_board.snapshot()

        # Phase 3: merge + assemble
        merged = _merge_across_sources(per_source)
        res.merged_count = len(merged)

        final_top_k = agent.config.final_top_k(len(focuses))
        res.final_top_k = final_top_k

        llm_cfg = LlmAssemblerConfig(
            enabled=bool(merged),
            timeout_s=agent.config.eval_layer_llm_timeout_s,
            content_preview_chars=200,
            trigger_only_when_overflow=not agent.config.eval_layer_active,
        )

        async def _llm_invoker(prompt_text: str) -> Dict:
            llm_agent = build_final_assembler_agent()
            return await asyncio.wait_for(
                _run_llm_agent(llm_agent, prompt_text), timeout=llm_cfg.timeout_s,
            )

        selected = await assemble_final_chunks(
            chunks=merged, focuses=focuses, final_top_k=final_top_k,
            focus_quota=agent.config.focus_quota, preset=preset,
            query=qinfo["query"], llm_config=llm_cfg, llm_invoker=_llm_invoker,
        )

        payload = render_final_payload(qinfo["query"], selected, focuses)
        res.elapsed_s = round(time.perf_counter() - started, 2)

        # Output validation
        chunks_out = payload.get("chunks") or []
        res.n_chunks_emitted = len(chunks_out)

        # schema integrity
        if chunks_out:
            keys = set(chunks_out[0].keys())
            res.schema_field_count = len(keys)
            res.has_focus_query_field = "focus_query" in keys
            res.has_score_field_leak = "score" in keys
            res.has_metadata_field_leak = "metadata" in keys

        # per-focus / per-source / per-tier
        for c in chunks_out:
            tier = c.get("confidence_tier") or "(empty)"
            res.chunks_per_tier[tier] = res.chunks_per_tier.get(tier, 0) + 1
            src = c.get("source_tool") or "(empty)"
            res.chunks_per_source[src] = res.chunks_per_source.get(src, 0) + 1
            fqs = c.get("focus_query") or []
            for fq in fqs:
                key = fq[:30] + "..." if len(fq) > 30 else fq
                res.chunks_per_focus[key] = res.chunks_per_focus.get(key, 0) + 1

        # angle_tag distribution (from selected ChunkEntry objects, not payload — payload doesn't carry angle)
        angle_per_focus_count: Dict[tuple, int] = {}
        for c in selected:
            angle = ((c.metadata or {}).get("angle_tag") or "").lower() or "(none)"
            res.angle_tag_distribution[angle] = res.angle_tag_distribution.get(angle, 0) + 1
            for fid in (c.target_focuses or set()):
                key = (fid, angle)
                angle_per_focus_count[key] = angle_per_focus_count.get(key, 0) + 1
        res.angle_redundancy_max = max(angle_per_focus_count.values()) if angle_per_focus_count else 0

    except Exception as e:
        res.error = f"{type(e).__name__}: {e}"
        res.elapsed_s = round(time.perf_counter() - started, 2)
        logger.exception("[%s/Q%d] failed", preset, qinfo["id"])

    return res


def _summarize(results: List[QueryResult]) -> str:
    lines = ["# P1-P5 Multi-Source E2E Report", ""]
    by_preset: Dict[str, List[QueryResult]] = {}
    for r in results:
        by_preset.setdefault(r.preset, []).append(r)

    for preset, rs in by_preset.items():
        lines.append(f"## {preset}")
        lines.append("")
        lines.append("| Q | Latency | Focuses | TopK cap | Emitted | Merged | C/I/Noise | Sources | Max Angle Redundancy | Schema OK | Error |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in rs:
            tier_str = f"{r.chunks_per_tier.get('critical',0)}/{r.chunks_per_tier.get('important',0)}/{r.chunks_per_tier.get('noise',0)}"
            src_str = ",".join(f"{k}:{v}" for k, v in sorted(r.chunks_per_source.items()))
            schema_ok = (r.has_focus_query_field and not r.has_score_field_leak and not r.has_metadata_field_leak)
            lines.append(
                f"| Q{r.query_id} | {r.elapsed_s:.2f}s | {r.n_focuses} | {r.final_top_k} | "
                f"{r.n_chunks_emitted} | {r.merged_count} | {tier_str} | {src_str} | "
                f"{r.angle_redundancy_max} | {'✅' if schema_ok else '⚠️'} | {r.error or '-'} |"
            )
        lines.append("")
        # focus_board details per query
        for r in rs:
            lines.append(f"### Q{r.query_id} focus_board")
            lines.append("```json")
            lines.append(json.dumps(r.focus_board_final, ensure_ascii=False, indent=2))
            lines.append("```")
            lines.append(f"angle_tag distribution: {dict(sorted(r.angle_tag_distribution.items(), key=lambda x: -x[1]))}")
            lines.append("")
    return "\n".join(lines)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--presets", default="lite,balanced,deep_dive")
    ap.add_argument("--queries", default="50,51")
    ap.add_argument("--sources", default="bailian,feishu,google")
    args = ap.parse_args()

    presets = [s.strip() for s in args.presets.split(",") if s.strip()]
    qids = {int(x) for x in args.queries.split(",") if x.strip()}
    selected_q = [q for q in QUERIES if q["id"] in qids]
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    results: List[QueryResult] = []
    for preset in presets:
        for q in selected_q:
            logger.info("=" * 90)
            logger.info("[run] preset=%s Q%d sources=%s", preset, q["id"], sources)
            r = await run_one(q, preset, sources)
            results.append(r)
            logger.info(
                "[done] preset=%s Q%d elapsed=%.2fs emitted=%d merged=%d C/I/N=%d/%d/%d err=%s",
                preset, q["id"], r.elapsed_s, r.n_chunks_emitted, r.merged_count,
                r.chunks_per_tier.get("critical", 0),
                r.chunks_per_tier.get("important", 0),
                r.chunks_per_tier.get("noise", 0),
                r.error or "-",
            )

    REPORT_JSON.write_text(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_MD.write_text(_summarize(results), encoding="utf-8")
    logger.info("wrote %s", REPORT_JSON)
    logger.info("wrote %s", REPORT_MD)
    print("\n" + _summarize(results))


if __name__ == "__main__":
    asyncio.run(main())
