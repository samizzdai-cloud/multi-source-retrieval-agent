"""Google SourceAgent balanced mode 端到端测试 + 阶段对比报告。

运行:
    python3 test_google_balanced_e2e.py
"""

import asyncio
import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from statistics import median
from typing import Any, Dict, List

sys.path.insert(0, "/Users/javendai/Documents/workplace/Multi-source_RAG_Agent")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("test_e2e")

ROOT = Path("/Users/javendai/Documents/workplace/Multi-source_RAG_Agent")
REPORT_JSON = ROOT / "test_google_balanced_report.json"
REPORT_MD = ROOT / "test_google_balanced_report.md"
LOG_DIR = ROOT / "test_google_logs"

TEST_QUERIES = [
    {"id": 1, "query": "nubia 英国站3月份DTC渠道的GMV和订单量", "difficulty": "easy", "type": "data"},
    {"id": 4, "query": "nubia 日本市场的KOL合作名单和已确认的博主合作进度", "difficulty": "medium", "type": "data"},
    {"id": 44, "query": "REDMAGIC 11 Pro 各站点首发销量和DTC渠道表现", "difficulty": "easy", "type": "data"},
    {"id": 48, "query": "REDMAGIC 品牌在游戏手机赛道的竞品格局和用户认知度", "difficulty": "hard", "type": "concept"},
    {"id": 55, "query": "nubia 今年的社媒营销预算分配和各平台投放效果", "difficulty": "hard", "type": "data"},
]

SCENARIOS = [
    {
        "name": "phase3_api_routing",
        "label": "Phase 3 API Routing",
        "overrides": {
            "google_enable_native_slide_size_gate": True,
            "google_enable_conditional_file_rerank": True,
            "google_enable_type_pools": True,
            "google_enable_gmail_isolation": True,
            "google_type_pool_sheet_workers": 30,
            "google_type_pool_doc_workers": 30,
            "google_type_pool_slide_workers": 30,
            "google_type_pool_gmail_workers": 14, # NOT USED, shared pool handles gmail
        },
    },
    {
        "name": "baseline",
        "label": "Phase 1 Baseline",
        "overrides": {
            "google_enable_native_slide_size_gate": False,
            "google_enable_conditional_file_rerank": False,
            "google_enable_type_pools": False,
            "google_enable_gmail_isolation": False,
        },
    },
    {
        "name": "phase2",
        "label": "Phase 2",
        "overrides": {
            "google_enable_native_slide_size_gate": True,
            "google_enable_conditional_file_rerank": True,
            "google_enable_type_pools": False,
            "google_enable_gmail_isolation": False,
        },
    },
    {
        "name": "phase3",
        "label": "Phase 3",
        "overrides": {
            "google_enable_native_slide_size_gate": True,
            "google_enable_conditional_file_rerank": True,
            "google_enable_type_pools": True,
            "google_enable_gmail_isolation": True,
        },
    },
    {
        "name": "phase3_unlimited",
        "label": "Phase 3 Unlimited Concurrency",
        "overrides": {
            "google_enable_native_slide_size_gate": True,
            "google_enable_conditional_file_rerank": True,
            "google_enable_type_pools": True,
            "google_enable_gmail_isolation": True,
            "google_type_pool_sheet_workers": 30,
            "google_type_pool_doc_workers": 30,
            "google_type_pool_slide_workers": 30,
            "google_type_pool_gmail_workers": 30,
        },
    },
]


@dataclass
class QueryResult:
    query_id: int
    query: str
    total_ms: float = 0.0
    top_chunks: List[Any] = field(default_factory=list)
    loops_used: int = 0
    error: str = ""


def _quantile(values: List[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * ratio))]


def _scenario_config(overrides: Dict[str, Any]):
    from tools.core.models import RetrieveConfig, SourceAgentConfig

    rc = RetrieveConfig(preset="balanced", enabled_sources=["google"])
    sa_cfg = rc.effective_config_for("google")
    base = SourceAgentConfig(
        source_timeout_s=60.0,
        google_download_timeout_s=15.0,
        google_api_timeout_s=15.0,
        source_top_k=sa_cfg.source_top_k,
        min_loops=sa_cfg.min_loops,
        max_loops=sa_cfg.max_loops,
        sub_query_top_n=sa_cfg.sub_query_top_n,
        google_enable_gmail=sa_cfg.google_enable_gmail,
        google_enable_gmail_attachments=sa_cfg.google_enable_gmail_attachments,
        google_enable_sheet_filter=sa_cfg.google_enable_sheet_filter,
        google_enable_excel_filter=sa_cfg.google_enable_excel_filter,
        google_enable_fulltext_search=sa_cfg.google_enable_fulltext_search,
        google_enable_native_slide_size_gate=sa_cfg.google_enable_native_slide_size_gate,
        google_enable_conditional_file_rerank=sa_cfg.google_enable_conditional_file_rerank,
        google_enable_type_pools=sa_cfg.google_enable_type_pools,
        google_enable_gmail_isolation=sa_cfg.google_enable_gmail_isolation,
        google_order_by_mode=sa_cfg.google_order_by_mode,
        google_per_file_timeout_s=sa_cfg.google_per_file_timeout_s,
        google_native_doc_size_gate_bytes=sa_cfg.google_native_doc_size_gate_bytes,
        google_native_doc_outline_threshold_bytes=sa_cfg.google_native_doc_outline_threshold_bytes,
        google_native_slide_size_gate_bytes=sa_cfg.google_native_slide_size_gate_bytes,
        google_sheet_chunk_cap=sa_cfg.google_sheet_chunk_cap,
        google_doc_chunk_cap=sa_cfg.google_doc_chunk_cap,
        google_slide_chunk_cap=sa_cfg.google_slide_chunk_cap,
        google_file_rerank_min_chunks_sheet=sa_cfg.google_file_rerank_min_chunks_sheet,
        google_file_rerank_min_chunks_slide=sa_cfg.google_file_rerank_min_chunks_slide,
        google_type_pool_sheet_workers=sa_cfg.google_type_pool_sheet_workers,
        google_type_pool_doc_workers=sa_cfg.google_type_pool_doc_workers,
        google_type_pool_slide_workers=sa_cfg.google_type_pool_slide_workers,
        google_type_pool_gmail_workers=sa_cfg.google_type_pool_gmail_workers,
        google_gmail_attachment_max_files=sa_cfg.google_gmail_attachment_max_files,
        google_doc_search_per_sq=sa_cfg.google_doc_search_per_sq,
        google_slide_search_per_sq=sa_cfg.google_slide_search_per_sq,
        google_sheet_search_per_sq=sa_cfg.google_sheet_search_per_sq,
        google_max_gmail_threads_per_subquery=sa_cfg.google_max_gmail_threads_per_subquery,
        google_gmail_rerank_top_n=sa_cfg.google_gmail_rerank_top_n,
        google_doc_candidates_per_sq=sa_cfg.google_doc_candidates_per_sq,
        google_slide_candidates_per_sq=sa_cfg.google_slide_candidates_per_sq,
        google_sheet_candidates_per_sq=sa_cfg.google_sheet_candidates_per_sq,
    )
    return replace(base, **overrides)


async def run_single(qinfo: dict, scenario_name: str, sa_cfg) -> QueryResult:
    from agents.source_agent_base import Focus
    from agents.google.google_source_agent import GoogleSourceAgent

    r = QueryResult(query_id=qinfo["id"], query=qinfo["query"])
    started = time.perf_counter()

    try:
        agent = GoogleSourceAgent(cfg=sa_cfg)
        logger.info("\n" + "=" * 80)
        logger.info("[SCENARIO] %s", scenario_name)
        logger.info("[TEST START] Q%d | %s | difficulty=%s | type=%s",
                    qinfo["id"], qinfo["query"], qinfo["difficulty"], qinfo["type"])
        logger.info("[TEST CONFIG] source_timeout=%.0fs | download=%.0fs | api=%.0fs | top_k=%d | sub_query_top_n=%d",
                    sa_cfg.source_timeout_s, sa_cfg.google_download_timeout_s,
                    sa_cfg.google_api_timeout_s, sa_cfg.source_top_k, sa_cfg.sub_query_top_n)
        logger.info("[TEST CONFIG] gmail=%s | attachments=%s | order_by=%s | max_threads=%d | rerank_top_n=%d",
                    sa_cfg.google_enable_gmail, sa_cfg.google_enable_gmail_attachments,
                    sa_cfg.google_order_by_mode, sa_cfg.google_max_gmail_threads_per_subquery,
                    sa_cfg.google_gmail_rerank_top_n)

        focus = Focus(focus_id="f1", text=qinfo["query"], targets=["google"])
        result = await agent.run([focus])
        r.total_ms = result.elapsed_s * 1000.0
        r.top_chunks = result.top_chunks
        r.loops_used = result.loops_used
        logger.info("[TEST RESULT] Q%d | top_chunks=%d | loops=%d | elapsed=%.0fms",
                    qinfo["id"], len(result.top_chunks), result.loops_used, r.total_ms)
        type_stats: Dict[str, int] = {}
        for c in result.top_chunks:
            meta = c.metadata or {}
            ctype = meta.get("type", "unknown")
            type_stats[ctype] = type_stats.get(ctype, 0) + 1
        logger.info("[TEST RESULT] Q%d | chunk_type_stats=%s", qinfo["id"], type_stats)
        for i, c in enumerate(result.top_chunks, 1):
            meta = c.metadata or {}
            logger.info("[TEST CHUNK] Q%d #%d | score=%.3f | tier=%s | type=%s | title=%s | content=%.200s",
                        qinfo["id"], i,
                        c.score if c.score is not None else 0.0,
                        c.tier or "?",
                        meta.get("type", "?"),
                        meta.get("source_name", meta.get("file_title", "?")),
                        c.content or "")
    except Exception as e:
        logger.exception("[TEST ERROR] Q%d failed: %s", qinfo["id"], e)
        r.error = str(e)
        r.total_ms = (time.perf_counter() - started) * 1000.0
    return r


def _parse_log(log_path: Path) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {}
    current_qid: int | None = None
    current_scenario: str | None = None
    for line in log_path.read_text(encoding="utf-8").splitlines():
        m = re.search(r"\[SCENARIO\]\s+(\S+)", line)
        if m:
            current_scenario = m.group(1)
            continue
        m = re.search(r"\[TEST START\] Q(\d+)", line)
        if m and current_scenario:
            current_qid = int(m.group(1))
            metrics.setdefault(str(current_qid), {
                "scenario": current_scenario,
                "phase_b_ms": 0.0,
                "batch_total_ms": 0.0,
                "file_read": {},
                "wait_done": {},
                "file_level_rerank": {"count": 0, "ms": []},
                "top_chunks": [],
                "tiers": {"critical": 0, "important": 0, "noise": 0},
                "selected_count": 0,
            })
            continue
        if current_qid is None:
            continue
        bucket = metrics[str(current_qid)]
        if "TIMING google.phase_b.metadata_prefetch" in line:
            mm = re.search(r"ms=(\d+(?:\.\d+)?)", line)
            if mm:
                bucket["phase_b_ms"] = float(mm.group(1))
        elif "TIMING google.batch_total" in line:
            mm = re.search(r"ms=(\d+(?:\.\d+)?)", line)
            if mm:
                bucket["batch_total_ms"] = float(mm.group(1))
        elif "TIMING google.file_read type=" in line:
            mm = re.search(r"type=(\w+).*?ms=(\d+(?:\.\d+)?)", line)
            if mm:
                bucket["file_read"].setdefault(mm.group(1), []).append(float(mm.group(2)))
        elif "TIMING google.wait_done" in line:
            mm = re.search(r"type=(\w+).*?ms=(\d+(?:\.\d+)?)", line)
            if mm:
                bucket["wait_done"].setdefault(mm.group(1), []).append(float(mm.group(2)))
        elif "[google] file-level rerank" in line:
            mm = re.search(r"ms=(\d+(?:\.\d+)?)", line)
            if mm:
                bucket["file_level_rerank"]["count"] += 1
                bucket["file_level_rerank"]["ms"].append(float(mm.group(1)))
        elif "[TEST CHUNK]" in line:
            mm = re.search(r"tier=(\w+).*?title=(.*?) \| content=", line)
            if mm:
                tier = mm.group(1)
                title = mm.group(2).strip()
                bucket["top_chunks"].append(title)
                bucket["selected_count"] += 1
                if tier in bucket["tiers"]:
                    bucket["tiers"][tier] += 1
    for bucket in metrics.values():
        for key in ("file_read", "wait_done"):
            summary = {}
            for ftype, vals in bucket[key].items():
                summary[ftype] = {
                    "count": len(vals),
                    "p50": _quantile(vals, 0.50),
                    "p95": _quantile(vals, 0.95),
                    "max": max(vals) if vals else 0.0,
                }
            bucket[key] = summary
        rerank_ms = bucket["file_level_rerank"]["ms"]
        bucket["file_level_rerank"] = {
            "count": bucket["file_level_rerank"]["count"],
            "total_ms": sum(rerank_ms),
            "p50": _quantile(rerank_ms, 0.50),
            "p95": _quantile(rerank_ms, 0.95),
            "max": max(rerank_ms) if rerank_ms else 0.0,
        }
        bucket["top_chunk_titles"] = sorted({t for t in bucket["top_chunks"] if t})[:10]
        del bucket["top_chunks"]
    return metrics


def _compare_reports(all_reports: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    comparisons: Dict[str, Any] = {}
    baseline = all_reports["baseline"]
    for q in baseline:
        base = baseline[q]
        comparisons[q] = {}
        for scenario_name in ("phase2", "phase3", "phase3_unlimited", "phase3_api_routing"):
            curr = all_reports[scenario_name].get(q, {})
            comparisons[q][scenario_name] = {
                "elapsed_ms_delta": curr.get("elapsed_ms", 0.0) - base.get("elapsed_ms", 0.0),
                "batch_total_ms_delta": curr.get("batch_total_ms", 0.0) - base.get("batch_total_ms", 0.0),
                "selected_count_delta": curr.get("selected_count", 0) - base.get("selected_count", 0),
                "critical_delta": curr.get("tiers", {}).get("critical", 0) - base.get("tiers", {}).get("critical", 0),
                "important_delta": curr.get("tiers", {}).get("important", 0) - base.get("tiers", {}).get("important", 0),
                "top_titles": curr.get("top_chunk_titles", []),
            }
    return comparisons


def _render_report(report: Dict[str, Any]) -> str:
    lines = ["# Google Balanced Optimization Report", ""]
    for scenario in SCENARIOS:
        name = scenario["name"]
        lines.append(f"## {scenario['label']}")
        for q in TEST_QUERIES:
            bucket = report["scenarios"][name].get(str(q["id"]), {})
            lines.append(
                f"- Q{q['id']}: elapsed={bucket.get('elapsed_ms', 0):.0f}ms "
                f"batch_total={bucket.get('batch_total_ms', 0):.1f}ms "
                f"phase_b={bucket.get('phase_b_ms', 0):.1f}ms "
                f"rerank_count={bucket.get('file_level_rerank', {}).get('count', 0)} "
                f"tiers={bucket.get('tiers', {})}"
            )
        lines.append("")
    lines.append("## Comparison")
    for q in TEST_QUERIES:
        diff = report["comparisons"].get(str(q["id"]), {})
        lines.append(f"- Q{q['id']}:")
        for phase in ("phase2", "phase3", "phase3_unlimited", "phase3_api_routing"):
            d = diff.get(phase, {})
            lines.append(
                f"  - {phase}: elapsed_delta={d.get('elapsed_ms_delta', 0):.0f}ms "
                f"batch_delta={d.get('batch_total_ms_delta', 0):.1f}ms "
                f"selected_delta={d.get('selected_count_delta', 0)} "
                f"critical_delta={d.get('critical_delta', 0)} "
                f"important_delta={d.get('important_delta', 0)}"
            )
    return "\n".join(lines) + "\n"


async def run_scenario(scenario: Dict[str, Any]) -> Dict[str, Any]:
    scenario_name = scenario["name"]
    sa_cfg = _scenario_config(scenario["overrides"])
    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"{scenario_name}.log"
    if log_path.exists():
        log_path.unlink()
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root_logger = logging.getLogger()
    root_logger.addHandler(file_handler)
    try:
        results = []
        for q in TEST_QUERIES:
            results.append(await run_single(q, scenario_name, sa_cfg))
    finally:
        root_logger.removeHandler(file_handler)
        file_handler.close()

    parsed = _parse_log(log_path)
    for r in results:
        parsed.setdefault(str(r.query_id), {})
        parsed[str(r.query_id)]["elapsed_ms"] = r.total_ms
        parsed[str(r.query_id)]["error"] = r.error
        parsed[str(r.query_id)]["selected_count"] = len(r.top_chunks)
    return parsed


async def main():
    all_reports: Dict[str, Dict[str, Any]] = {}
    for scenario in SCENARIOS:
        logger.info("Running scenario: %s", scenario["name"])
        all_reports[scenario["name"]] = await run_scenario(scenario)

    final_report = {
        "scenarios": all_reports,
        "comparisons": _compare_reports(all_reports),
        "generated_at": time.time(),
    }
    REPORT_JSON.write_text(json.dumps(final_report, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_MD.write_text(_render_report(final_report), encoding="utf-8")
    logger.info("Report written to %s and %s", REPORT_JSON, REPORT_MD)


if __name__ == "__main__":
    asyncio.run(main())
