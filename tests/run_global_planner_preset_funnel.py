"""Compare Global Planner models across Feishu-only retrieve presets.

This is a diagnostic harness: it writes one body-free artifact after each cell
so a provider or console failure cannot erase completed E2E funnel evidence.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.feishu_runner import LogCapture, parse_logs
from eval.feishu_scope_first_runner import _inconclusive_reasons, _replan
from tools.core.benchmark_contract import evaluate_resource_first_case
from tools.core.evidence_role_funnel import RoleFunnelObserver, activate, reset
from tools.core.models import RetrieveConfig, SourceAgentConfig, THINKING_FLASH


def _body_free(record: Mapping[str, Any]) -> dict[str, Any]:
    safe = dict(record)
    payload = dict(safe.get("raw_payload") or {})
    redacted = []
    for chunk in payload.get("chunks") or []:
        item = dict(chunk)
        text = str(item.pop("text", "") or "")
        item["content_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""
        item["content_chars"] = len(text)
        redacted.append(item)
    payload["chunks"] = redacted
    safe["raw_payload"] = payload
    return safe


async def _retrieve(
    query: str, preset: str, planner_model: str, source_timeout_s: float,
) -> dict[str, Any]:
    from google.adk.runners import InMemoryRunner
    from google.genai import types as genai_types
    from retrieve_agent import build_retrieve_agent
    from tools.core.models import SourceConfig

    cfg = RetrieveConfig(
        preset=preset,
        enabled_sources=["feishu"],
        source_configs={"feishu": SourceAgentConfig(source_timeout_s=source_timeout_s)},
    )
    cfg.global_planner.model = planner_model
    cfg.global_planner.thinking_config = THINKING_FLASH
    cfg.global_planner.timeout_s = 45.0
    cfg.eval_model_override = "deepseek/deepseek-flash"
    cfg.eval_thinking = THINKING_FLASH
    agent = build_retrieve_agent(
        config=cfg, source_config=SourceConfig(load_from_env=True),
        name=f"planner_funnel_{preset}_{uuid.uuid4().hex[:8]}",
    )
    runner = InMemoryRunner(agent=agent, app_name="global_planner_funnel")
    user_id = f"funnel_{uuid.uuid4().hex[:8]}"
    session = await runner.session_service.create_session(app_name="global_planner_funnel", user_id=user_id)
    capture = LogCapture()
    root_logger = logging.getLogger()
    root_logger.addHandler(capture)
    record: dict[str, Any] = {
        "query": query, "preset": preset, "planner_model": planner_model,
        "planner_thinking": "disabled", "eval_model": cfg.eval_model_override,
        "eval_thinking": "disabled", "source_timeout_s": source_timeout_s,
        "raw_payload": None, "retrieval_trace": None,
        "error": None,
    }
    started = time.monotonic()
    try:
        content = genai_types.Content(role="user", parts=[genai_types.Part(text=query)])
        async for event in runner.run_async(user_id=user_id, session_id=session.id, new_message=content):
            delta = event.actions.state_delta if event.actions else None
            if not delta:
                continue
            for key in ("retrieval_result", "retrieval_trace"):
                raw = delta.get(key)
                if raw:
                    try:
                        record["raw_payload" if key == "retrieval_result" else "retrieval_trace"] = json.loads(raw)
                    except Exception as exc:  # retain a diagnosable failure, not a silent empty payload
                        record["error"] = f"{key}_json_error:{type(exc).__name__}"
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        logging.getLogger(__name__).exception("retrieve run failed")
    finally:
        root_logger.removeHandler(capture)
        await agent.aclose()
    record["e2e_elapsed_s"] = round(time.monotonic() - started, 3)
    record["log_raw"] = capture.records
    record["log_parsed"] = parse_logs(capture.records)
    return record


def _funnel_summary(record: Mapping[str, Any], expected_chain: list[Mapping[str, Any]]) -> dict[str, Any]:
    observer = RoleFunnelObserver(expected_chain)
    # Observation must surround the call; this fallback only makes an empty schema explicit.
    # The caller supplies the populated observer instead.
    del observer
    events = list((record.get("retrieval_trace") or {}).get("events") or [])
    counts = Counter(str(event.get("event") or "") for event in events if isinstance(event, Mapping))
    response_rows = [event for event in events if isinstance(event, Mapping) and event.get("event") == "partial_eval_response_shape"]
    return {
        "event_counts": dict(sorted(counts.items())),
        "partial_eval": [{
            "batch_id": event.get("batch_id"), "focus_group_count": event.get("focus_group_count"),
            "tier_count": event.get("tier_count"), "no_qualifying_group_count": event.get("no_qualifying_group_count"),
            "submitted_count": len(event.get("submitted_chunk_ids") or []),
        } for event in response_rows],
    }


async def run_cell(
    case: Mapping[str, Any], preset: str, planner_model: str, source_timeout_s: float,
) -> dict[str, Any]:
    observer = RoleFunnelObserver(case.get("expected_chain") or [])
    token = activate(observer)
    try:
        record = await _retrieve(str(case["user_query"]), preset, planner_model, source_timeout_s)
    finally:
        reset(token)
    chunks = list((record.get("raw_payload") or {}).get("chunks") or [])
    verdict = evaluate_resource_first_case(case, final_chunks=chunks, replan_decision=_replan(record.get("retrieval_trace")))
    artifact = _body_free(record)
    funnel = _funnel_summary(record, case.get("expected_chain") or [])
    funnel["roles"] = observer.summary().get("roles") or []
    funnel["read_lineage_events"] = observer.summary().get("read_lineage_events") or []
    artifact["evidence_role_funnel"] = funnel
    reasons = _inconclusive_reasons(record)
    artifact["benchmark"] = {
        "case_id": case["case_id"], "semantic_passed": verdict.passed,
        "replan": _replan(record.get("retrieval_trace")), "failures": list(verdict.failures),
        "inconclusive": bool(reasons), "inconclusive_reasons": reasons,
    }
    return artifact


def _load_case(manifest: Path, case_id: str) -> Mapping[str, Any]:
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    for case in payload.get("cases") or []:
        if case.get("case_id") == case_id:
            return case
    raise ValueError(f"case not found: {case_id}")


async def main_async(args: argparse.Namespace) -> None:
    case = _load_case(args.manifest, args.case_id)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary: list[dict[str, Any]] = []
    for model in args.models:
        label = model.rsplit("-", 1)[-1]
        for preset in args.presets:
            path = args.output_dir / f"{label}__{preset}.json"
            if args.resume and path.exists():
                artifact = json.loads(path.read_text(encoding="utf-8"))
            else:
                artifact = await run_cell(case, preset, model, args.source_timeout)
                path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
            trace = artifact.get("retrieval_trace") or {}
            events = trace.get("events") or []
            planner = [event for event in events if isinstance(event, Mapping) and event.get("event") == "planner_focus"]
            summary.append({
                "model": model, "preset": preset, "artifact": str(path),
                "e2e_elapsed_s": artifact.get("e2e_elapsed_s"),
                "latency": (artifact.get("log_parsed") or {}).get("latency"),
                "focuses": len(planner), "primary_sub_queries": sum(len(event.get("query_ids") or []) for event in planner),
                "final_chunks": len((artifact.get("raw_payload") or {}).get("chunks") or []),
                "benchmark": artifact.get("benchmark"),
                "event_counts": (artifact.get("evidence_role_funnel") or {}).get("event_counts"),
            })
            print(f"checkpoint {label}/{preset}", flush=True)
    payload = {"case_id": case["case_id"], "query": case["user_query"], "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "cells": summary}
    (args.output_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("completed", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "tests/artifacts/p3_scope_first_v2/07_promoted_benchmark.json")
    parser.add_argument("--case-id", default="v2_pilot_01")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--models", nargs="+", default=["deepseek/deepseek-flash", "deepseek/deepseek-v4-pro"])
    parser.add_argument("--presets", nargs="+", default=["lite", "balanced", "deep_dive"])
    parser.add_argument("--source-timeout", type=float, default=180.0)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    os.environ["RAG_ENABLED_SOURCES"] = "feishu"
    os.environ["RAG_FEISHU_SOURCE_TIMEOUT_S"] = "180"
    asyncio.run(main_async(args))
