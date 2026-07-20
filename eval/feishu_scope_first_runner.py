"""Run a promoted P3 v2 scope-first pilot through Feishu retrieve-only."""
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
from tools.core.evidence_role_funnel import RoleFunnelObserver, activate as activate_role_funnel, reset as reset_role_funnel


DEFAULT_MANIFEST = ROOT / "tests" / "artifacts" / "p3_scope_first_v2" / "07_promoted_benchmark.json"
DEFAULT_OUTPUT = ROOT / "tests" / "artifacts" / "p3_scope_first_v2_runs"


def _replan(trace: Mapping[str, Any] | None) -> str:
    for event in (trace or {}).get("events", []):
        if event.get("event") == "plan_eval" and any(item.get("gap_type") == "upstream_evidence_needed" for item in event.get("focus_decisions") or [] if isinstance(item, Mapping)):
            return "upstream_evidence_needed"
    return "none"


def _read_timeout(record: Mapping[str, Any]) -> bool:
    text = "\n".join(str(item) for item in record.get("log_raw") or [])
    if any(marker in text for marker in ("ReadTimeout", "Read timed out", "per-doc timeout")):
        return True
    return any(event.get("event") == "timeout" for event in (record.get("retrieval_trace") or {}).get("events") or [] if isinstance(event, Mapping))


def _retry_is_quiescent(record: Mapping[str, Any]) -> bool:
    events = (record.get("retrieval_trace") or {}).get("events") or []
    drains = [event for event in events if isinstance(event, Mapping) and event.get("event") == "cancel_drain"]
    return not drains or all(int(event.get("remaining_sync_io") or 0) == 0 for event in drains)


def _body_free(record: Mapping[str, Any]) -> dict[str, Any]:
    safe = dict(record)
    payload = dict(safe.get("raw_payload") or {})
    chunks = []
    for chunk in payload.get("chunks") or []:
        item = dict(chunk)
        text = str(item.pop("text", "") or "")
        item["content_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""
        item["content_chars"] = len(text)
        chunks.append(item)
    payload["chunks"] = chunks
    safe["raw_payload"] = payload
    return safe


async def _once(
    case: Mapping[str, Any], preset: str, retries_after_initial: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    record: dict[str, Any] = {}
    attempt_records: list[dict[str, Any]] = []
    for attempt in range(1, retries_after_initial + 2):
        observer = RoleFunnelObserver(case.get("expected_chain") or [])
        token = activate_role_funnel(observer)
        try:
            record = await run_case({"test_id": case["case_id"], "query": case["user_query"]}, preset, retrieve_only=True)
        finally:
            reset_role_funnel(token)
        record["evidence_role_funnel"] = observer.summary()
        attempt_record = _body_free(record)
        attempt_record["attempt"] = attempt
        attempt_record["transport_inconclusive"] = _read_timeout(record)
        attempt_records.append(attempt_record)
        if (not _read_timeout(record) or not _retry_is_quiescent(record)
                or attempt == retries_after_initial + 1):
            return record, attempt_records
        await asyncio.sleep(min(5 * attempt, 10))
    return record, attempt_records


async def run_pilot(manifest: Mapping[str, Any], output_dir: Path, *, preset: str = "deep_dive", runs: int = 3, retries_after_initial: int = 2, allow_pending_review: bool = False, resume: bool = False, case_ids: set[str] | None = None) -> dict[str, Any]:
    if manifest.get("format") != "p3_scope_first_benchmark_v2":
        raise ValueError("not a P3 v2 scope-first benchmark manifest")
    cases = list(manifest.get("cases") or [])
    if case_ids:
        cases = [case for case in cases if case.get("case_id") in case_ids]
    pending = [case["case_id"] for case in cases if case.get("review_required") and case.get("review_status") not in {"approved", "approved_with_reduction"}]
    if pending and not allow_pending_review:
        raise ValueError(f"cross-document review pending: {pending}")
    output_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    for case in cases:
        runs_report = []
        for run_number in range(1, runs + 1):
            path = output_dir / f"{case['case_id']}__run{run_number}__{preset}.json"
            if resume and path.exists():
                stored = json.loads(path.read_text(encoding="utf-8"))
                benchmark = dict(stored.get("benchmark") or {})
                runs_report.append({"run": run_number, "passed": bool(benchmark.get("passed")) and not bool(stored.get("error")), "timeout": _read_timeout(stored), "attempts_used": benchmark.get("attempts_used", 0), "failures": list(benchmark.get("failures") or []), "artifact": str(path), "e2e_elapsed_s": stored.get("e2e_elapsed_s")})
                continue
            record, attempt_records = await _once(case, preset, retries_after_initial)
            attempts = len(attempt_records)
            chunks = list((record.get("raw_payload") or {}).get("chunks") or [])
            decision = _replan(record.get("retrieval_trace"))
            verdict = evaluate_resource_first_case(case, final_chunks=chunks, replan_decision=decision)
            artifact = _body_free(record)
            funnel = dict(record.get("evidence_role_funnel") or {})
            events = (record.get("retrieval_trace") or {}).get("events") or []
            read_events = [event for event in events if isinstance(event, Mapping) and event.get("event") == "read"]
            funnel["unique_read_chunks"] = len({str(event.get("chunk_id") or "") for event in read_events if event.get("chunk_id")})
            funnel["unique_documents"] = len({str(event.get("document") or "") for event in read_events if event.get("document")})
            hits: dict[str, set[str]] = {}
            for event in read_events:
                for query_id in event.get("query_ids") or []:
                    hits.setdefault(str(event.get("chunk_id") or ""), set()).add(str(query_id))
            funnel["multi_query_hits"] = {
                chunk_id: sorted(query_ids) for chunk_id, query_ids in hits.items() if len(query_ids) > 1 and chunk_id
            }
            artifact["evidence_role_funnel"] = funnel
            artifact["attempt_records"] = attempt_records
            artifact["attempt_elapsed_s_total"] = sum(
                float(item.get("e2e_elapsed_s") or 0.0) for item in attempt_records
            )
            artifact["benchmark"] = {"case_id": case["case_id"], "run": run_number, "attempts_used": attempts, "replan": decision, "passed": verdict.passed, "failures": list(verdict.failures)}
            path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
            runs_report.append({"run": run_number, "passed": verdict.passed and not bool(record.get("error")), "timeout": _read_timeout(record), "attempts_used": attempts, "failures": list(verdict.failures), "artifact": str(path), "e2e_elapsed_s": record.get("e2e_elapsed_s")})
        complete = sum(1 for item in runs_report if item["passed"])
        reports.append({"case_id": case["case_id"], "case_type": case["case_type"], "chain_complete_runs": complete, "stable": complete >= 2, "timeout_runs": sum(1 for item in runs_report if item["timeout"]), "runs": runs_report})
    summary = {"benchmark": "p3_scope_first_v2", "preset": preset, "runs_per_case": runs, "retries_after_initial": retries_after_initial, "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "reports": reports, "stable_cases": sum(1 for item in reports if item["stable"]), "total_cases": len(reports), "formal_gate_passed": bool(reports) and all(item["stable"] for item in reports)}
    (output_dir / f"summary__{preset}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--preset", default="deep_dive")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--retries-after-initial", type=int, default=2)
    parser.add_argument("--allow-pending-review", action="store_true", help="diagnostic only; formal run requires approved cross-document review")
    parser.add_argument("--resume", action="store_true", help="reuse already-written run artifacts and continue only missing runs")
    parser.add_argument("--case-id", action="append", help="run only the selected case id; repeatable")
    parser.add_argument("--source-timeout", type=float, default=180.0)
    parser.add_argument("--planner-timeout", type=float, default=45.0)
    args = parser.parse_args()
    os.environ["RAG_FEISHU_SOURCE_TIMEOUT_S"] = str(args.source_timeout)
    os.environ["RAG_GLOBAL_PLANNER_TIMEOUT_S"] = str(args.planner_timeout)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    summary = asyncio.run(run_pilot(manifest, args.output_dir, preset=args.preset, runs=args.runs, retries_after_initial=args.retries_after_initial, allow_pending_review=args.allow_pending_review, resume=args.resume, case_ids=set(args.case_id or [])))
    print(f"stable {summary['stable_cases']}/{summary['total_cases']} cases")
    return 0 if summary["formal_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
