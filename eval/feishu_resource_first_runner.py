"""Run a promoted P3 resource-first pilot through Feishu retrieve-only."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.feishu_runner import run_case
from tools.core.benchmark_contract import evaluate_resource_first_case

DEFAULT_MANIFEST = ROOT / "tests" / "artifacts" / "p3_resource_first" / "07_promoted_benchmark.json"
DEFAULT_OUTPUT = ROOT / "tests" / "artifacts" / "p3_resource_first_runs"


def observed_replan(trace: Mapping[str, Any] | None) -> str:
    decisions = []
    for event in (trace or {}).get("events", []):
        if event.get("event") == "plan_eval":
            decisions.extend(event.get("focus_decisions") or [])
    return "upstream_evidence_needed" if any(item.get("gap_type") == "upstream_evidence_needed" for item in decisions if isinstance(item, Mapping)) else "none"


def retryable_read_failure(record: Mapping[str, Any]) -> bool:
    text = "\n".join(str(line) for line in record.get("log_raw") or [])
    return any(marker in text for marker in ("ReadTimeout", "Read timed out", "per-doc timeout"))


async def run_once(case: Mapping[str, Any], preset: str, retries_after_initial: int) -> tuple[dict[str, Any], int]:
    attempts = max(1, retries_after_initial + 1)
    record: dict[str, Any] = {}
    for number in range(1, attempts + 1):
        record = await run_case({"test_id": case["case_id"], "query": case["user_query"]}, preset, retrieve_only=True)
        if not retryable_read_failure(record) or number == attempts:
            return record, number
        await asyncio.sleep(min(5 * number, 10))
    return record, attempts


def body_free(record: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(record)
    payload = dict(value.get("raw_payload") or {})
    chunks = []
    for chunk in payload.get("chunks") or []:
        safe = dict(chunk)
        text = str(safe.pop("text", "") or "")
        safe["content_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""
        safe["content_chars"] = len(text)
        chunks.append(safe)
    payload["chunks"] = chunks
    value["raw_payload"] = payload
    return value


async def run_pilot(manifest: Mapping[str, Any], output_dir: Path, *, preset: str, runs: int, retries_after_initial: int, allow_pending_review: bool) -> dict[str, Any]:
    if manifest.get("format") != "p3_resource_first_benchmark_v1":
        raise ValueError("not a resource-first benchmark manifest")
    cases = list(manifest.get("cases") or [])
    pending = [item["case_id"] for item in cases if item.get("review_required") and item.get("review_status") != "approved"]
    if pending and not allow_pending_review:
        raise ValueError(f"cross-document review pending: {pending}")
    output_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    for case in cases:
        outcomes = []
        for run_index in range(1, runs + 1):
            record, attempts = await run_once(case, preset, retries_after_initial)
            chunks = list((record.get("raw_payload") or {}).get("chunks") or [])
            decision = observed_replan(record.get("retrieval_trace"))
            verdict = evaluate_resource_first_case(case, final_chunks=chunks, replan_decision=decision)
            artifact = body_free(record)
            artifact["benchmark"] = {"case_id": case["case_id"], "run": run_index, "attempts_used": attempts, "replan": decision, "passed": verdict.passed, "failures": list(verdict.failures)}
            path = output_dir / f"{case['case_id']}__run{run_index}__{preset}.json"
            path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
            outcomes.append({"run": run_index, "passed": verdict.passed and not bool(record.get("error")), "timeout": retryable_read_failure(record), "attempts_used": attempts, "failures": list(verdict.failures), "artifact": str(path), "e2e_elapsed_s": record.get("e2e_elapsed_s")})
        complete = sum(1 for item in outcomes if item["passed"])
        reports.append({"case_id": case["case_id"], "case_type": case["case_type"], "chain_complete_runs": complete, "stable": complete >= 2, "timeout_runs": sum(1 for item in outcomes if item["timeout"]), "runs": outcomes})
    summary = {"benchmark": "p3_resource_first", "preset": preset, "runs_per_case": runs, "retries_after_initial": retries_after_initial, "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "reports": reports, "stable_cases": sum(1 for item in reports if item["stable"]), "total_cases": len(reports), "formal_gate_passed": bool(reports) and all(item["stable"] for item in reports)}
    (output_dir / f"summary__{preset}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--preset", default="deep_dive")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--retries-after-initial", type=int, default=2)
    parser.add_argument("--allow-pending-review", action="store_true", help="diagnostic only; formal runs require approved cross-document review")
    parser.add_argument("--source-timeout", type=float, default=180.0)
    parser.add_argument("--planner-timeout", type=float, default=45.0)
    args = parser.parse_args()
    os.environ["RAG_FEISHU_SOURCE_TIMEOUT_S"] = str(args.source_timeout)
    os.environ["RAG_GLOBAL_PLANNER_TIMEOUT_S"] = str(args.planner_timeout)
    summary = asyncio.run(run_pilot(_load_manifest(args.manifest), args.output_dir, preset=args.preset, runs=args.runs, retries_after_initial=args.retries_after_initial, allow_pending_review=args.allow_pending_review))
    print(f"stable {summary['stable_cases']}/{summary['total_cases']} cases")
    return 0 if summary["formal_gate_passed"] else 1


def _load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
