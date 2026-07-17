"""Capture body-free, current-state snapshots for P3 benchmark labeling.

This is deliberately separate from the benchmark runner: registry candidates
are not scored until a reviewer has promoted them with a live evidence chain.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from eval.feishu_runner import run_case


ROOT = Path(__file__).parent.parent
REGISTRY = ROOT / "tests" / "fixtures" / "feishu_query_first_candidates.json"
DEFAULT_OUTPUT = ROOT / "tests" / "artifacts" / "feishu_query_first_snapshots"


def _body_free(record: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    payload = dict(record.get("raw_payload") or {})
    chunks = []
    for chunk in payload.get("chunks") or []:
        item = dict(chunk)
        content = str(item.pop("text", "") or "")
        item["content_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest() if content else ""
        item["content_chars"] = len(content)
        chunks.append(item)
    payload["chunks"] = chunks
    reads = [event for event in (record.get("retrieval_trace") or {}).get("events", [])
             if event.get("event") == "read" and event.get("chunk_id")]
    source_timeout = "timed out after" in "\n".join(record.get("log_raw") or [])
    status = "runtime_error" if record.get("error") else "inconclusive_timeout" if source_timeout else "captured"
    if not chunks and not reads and status == "captured":
        status = "inconclusive_empty"
    return {
        "candidate": candidate,
        "snapshot_status": status,
        "runtime_error": record.get("error"),
        "e2e_elapsed_s": record.get("e2e_elapsed_s"),
        "retrieval_trace": record.get("retrieval_trace"),
        "read_snapshot": reads,
        "raw_payload": payload,
    }


async def main(case_ids: set[str] | None, preset: str, output_dir: Path) -> int:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    candidates = registry["candidates"]
    if case_ids:
        candidates = [item for item in candidates if item["id"] in case_ids]
    if not candidates:
        raise SystemExit("No P3 candidates selected")
    output_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for index, candidate in enumerate(candidates, 1):
        record = await run_case({"test_id": candidate["id"], "query": candidate["source_span"]}, preset, retrieve_only=True)
        artifact = _body_free(record, candidate)
        path = output_dir / f"{candidate['id']}__{preset}.json"
        path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        failed = artifact["snapshot_status"] != "captured"
        failures += failed
        count = len((artifact["raw_payload"] or {}).get("chunks") or [])
        print(f"[{index}/{len(candidates)}] {candidate['id']} {artifact['snapshot_status']} chunks={count} -> {path}")
    artifacts = []
    for path in sorted(output_dir.glob(f"p3_q*__{preset}.json")):
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        reads = artifact.get("read_snapshot") or []
        artifacts.append({
            "id": (artifact.get("candidate") or {}).get("id"),
            "snapshot_status": artifact.get("snapshot_status"),
            "final_chunk_count": len(((artifact.get("raw_payload") or {}).get("chunks") or [])),
            "read_chunk_count": len({item.get("chunk_id") for item in reads if item.get("chunk_id")}),
            "path": str(path),
        })
    summary = {
        "preset": preset,
        "total_artifacts": len(artifacts),
        "captured": sum(item["snapshot_status"] == "captured" for item in artifacts),
        "inconclusive": sum(item["snapshot_status"] != "captured" for item in artifacts),
        "artifacts": artifacts,
    }
    (output_dir / f"summary__{preset}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="", help="comma-separated P3 candidate IDs")
    parser.add_argument("--preset", default="deep_dive")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    selected = {item.strip() for item in args.cases.split(",") if item.strip()} or None
    raise SystemExit(asyncio.run(main(selected, args.preset, Path(args.output_dir))))
