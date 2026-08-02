"""Google-only evaluation runner.

Reads `google_test_cases.yaml`, drives each case through root_agent_v1
(retrieve + answer) under google_only mode, captures chunks/answer/logs,
and saves a per-(test_id, preset) JSON to cache/.

Usage:
    python eval/google_runner.py                          # all cases × all presets
    python eval/google_runner.py --presets balanced       # one preset
    python eval/google_runner.py --cases Doc1_Q1,Doc2_Q1  # subset
    python eval/google_runner.py --resume                 # skip cases already in cache
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# project root on path so we can import answer_agent / root_agent_config / etc.
sys.path.insert(0, str(Path(__file__).parent.parent))

# load .env
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env", override=False)
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%H:%M:%S",
)
for _noisy in ("httpx", "httpcore", "urllib3", "google.auth"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger("google_runner")

ROOT = Path(__file__).parent.parent
CACHE_DIR = Path(__file__).parent / "cache_google"
CACHE_DIR.mkdir(exist_ok=True)


# ── log capture ───────────────────────────────────────────────────────────────

class LogCapture(logging.Handler):
    """Capture log records during a single agent run."""
    def __init__(self):
        super().__init__()
        self.records: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(f"{record.name}:{record.getMessage()}")
        except Exception:
            pass


# ── log parsing ───────────────────────────────────────────────────────────────

_RE_TIMING_PREPARE  = re.compile(r"TIMING retrieve\.prepare_plan.*?ms=([\d.]+)")
_RE_TIMING_DISPATCH = re.compile(r"TIMING retrieve\.dispatch.*?ms=([\d.]+)")
_RE_TIMING_MERGE    = re.compile(r"TIMING retrieve\.merge.*?ms=([\d.]+)")
_RE_TIMING_EVAL     = re.compile(r"TIMING retrieve\.eval_layer.*?ms=([\d.]+)")
_RE_TIMING_ASSEMBLE = re.compile(r"TIMING retrieve\.assemble.*?ms=([\d.]+)")
_RE_DONE_TOTAL      = re.compile(r"RetrieveAgent: done in (\d+)ms")
_RE_SOURCE_DONE     = re.compile(r"\[(\w+)\] done: loops=(\d+)/(\d+).*?elapsed=([\d.]+)s")
_RE_LOOP_PLAN       = re.compile(r"\[(\w+)\] loop=(\d+) plan produced (\d+) sub_queries")
_RE_LOOP_REPLAN     = re.compile(r"\[(\w+)\] loop=(\d+) replan (\d+) sub_queries")
_RE_SINGLE_PLAN     = re.compile(r"\[(\w+)\] plan produced (\d+) sub_queries")  # single-loop preset
_RE_SANITIZE        = re.compile(r"\[(\w+)\] plan sanitize focus=(\S+) mode=(\S+) before=(\d+) after=(\d+) qtypes=(\[[^\]]*\]) queries=(\[[^\]]*\])")
_RE_STABILIZE       = re.compile(r"\[(\w+)\] plan query stabilized focus=(\S+) before=(\[[^\]]*\]) after=(\[[^\]]*\])")
_RE_FOCUS_SAT       = re.compile(r"FocusBoard: (\w+) SATISFIED by (\w+) \(critical=(\d+) important=(\d+)")
_RE_TIER_DIST       = re.compile(r"\[(\w+)\] eval_tier_dist focus=(\w+) critical=(\d+) important=(\d+) noise=(\d+) untiered=(\d+) total=(\d+)")
_RE_FOCUS_FINAL     = re.compile(r"FocusBoard final: (.+)$")
_RE_ASSEMBLE_PATH   = re.compile(r"TIMING retrieve\.assemble candidates=\d+ picked=(\d+) cap=(\d+) path=(\S+) ms=")


def parse_logs(records: List[str]) -> Dict[str, Any]:
    """Extract latency / loop / focus state from captured logs."""
    out: Dict[str, Any] = {
        "latency": {
            "prepare_plan_ms": None,
            "dispatch_ms": None,
            "merge_ms": None,
            "eval_layer_ms": None,
            "assemble_ms": None,
            "total_ms": None,
        },
        "assemble": {"path": None, "picked": None, "cap": None},
        "per_source": {},      # {source: {loops_used, max_loops, elapsed_s, sub_queries_per_loop, tier_dist}}
        "focus_events": [],    # [{focus_id, source, critical, important}]
        "focus_final": None,
        "sanitize": [],        # [{source, focus_id, mode, before, after, queries}]
        "stabilize": [],       # [{source, focus_id, before, after}]
    }

    for line in records:
        m = _RE_TIMING_PREPARE.search(line)
        if m: out["latency"]["prepare_plan_ms"] = float(m.group(1)); continue

        m = _RE_TIMING_DISPATCH.search(line)
        if m: out["latency"]["dispatch_ms"] = float(m.group(1)); continue

        m = _RE_TIMING_MERGE.search(line)
        if m: out["latency"]["merge_ms"] = float(m.group(1)); continue

        m = _RE_TIMING_EVAL.search(line)
        if m: out["latency"]["eval_layer_ms"] = float(m.group(1)); continue

        m = _RE_TIMING_ASSEMBLE.search(line)
        if m: out["latency"]["assemble_ms"] = float(m.group(1)); continue

        m = _RE_DONE_TOTAL.search(line)
        if m: out["latency"]["total_ms"] = int(m.group(1)); continue

        m = _RE_ASSEMBLE_PATH.search(line)
        if m:
            out["assemble"] = {"picked": int(m.group(1)), "cap": int(m.group(2)), "path": m.group(3)}
            continue

        m = _RE_SOURCE_DONE.search(line)
        if m:
            src, used, mx, el = m.group(1), int(m.group(2)), int(m.group(3)), float(m.group(4))
            ps = out["per_source"].setdefault(src, {"sub_queries_per_loop": [], "tier_dist": []})
            ps["loops_used"] = used
            ps["max_loops"] = mx
            ps["elapsed_s"] = el
            ps["early_exit"] = used < mx
            continue

        m = _RE_LOOP_PLAN.search(line) or _RE_LOOP_REPLAN.search(line)
        if m:
            src, loop_n, n_sq = m.group(1), int(m.group(2)), int(m.group(3))
            ps = out["per_source"].setdefault(src, {"sub_queries_per_loop": [], "tier_dist": []})
            ps["sub_queries_per_loop"].append({"loop": loop_n, "n_sub_queries": n_sq})
            continue

        m = _RE_SINGLE_PLAN.search(line)
        if m:
            src, n_sq = m.group(1), int(m.group(2))
            ps = out["per_source"].setdefault(src, {"sub_queries_per_loop": [], "tier_dist": []})
            # only record if multi-loop variants haven't already populated
            if not ps["sub_queries_per_loop"]:
                ps["sub_queries_per_loop"].append({"loop": 1, "n_sub_queries": n_sq})
            continue

        m = _RE_FOCUS_SAT.search(line)
        if m:
            out["focus_events"].append({
                "focus_id": m.group(1),
                "source": m.group(2),
                "critical": int(m.group(3)),
                "important": int(m.group(4)),
            })
            continue

        m = _RE_TIER_DIST.search(line)
        if m:
            src = m.group(1)
            ps = out["per_source"].setdefault(src, {"sub_queries_per_loop": [], "tier_dist": []})
            ps["tier_dist"].append({
                "focus_id": m.group(2),
                "critical": int(m.group(3)),
                "important": int(m.group(4)),
                "noise": int(m.group(5)),
                "untiered": int(m.group(6)),
                "total": int(m.group(7)),
            })
            continue

        m = _RE_FOCUS_FINAL.search(line)
        if m:
            out["focus_final"] = m.group(1).strip()
            continue

        m = _RE_SANITIZE.search(line)
        if m:
            out["sanitize"].append({
                "source": m.group(1),
                "focus_id": m.group(2),
                "mode": m.group(3),
                "before": int(m.group(4)),
                "after": int(m.group(5)),
                "qtypes": m.group(6),
                "queries": m.group(7),
            })
            continue

        m = _RE_STABILIZE.search(line)
        if m:
            out["stabilize"].append({
                "source": m.group(1),
                "focus_id": m.group(2),
                "before": m.group(3),
                "after": m.group(4),
            })
            continue

    return out


# ── runner ────────────────────────────────────────────────────────────────────

async def run_case(case: Dict[str, Any], preset: str, retrieve_only: bool = False) -> Dict[str, Any]:
    """Run one (test_case, preset) through root_agent_v1 and return full record.

    If retrieve_only=True, skip answer_agent (faster, no rag_answer in output).
    """
    from google.adk.runners import InMemoryRunner
    from google.genai import types as genai_types

    test_id = case["test_id"]
    query = case["query"].strip()

    os.environ["RAG_PRESET"] = preset
    os.environ["RAG_ENABLED_SOURCES"] = "google"

    # Build a fresh agent for this preset (rebuilt per run to honour preset env).
    # Note: answer_agent is a module-level singleton with a parent binding, so we
    # instantiate a fresh Agent instance per run to avoid SequentialAgent reparent error.
    from root_agent_config import build_root_retrieve_agent

    retrieve = build_root_retrieve_agent(name=f"retrieve_{preset}_{uuid.uuid4().hex[:6]}")

    if retrieve_only:
        # Use retrieve_agent directly, no SequentialAgent / answer_agent.
        root = retrieve
    else:
        from google.adk.agents import SequentialAgent
        from google.adk.agents.llm_agent import Agent
        import answer_agent as _aa_mod
        fresh_answer = Agent(
            model="gemini-3-flash-preview",
            name=f"answer_{uuid.uuid4().hex[:6]}",
            description="basis-on chunks 生成中文自然语言回答",
            instruction=_aa_mod._INSTRUCTION,
            before_agent_callback=_aa_mod._mark_answer_started,
            after_agent_callback=_aa_mod._emit_answer_timing,
        )
        root = SequentialAgent(
            name=f"root_{preset}_{uuid.uuid4().hex[:6]}",
            description="google_only eval root",
            sub_agents=[retrieve, fresh_answer],
        )

    runner = InMemoryRunner(agent=root, app_name="google_eval")
    uid = f"eval_{uuid.uuid4().hex[:8]}"
    session = await runner.session_service.create_session(app_name="google_eval", user_id=uid)

    content = genai_types.Content(role="user", parts=[genai_types.Part(text=query)])

    # attach log capture to root logger
    capture = LogCapture()
    logging.getLogger().addHandler(capture)

    record: Dict[str, Any] = {
        "test_id": test_id,
        "preset": preset,
        "query": query,
        "target": case.get("target"),
        "question_type": case.get("question_type"),
        "ground_truth": case.get("ground_truth"),
        "expected": case.get("expected", {}),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "raw_payload": None,
        "rag_answer": "",
        "e2e_elapsed_s": None,
        "error": None,
    }
    answer_parts: List[str] = []
    t0 = time.monotonic()

    try:
        async for event in runner.run_async(user_id=uid, session_id=session.id, new_message=content):
            # collect answer text from answer_agent — filter out:
            #   - retrieve_agent status events: "[retrieve] start ...", "[retrieve] plan ...", ...
            #   - answer_agent timing marker: "[answer] done elapsed_ms=..."
            if event.content and event.content.parts:
                for part in event.content.parts:
                    t = part.text or ""
                    if not t:
                        continue
                    if t.startswith("[retrieve]") or t.startswith("[answer] done"):
                        continue
                    answer_parts.append(t)

            # capture retrieval_result payload
            if event.actions and event.actions.state_delta:
                raw = event.actions.state_delta.get("retrieval_result")
                if raw:
                    try:
                        record["raw_payload"] = json.loads(raw)
                    except Exception as e:
                        logger.warning("[%s/%s] payload parse error: %s", test_id, preset, e)
    except Exception as e:
        record["error"] = str(e)
        logger.exception("[%s/%s] run failed: %s", test_id, preset, e)
    finally:
        logging.getLogger().removeHandler(capture)

    record["e2e_elapsed_s"] = round(time.monotonic() - t0, 2)
    record["rag_answer"] = "".join(answer_parts).strip()
    record["log_parsed"] = parse_logs(capture.records)
    record["log_raw"] = capture.records      # keep raw for debug / future analysis

    return record


# ── orchestration ─────────────────────────────────────────────────────────────

def cache_path(test_id: str, preset: str) -> Path:
    return CACHE_DIR / f"{test_id}__{preset}.json"


def already_cached(test_id: str, preset: str) -> bool:
    p = cache_path(test_id, preset)
    if not p.exists():
        return False
    try:
        rec = json.loads(p.read_text())
        return rec.get("error") is None and rec.get("raw_payload") is not None
    except Exception:
        return False


async def main(
    cases_file: Path,
    presets: List[str],
    case_ids: Optional[List[str]],
    resume: bool,
    retrieve_only: bool,
    cache_dir: Path,
) -> None:
    spec = yaml.safe_load(cases_file.read_text())
    cases = spec["test_cases"]
    if case_ids:
        cases = [c for c in cases if c["test_id"] in case_ids]

    cache_dir.mkdir(exist_ok=True)
    total = len(cases) * len(presets)
    mode = "retrieve-only" if retrieve_only else "end-to-end (retrieve+answer)"
    print(f"\nGoogle eval ({mode}): {len(cases)} cases × {len(presets)} presets = {total} runs")
    print(f"Cache: {cache_dir}")
    print(f"Resume: {resume}\n")

    done = 0
    for preset in presets:
        for case in cases:
            done += 1
            tid = case["test_id"]
            cpath = cache_dir / f"{tid}__{preset}.json"
            if resume and cpath.exists():
                try:
                    rec = json.loads(cpath.read_text())
                    if rec.get("error") is None and rec.get("raw_payload"):
                        print(f"  [{done:>2}/{total}] {tid} / {preset}  ⏭  cached, skip")
                        continue
                except Exception:
                    pass
            print(f"  [{done:>2}/{total}] {tid} / {preset}  ▶ running...")
            rec = await run_case(case, preset, retrieve_only=retrieve_only)
            cpath.write_text(json.dumps(rec, indent=2, ensure_ascii=False))
            chunks_n = len(rec.get("raw_payload", {}).get("chunks", []) or [])
            ans_n = len(rec.get("rag_answer", "") or "")
            ret_ms = (rec.get("log_parsed") or {}).get("latency", {}).get("total_ms")
            err = rec.get("error")
            if err:
                tag = f"❌ ERROR err={err[:80]}"
            elif retrieve_only:
                tag = f"chunks={chunks_n} retrieve={ret_ms}ms e2e={rec['e2e_elapsed_s']}s"
            else:
                tag = f"chunks={chunks_n} answer_len={ans_n} retrieve={ret_ms}ms e2e={rec['e2e_elapsed_s']}s"
            print(f"        {tag}")


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--cases-file", default=str(Path(__file__).parent / "google_test_cases.example.yaml"))
    p.add_argument("--presets", default="balanced")
    p.add_argument("--cases", default=None, help="Comma-separated test_ids to run")
    p.add_argument("--resume", action="store_true", help="Skip cases already cached")
    p.add_argument("--retrieve-only", action="store_true",
                   help="Skip answer_agent, only run retrieve_agent (faster, no rag_answer)")
    p.add_argument("--cache-dir", default=None,
                   help="Override cache directory (default: eval/cache_google/)")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    presets = [s.strip() for s in args.presets.split(",") if s.strip()]
    case_ids = [s.strip() for s in args.cases.split(",")] if args.cases else None
    cache_dir = Path(args.cache_dir) if args.cache_dir else CACHE_DIR
    asyncio.run(main(Path(args.cases_file), presets, case_ids, args.resume,
                     args.retrieve_only, cache_dir))
