"""Create body-free, DeepSeek-reviewed draft labels for P3 candidates.

Raw retrieval text exists only in process memory and is sent to the configured
DeepSeek service under the authorized evaluation workflow. Persisted output is
limited to the normal body-free snapshot plus semantic label anchors.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Mapping

from eval.capture_feishu_benchmark_snapshots import DEFAULT_OUTPUT, REGISTRY, _body_free
from eval.feishu_runner import run_case
from tools.core.llm_client import call_llm, parse_json_response


ROOT = Path(__file__).parent.parent
DEFAULT_LABELS = ROOT / "tests" / "artifacts" / "feishu_query_first_live_labels_draft.json"
_OUTCOMES = {"sufficient_evidence", "partial_evidence", "upstream_evidence_needed", "no_relevant_evidence"}
_REPLANS = {"none", "query_refinement", "upstream_evidence_needed"}


def _chunk_id(chunk: Mapping[str, Any]) -> str:
    return str(chunk.get("chunk_id") or chunk.get("id") or "")


def _chunk_document_id(chunk: Mapping[str, Any]) -> str:
    source_url = str(chunk.get("source_url") or "")
    if source_url:
        return source_url
    chunk_id = _chunk_id(chunk)
    return chunk_id.split(":", 2)[1] if chunk_id.count(":") >= 2 else ""


def _build_label_prompt(candidate: Mapping[str, Any], chunks: list[Mapping[str, Any]]) -> str:
    rendered = []
    for chunk in chunks:
        rendered.append({
            "chunk_id": _chunk_id(chunk),
            "document_id": _chunk_document_id(chunk),
            "document": (chunk.get("metadata") or {}).get("source_name", ""),
            "heading": (chunk.get("metadata") or {}).get("heading_path", ""),
            "text": chunk.get("text", ""),
        })
    return (
        "You are a strict retrieval benchmark labeler. Judge only the supplied live chunks; "
        "never infer missing facts. The user query is verbatim from a real Feishu query seed. "
        "If requested raw tables, dates, entities, or metrics are absent, select upstream_evidence_needed. "
        "A cross-document chain is valid only when the same business entity and period are explicit. "
        "A cross-chunk chain requires at least two distinct supplied chunk IDs.\n\n"
        f"candidate={json.dumps(dict(candidate), ensure_ascii=False)}\n"
        f"chunks={json.dumps(rendered, ensure_ascii=False)}\n\n"
        "Return JSON only: {outcome, replan_decision, expected_chain:[{chunk_id,document_id,role,snippet,evidence_status}], "
        "chain_logic, evidence_shape:{cross_chunk,cross_document,business_entity,period}, reviewer_note}. "
        "outcome must be one of sufficient_evidence, partial_evidence, upstream_evidence_needed, no_relevant_evidence. "
        "replan_decision must be one of none, query_refinement, upstream_evidence_needed. "
        "Use only supplied chunk IDs and keep every snippet to at most 180 characters."
    )


def _sanitize_label(raw: Mapping[str, Any], chunks: list[Mapping[str, Any]]) -> dict[str, Any]:
    by_id = {_chunk_id(chunk): chunk for chunk in chunks if _chunk_id(chunk)}
    outcome = str(raw.get("outcome") or "no_relevant_evidence")
    if outcome not in _OUTCOMES:
        outcome = "no_relevant_evidence"
    replan = str(raw.get("replan_decision") or "upstream_evidence_needed")
    if replan not in _REPLANS:
        replan = "upstream_evidence_needed"
    chain = []
    for item in raw.get("expected_chain") or []:
        if not isinstance(item, Mapping):
            continue
        chunk_id = str(item.get("chunk_id") or "")
        chunk = by_id.get(chunk_id)
        if chunk is None:
            continue
        snippet = str(item.get("snippet") or "").strip()[:180]
        if not snippet:
            continue
        chain.append({
            "chunk_id": chunk_id,
            "document_id": _chunk_document_id(chunk),
            "role": str(item.get("role") or "evidence"),
            "snippet": snippet,
            "evidence_status": str(item.get("evidence_status") or "partial"),
        })
    shape = dict(raw.get("evidence_shape") or {})
    shape["cross_chunk"] = bool(shape.get("cross_chunk")) and len({item["chunk_id"] for item in chain}) >= 2
    shape["cross_document"] = bool(shape.get("cross_document")) and len({item["document_id"] for item in chain}) >= 2
    if not shape["cross_document"]:
        shape["business_entity"] = ""
        shape["period"] = ""
    # A positive conclusion without an ID-anchored chain is not promotable.
    # Keep it as a draft partial result for human review rather than allowing a
    # model assertion to masquerade as retrieval evidence.
    if outcome == "sufficient_evidence" and not chain:
        outcome = "partial_evidence"
    return {
        "outcome": outcome,
        "replan_decision": replan,
        "expected_chain": chain,
        "chain_logic": str(raw.get("chain_logic") or "").strip(),
        "evidence_shape": shape,
        "reviewer_note": str(raw.get("reviewer_note") or "").strip(),
    }


async def _label_one(candidate: dict[str, Any], preset: str, output_dir: Path) -> dict[str, Any]:
    record = await run_case({"test_id": candidate["id"], "query": candidate["source_span"]}, preset, retrieve_only=True)
    snapshot = _body_free(record, candidate)
    snapshot_path = output_dir / f"{candidate['id']}__{preset}.json"
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    chunks = list((record.get("raw_payload") or {}).get("chunks") or [])
    if snapshot["snapshot_status"] != "captured":
        return {"id": candidate["id"], "candidate": candidate, "live_snapshot": str(snapshot_path),
                "label_status": snapshot["snapshot_status"], "expected_chain": []}
    prompt = _build_label_prompt(candidate, chunks)
    raw = await asyncio.to_thread(call_llm, prompt, max_tokens=3_000, timeout=45.0, _caller_tag="p3_live_label")
    label = _sanitize_label(parse_json_response(raw), chunks)
    return {"id": candidate["id"], "candidate": candidate, "live_snapshot": str(snapshot_path),
            "label_status": "draft", **label}


async def main(case_ids: set[str] | None, preset: str, output_dir: Path, labels_path: Path) -> int:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    candidates = registry["candidates"]
    if case_ids:
        candidates = [item for item in candidates if item["id"] in case_ids]
    output_dir.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict[str, Any]] = {}
    if labels_path.exists():
        try:
            old_payload = json.loads(labels_path.read_text(encoding="utf-8"))
            existing = {str(item.get("id")): item for item in old_payload.get("cases") or [] if item.get("id")}
        except (OSError, json.JSONDecodeError):
            existing = {}
    for index, candidate in enumerate(candidates, 1):
        item = await _label_one(candidate, preset, output_dir)
        existing[candidate["id"]] = item
        print(f"[{index}/{len(candidates)}] {candidate['id']} {item['label_status']} -> {item['live_snapshot']}")
    labels = [existing[candidate["id"]] for candidate in registry["candidates"] if candidate["id"] in existing]
    payload = {"benchmark_state": "draft_live_labels", "source": "feishu", "preset": preset,
               "body_free_artifacts": True, "cases": labels}
    labels_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="", help="comma-separated P3 candidate IDs")
    parser.add_argument("--preset", default="deep_dive")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--labels", default=str(DEFAULT_LABELS))
    args = parser.parse_args()
    selected = {item.strip() for item in args.cases.split(",") if item.strip()} or None
    raise SystemExit(asyncio.run(main(selected, args.preset, Path(args.output_dir), Path(args.labels))))
