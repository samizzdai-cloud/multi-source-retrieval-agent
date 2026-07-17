"""Run the real-query Feishu benchmark and persist compact retrieval evidence.

This runner deliberately calls retrieve-only.  It scores chunks and replan
decisions; answer wording is outside this benchmark's contract.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import hashlib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from eval.feishu_runner import run_case  # noqa: E402
from tools.core.benchmark_contract import evaluate_case, load_query_first_cases  # noqa: E402


FIXTURE = ROOT / "tests" / "fixtures" / "feishu_query_first_benchmark.json"
DEFAULT_OUTPUT = ROOT / "tests" / "artifacts" / "feishu_query_first"


def _observed_replan(trace: dict | None) -> str:
    decisions = []
    for event in (trace or {}).get("events", []):
        if event.get("event") != "plan_eval":
            continue
        decisions.extend(event.get("focus_decisions") or [])
    gap_types = {
        decision.get("gap_type") for decision in decisions
        if isinstance(decision, dict)
    }
    if "upstream_evidence_needed" in gap_types:
        return "upstream_evidence_needed"
    return "none"


def _direct_chunk_ids(trace: dict | None) -> list[str]:
    return [
        str(event["chunk_id"])
        for event in (trace or {}).get("events", [])
        if event.get("event") == "read" and int(event.get("loop", 1)) == 1 and event.get("chunk_id")
    ]


def _chunks_for_ids(chunks: list[dict[str, Any]], ids: list[str]) -> list[dict[str, Any]]:
    """Resolve trace IDs to current chunks for snippet-based benchmark scoring."""
    wanted = set(ids)
    return [chunk for chunk in chunks if str(chunk.get("chunk_id") or "") in wanted]


def _body_free_artifact(record: dict[str, Any]) -> dict[str, Any]:
    """Keep trace metadata locally without persisting document body text."""
    artifact = dict(record)
    payload = dict(record.get("raw_payload") or {})
    chunks = []
    for chunk in payload.get("chunks", []) or []:
        safe = dict(chunk)
        body = str(safe.pop("text", "") or "")
        safe["content_sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest() if body else ""
        safe["content_chars"] = len(body)
        chunks.append(safe)
    if payload:
        payload["chunks"] = chunks
    artifact["raw_payload"] = payload or None
    return artifact


def _has_transient_feishu_read_failure(record: dict[str, Any]) -> bool:
    logs = "\n".join(str(line) for line in (record.get("log_raw") or []))
    return any(marker in logs for marker in ("per-doc timeout", "ReadTimeout", "Read timed out"))


async def _run_case_with_retry(case: dict[str, Any], preset: str, attempts: int) -> tuple[dict[str, Any], int]:
    """Retry only transport/read failures; never retry a semantic verdict."""
    attempts = max(1, attempts)
    record: dict[str, Any] = {}
    for attempt in range(1, attempts + 1):
        record = await run_case({"test_id": case["id"], "query": case["user_query"]}, preset, retrieve_only=True)
        if not _has_transient_feishu_read_failure(record) or attempt == attempts:
            return record, attempt
        await asyncio.sleep(min(5.0 * attempt, 10.0))
    return record, attempts


async def main(case_ids: set[str] | None, preset: str, output_dir: Path, retry_attempts: int = 2) -> int:
    cases = load_query_first_cases(FIXTURE)
    if case_ids:
        cases = [case for case in cases if case["id"] in case_ids]
    if not cases:
        raise SystemExit("No benchmark cases selected")
    output_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    reports: list[dict[str, Any]] = []
    for index, case in enumerate(cases, 1):
        record, attempts_used = await _run_case_with_retry(case, preset, retry_attempts)
        payload = record.get("raw_payload") or {}
        payload_chunks = list(payload.get("chunks", []) or [])
        final_ids = [str(chunk.get("chunk_id")) for chunk in payload_chunks if chunk.get("chunk_id")]
        trace = record.get("retrieval_trace")
        direct_ids = _direct_chunk_ids(trace)
        verdict = evaluate_case(
            case,
            direct_chunk_ids=direct_ids,
            final_chunk_ids=final_ids,
            replan_decision=_observed_replan(trace),
            direct_chunks=_chunks_for_ids(payload_chunks, direct_ids),
            final_chunks=payload_chunks,
        )
        record["benchmark"] = {
            "case_id": case["id"],
            "direct_chunk_ids": direct_ids,
            "final_chunk_ids": final_ids,
            "observed_replan": _observed_replan(trace),
            "attempts_used": attempts_used,
            "passed": verdict.passed,
            "failures": list(verdict.failures),
        }
        record["query_provenance"] = case["provenance"]
        path = output_dir / f"{case['id']}__{preset}.json"
        path.write_text(json.dumps(_body_free_artifact(record), ensure_ascii=False, indent=2), encoding="utf-8")
        status = "PASS" if verdict.passed and not record.get("error") else "FAIL"
        failures += status == "FAIL"
        print(f"[{index}/{len(cases)}] {case['id']} {status} replan={record['benchmark']['observed_replan']} chunks={len(final_ids)} → {path}")
        for failure in verdict.failures:
            print(f"  - {failure}")
        if record.get("error"):
            print(f"  - runtime error: {record['error']}")
        reports.append({
            "case_id": case["id"],
            "passed": verdict.passed and not bool(record.get("error")),
            "runtime_error": record.get("error"),
            "observed_replan": record["benchmark"]["observed_replan"],
            "final_chunk_count": len(final_ids),
            "trace_event_counts": (trace or {}).get("event_counts", {}),
            "e2e_elapsed_s": record.get("e2e_elapsed_s"),
            "attempts_used": attempts_used,
            "failures": list(verdict.failures),
            "artifact": str(path),
        })

    # Completion hook: a body-free report suitable for monitoring/CI.  The
    # individual artifacts retain the trace; this file only aggregates results.
    passed = sum(1 for report in reports if report["passed"])
    summary = {
        "benchmark": "feishu_query_first",
        "preset": preset,
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total": len(reports),
        "passed": passed,
        "failed": len(reports) - passed,
        "pass_rate": round(passed / len(reports), 4) if reports else 0.0,
        "replan_counts": {decision: sum(1 for report in reports if report["observed_replan"] == decision)
                          for decision in ("none", "query_refinement", "upstream_evidence_needed")},
        "reports": reports,
    }
    summary_path = output_dir / f"summary__{preset}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[hook] summary {passed}/{len(reports)} passed → {summary_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--preset", default="deep_dive")
    parser.add_argument("--cases", default="", help="Comma-separated benchmark IDs")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--source-timeout", type=float, default=180.0,
                        help="Feishu source stage timeout for real benchmark runs")
    parser.add_argument("--planner-timeout", type=float, default=45.0,
                        help="Global Planner timeout for real benchmark runs")
    parser.add_argument("--eval-layer-mode", choices=("rule", "llm_tool"), default="llm_tool",
                        help="Deep-dive benchmark default: configured DeepSeek global evidence evaluation")
    parser.add_argument("--retry-attempts", type=int, default=2,
                        help="Retry only transient Feishu document-read timeouts")
    args = parser.parse_args()
    os.environ["RAG_FEISHU_SOURCE_TIMEOUT_S"] = str(args.source_timeout)
    os.environ["RAG_GLOBAL_PLANNER_TIMEOUT_S"] = str(args.planner_timeout)
    os.environ["RAG_EVAL_LAYER_MODE"] = args.eval_layer_mode
    selected = {item.strip() for item in args.cases.split(",") if item.strip()} or None
    raise SystemExit(asyncio.run(main(selected, args.preset, Path(args.output_dir), args.retry_attempts)))
