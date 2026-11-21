"""Run one body-free Feishu-only release smoke check.

This script deliberately keeps live business data out of the repository.  Its
JSON artifact contains only hashes, counts, route/event labels, timings, and
error categories; it never serializes the query, document URLs, chunks, or
credentials.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SMOKE_QUERY = "本周末 Nubia Z80 Ultra 的净销量和净销售额分别是多少？"
SAFE_ROUTES = {"mcp", "mcp_fallback", "block_api", "table", "direct", "wiki"}
MCP_PROTOCOL_RE = re.compile(r"(?:unauthorized|jsondecodeerror|expecting value|non-json)", re.IGNORECASE)


class _ErrorLabelCapture(logging.Handler):
    """Count known protocol failures without retaining log messages."""

    def __init__(self) -> None:
        super().__init__()
        self.labels: Counter[str] = Counter()

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        mcp_call = "mcp" in record.name.lower() or "mcp" in record.funcName.lower() or "mcp" in message.lower()
        if mcp_call and MCP_PROTOCOL_RE.search(message):
            self.labels["mcp_protocol_error"] += 1
        elif record.funcName == "_process_io_task" and "unauthorized" in message.lower():
            self.labels["optional_doc_enrichment_unauthorized"] += 1
        elif "_mcp_call pure failed" in message:
            self.labels["mcp_search_call_error"] += 1
        elif "native_primary candidates=0" in message:
            self.labels["mcp_zero_candidates"] += 1
        elif "source_error" in message:
            self.labels["source_error"] += 1
        elif record.levelno >= logging.ERROR:
            self.labels[f"log_{record.levelname.lower()}"] += 1


def _event_summary(trace: Mapping[str, Any]) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    events = [event for event in trace.get("events", []) if isinstance(event, Mapping)]
    names = Counter(str(event.get("event") or "unknown") for event in events)
    routes = Counter(
        str(event.get("route"))
        for event in events
        if str(event.get("route") or "") in SAFE_ROUTES
    )
    errors = Counter(
        str(event.get("event"))
        for event in events
        if "error" in str(event.get("event") or "").lower()
        or "timeout" in str(event.get("event") or "").lower()
    )
    return dict(names), dict(routes), dict(errors)


def build_body_free_summary(
    *, query: str, preset: str = "balanced", elapsed_s: float, raw_payload: Mapping[str, Any], trace: Mapping[str, Any], log_errors: Mapping[str, int],
    runner_event_count: int = 0, state_delta_key_counts: Mapping[str, int] | None = None,
    source_timeout_s: float | None = None,
) -> dict[str, Any]:
    chunks = [item for item in raw_payload.get("chunks", []) if isinstance(item, Mapping)]
    payload_shape = {
        str(key): (len(value) if isinstance(value, (str, list, dict)) else type(value).__name__)
        for key, value in raw_payload.items()
        if key != "chunks"
    }
    trace_shape = {
        str(key): (len(value) if isinstance(value, (str, list, dict)) else type(value).__name__)
        for key, value in trace.items()
        if key != "events"
    }
    chunk_sources = Counter(str(item.get("source_tool") or "") for item in chunks)
    event_counts, route_counts, trace_errors = _event_summary(trace)
    source_only = bool(chunks) and set(chunk_sources) == {"feishu"}
    trace_ok = (
        any("planner" in name for name in event_counts)
        and any("source" in name for name in event_counts)
    )
    error_counts = Counter(trace_errors)
    error_counts.update({str(key): int(value) for key, value in log_errors.items()})
    mcp_ok = not error_counts.get("mcp_protocol_error", 0)
    return {
        "schema": "feishu_release_e2e_summary_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
        "preset": preset,
        "enabled_sources": ["feishu"],
        "planner_model": "deepseek/deepseek-flash",
        "eval_model": "deepseek/deepseek-flash",
        "thinking": "disabled",
        "source_timeout_s": source_timeout_s,
        "e2e_elapsed_s": round(elapsed_s, 3),
        "chunk_count": len(chunks),
        "chunk_source_counts": dict(chunk_sources),
        "payload_shape": payload_shape,
        "trace_shape": trace_shape,
        "event_counts": event_counts,
        "route_counts": route_counts,
        "error_counts": dict(error_counts),
        "runner_event_count": runner_event_count,
        "state_delta_key_counts": dict(state_delta_key_counts or {}),
        "checks": {
            "nonempty_chunks": bool(chunks),
            "feishu_only": source_only,
            "planner_and_source_trace": trace_ok,
            "no_mcp_protocol_error": mcp_ok,
        },
        "passed": bool(source_only and trace_ok and mcp_ok),
    }


async def run_check(
    query: str,
    *,
    preset: str = "balanced",
    source_timeout_s: float | None = None,
) -> dict[str, Any]:
    from google.adk.runners import InMemoryRunner
    from google.genai import types as genai_types
    from feishu_read_tool.oauth_tokens import ensure_valid_project_access_token, token_status
    from retrieve_agent import build_retrieve_agent
    from tools.core.models import RetrieveConfig, SourceAgentConfig, SourceConfig, THINKING_FLASH

    status = token_status()
    if not status.get("access_token_present"):
        raise RuntimeError("Feishu OAuth access token is not configured")
    # Resolve once before module construction, then use explicit injection for
    # this release probe.  This removes ambiguity between a refreshed .env and
    # module-level environment snapshots without exposing the credential.
    uat = ensure_valid_project_access_token()
    if not uat:
        raise RuntimeError("Feishu OAuth access token could not be resolved")

    source_configs = (
        {"feishu": SourceAgentConfig(source_timeout_s=source_timeout_s)}
        if source_timeout_s is not None
        else {}
    )
    cfg = RetrieveConfig(preset=preset, enabled_sources=["feishu"], source_configs=source_configs)
    cfg.global_planner.model = "deepseek/deepseek-flash"
    cfg.global_planner.thinking_config = THINKING_FLASH
    cfg.eval_model_override = "deepseek/deepseek-flash"
    cfg.eval_thinking = THINKING_FLASH
    agent = build_retrieve_agent(
        config=cfg,
        source_config=SourceConfig(feishu_uat=uat, load_from_env=False),
        name=f"release_feishu_{uuid.uuid4().hex[:8]}",
    )
    runner = InMemoryRunner(agent=agent, app_name="release_feishu_e2e")
    user_id = f"release_{uuid.uuid4().hex[:8]}"
    session = await runner.session_service.create_session(
        app_name="release_feishu_e2e", user_id=user_id,
    )
    capture = _ErrorLabelCapture()
    logging.getLogger().addHandler(capture)
    raw_payload: dict[str, Any] = {}
    trace: dict[str, Any] = {}
    runner_event_count = 0
    state_delta_key_counts: Counter[str] = Counter()
    started = time.monotonic()
    try:
        content = genai_types.Content(role="user", parts=[genai_types.Part(text=query)])
        async for event in runner.run_async(user_id=user_id, session_id=session.id, new_message=content):
            runner_event_count += 1
            state = event.actions.state_delta if event.actions else None
            if not state:
                continue
            state_delta_key_counts.update(str(key) for key in state)
            for key, target in (("retrieval_result", raw_payload), ("retrieval_trace", trace)):
                if state.get(key):
                    try:
                        target.clear()
                        target.update(json.loads(state[key]))
                    except (TypeError, json.JSONDecodeError):
                        capture.labels[f"{key}_json_error"] += 1
    finally:
        logging.getLogger().removeHandler(capture)
        await agent.aclose()
    return build_body_free_summary(
        query=query,
        preset=preset,
        elapsed_s=time.monotonic() - started,
        raw_payload=raw_payload,
        trace=trace,
        log_errors=capture.labels,
        runner_event_count=runner_event_count,
        state_delta_key_counts=state_delta_key_counts,
        source_timeout_s=cfg.effective_config_for("feishu").source_timeout_s,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Feishu-only body-free release E2E check")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "tests" / "artifacts" / "release_e2e")
    parser.add_argument("--preset", choices=("lite", "balanced", "deep_dive"), default="balanced")
    parser.add_argument(
        "--source-timeout-s",
        type=float,
        help="Optional Feishu source wall in seconds. Omit to preserve the selected preset.",
    )
    parser.add_argument(
        "--query-artifact",
        type=Path,
        help="Local JSON artifact with a string 'query' field; its body is never written to output.",
    )
    args = parser.parse_args()
    if args.source_timeout_s is not None and args.source_timeout_s <= 0:
        parser.error("--source-timeout-s must be positive")
    query = SMOKE_QUERY
    if args.query_artifact is not None:
        try:
            artifact = json.loads(args.query_artifact.read_text(encoding="utf-8"))
            query = artifact["query"]
            if not isinstance(query, str) or not query.strip():
                raise ValueError("query must be a non-empty string")
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            parser.error(f"invalid --query-artifact: {type(exc).__name__}")
    try:
        summary = asyncio.run(run_check(
            query, preset=args.preset, source_timeout_s=args.source_timeout_s,
        ))
    except Exception as exc:
        summary = {
            "schema": "feishu_release_e2e_summary_v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
            "preset": args.preset, "enabled_sources": ["feishu"],
            "error_counts": {f"runtime_{type(exc).__name__}": 1},
            "checks": {"nonempty_chunks": False, "feishu_only": False,
                       "planner_and_source_trace": False, "no_mcp_protocol_error": False},
            "passed": False,
        }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"release_feishu_{int(time.time())}.json"
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": summary["passed"], "output": str(output), "checks": summary["checks"]}, ensure_ascii=False))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
