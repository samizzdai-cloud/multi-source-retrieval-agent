"""Resource-first P3 test-set builder; it never changes production retrieval code.

Workflow: selected Feishu docs -> local Markdown/chunk resources -> DeepSeek
Pro proposes queries -> scores them -> builds auditable evidence-chain drafts.
Query seeds are optional demand signals, never required benchmark cases.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.llm_client import call_llm, parse_json_response

INVENTORY = ROOT / "tests" / "fixtures" / "p3_core_documents.json"
SEEDS = ROOT / "tests" / "fixtures" / "feishu_query_first_candidates.json"
RESOURCE_DIR = ROOT / "tests" / "artifacts" / "p3_core_resources"
OUTPUT_DIR = ROOT / "tests" / "artifacts" / "p3_testset_builder"
LLM = Callable[[str], str]
_OUTCOMES = {"sufficient_evidence", "partial_evidence", "upstream_evidence_needed", "no_relevant_evidence"}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def load_inventory(path: Path = INVENTORY) -> list[dict[str, str]]:
    documents = _load(path).get("documents")
    if not isinstance(documents, list) or not documents:
        raise ValueError("core-document inventory is empty")
    seen, normalized = set(), []
    for item in documents:
        if not isinstance(item, Mapping):
            raise ValueError("core document must be an object")
        doc_id = str(item.get("document_id") or "").strip()
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not doc_id or not title or not url or doc_id in seen:
            raise ValueError("core documents need unique document_id, title, url")
        seen.add(doc_id)
        normalized.append({"document_id": doc_id, "title": title, "url": url})
    return normalized


def _metadata_title(metadata: Mapping[str, Any]) -> str:
    """Drive metadata is authoritative; block payloads do not reliably contain a title."""
    for key in ("name", "title", "doc_name"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return ""


def _invalid_token_error(exc: Exception) -> bool:
    text = str(exc).casefold()
    return any(marker in text for marker in ("invalid token", "invalid document", "not found", "permission denied", "permission error"))


def export_resources(*, inventory: list[Mapping[str, str]], token: str, output_dir: Path = RESOURCE_DIR) -> dict[str, Any]:
    """Read exactly the approved documents via the production Feishu reader/chunker."""
    # lark_oapi imports every endpoint (~25 seconds on this Windows setup), so
    # keep it out of label-only runs and unit tests.
    from feishu_read_tool.client import FeishuClient
    from feishu_read_tool.docx_pipeline import run_rag_pipeline
    from feishu_read_tool.pipeline_models import PipelineConfig
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_chunks: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    client = FeishuClient(user_access_token=token)
    try:
        metadata_by_id = client.get_files_metadata(
            [str(document["document_id"]) for document in inventory]
        )
    except Exception as exc:
        metadata_by_id = {}
        failures.append({"document_id": "__metadata__", "error": str(exc)})
    for document in inventory:
        doc_id, fallback_title, url = document["document_id"], document["title"], document["url"]
        title = _metadata_title(metadata_by_id.get(doc_id, {}))
        title_source = "drive_batch_metadata"
        if not title:
            try:
                title = client.get_document_title(doc_id)
                title_source = "docx_document_metadata"
            except Exception:
                title = fallback_title
                title_source = "inventory_fallback"
        result = None
        attempts_used = 0
        last_error = ""
        invalid_token = False
        for attempt in range(1, 4):
            attempts_used = attempt
            try:
                candidate_result = run_rag_pipeline(
                    query="P3 resource export", candidates=[{"id": doc_id, "title": title, "url": url, "doc_type": "DOCX"}],
                    user_access_token=token, cfg=PipelineConfig(skip_scoring=True, total_token_budget=2_000_000),
                )
                if candidate_result.chunks:
                    result = candidate_result
                    break
                raise RuntimeError("reader returned zero chunks")
            except Exception as exc:
                last_error = str(exc)
                invalid_token = _invalid_token_error(exc)
                if invalid_token:
                    break
        if result is None:
            failures.append({"document_id": doc_id, "status": "invalid_token" if invalid_token else "read_failed", "attempts_used": attempts_used, "error": last_error})
            continue
        try:
            md = [f"# {title}", "", f"document_id: {doc_id}", f"source_url: {url}", ""]
            count = 0
            for index, chunk in enumerate(result.chunks, 1):
                content = str(getattr(chunk, "chunks_content", "") or "").strip()
                if not content:
                    continue
                ref = f"{doc_id}:C{index}"
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                heading = str(getattr(chunk, "heading_path", "") or "")
                content_type = str(getattr(getattr(chunk, "content_type", ""), "value", getattr(chunk, "content_type", "")))
                manifest_chunks.append({"resource_ref": ref, "document_id": doc_id, "document_title": title, "title_source": title_source, "read_attempts": attempts_used,
                                        "source_url": url, "heading_path": heading, "content_type": content_type,
                                        "content_sha256": digest, "content_chars": len(content), "anchor": content[:240]})
                md.extend([f"## [{ref}] {heading or 'untitled'}", "", content, ""])
                count += 1
            (output_dir / f"{doc_id}.md").write_text("\n".join(md), encoding="utf-8")
        except Exception as exc:
            failures.append({"document_id": doc_id, "error": str(exc)})
    manifest = {"format": "p3_resource_manifest_v1", "generated_at": datetime.now(timezone.utc).isoformat(),
                "title_source": "Drive batch metadata, then Docx document metadata; inventory title is fallback only",
                "chunks": manifest_chunks, "failures": failures}
    (output_dir / "resource_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def load_resources(resource_dir: Path = RESOURCE_DIR) -> list[dict[str, Any]]:
    manifest = _load(resource_dir / "resource_manifest.json")
    body: dict[str, str] = {}
    for path in resource_dir.glob("*.md"):
        for part in re.split(r"(?m)^## \[", path.read_text(encoding="utf-8"))[1:]:
            heading, _, text = part.partition("\n\n")
            body[heading.split("]", 1)[0]] = text.strip()
    resources = []
    for item in manifest.get("chunks") or []:
        row = dict(item)
        text = body.get(str(row.get("resource_ref") or ""), "")
        if not text:
            raise ValueError(f"missing local resource body: {row.get('resource_ref')}")
        row["content"] = text
        resources.append(row)
    if not resources:
        raise ValueError("no resource chunks available")
    return resources


def _call_json(llm: LLM, prompt: str) -> Mapping[str, Any]:
    raw = llm(prompt)
    debug_path = os.getenv("P3_BUILDER_RAW_RESPONSE_PATH", "").strip()
    if debug_path:
        Path(debug_path).parent.mkdir(parents=True, exist_ok=True)
        Path(debug_path).write_text(raw, encoding="utf-8")
    value = parse_json_response(raw)
    if not isinstance(value, Mapping):
        raise ValueError("DeepSeek must return a JSON object")
    return value


def _catalog(resources: list[Mapping[str, Any]], *, with_content: bool = False, anchor_chars: int = 120) -> list[dict[str, Any]]:
    keys = ("resource_ref", "document_id", "document_title", "heading_path", "content_type", "content_sha256", "anchor")
    rows = []
    for item in resources:
        row = {key: item.get(key) for key in keys}
        row["anchor"] = str(row.get("anchor") or "")[:anchor_chars]
        if with_content:
            row["content"] = item["content"]
        rows.append(row)
    return rows


def propose_queries(resources: list[Mapping[str, Any]], seeds: list[str], llm: LLM) -> list[dict[str, Any]]:
    document_ids = sorted({str(item.get("document_id") or "") for item in resources})
    shape = "Only propose genuine cross-document cases; every pair must have explicit matching entity and period." if len(document_ids) > 1 else "Propose single-document and multi-chunk cases only."
    prompt = """You design a strict Feishu retrieve-only benchmark. Generate at most 5 candidate queries FROM supplied core-document chunks. User concern queries are optional coverage signals, not required cases. Include only cases whose evidence can be anchored in the resource, or whose explicit missing input can justify upstream_evidence_needed. Cover diverse evidence behavior where real resources permit; never invent facts. """ + shape + """ Return JSON only: {candidates:[{id,query,intent,expected_outcome,required_resource_refs,why_testable,coverage_tags}]}.

resources=""" + json.dumps(_catalog(resources), ensure_ascii=False) + "\nuser_concern_signals=" + json.dumps(seeds, ensure_ascii=False)
    allowed_refs = {str(item["resource_ref"]) for item in resources}
    accepted = []
    for item in (_call_json(llm, prompt).get("candidates") or []):
        if not isinstance(item, Mapping):
            continue
        refs = [str(ref) for ref in (item.get("required_resource_refs") or [])]
        if not str(item.get("query") or "").strip() or not refs or not set(refs).issubset(allowed_refs):
            continue
        outcome = str(item.get("expected_outcome") or "")
        if outcome not in _OUTCOMES:
            continue
        # A proposal is a retrieval contract, not an answer. Keep only the
        # fields needed by the later evidence-chain stage.
        accepted.append({"id": str(item.get("id") or ""), "query": str(item["query"]).strip(),
                         "intent": str(item.get("intent") or ""), "expected_outcome": outcome,
                         "required_resource_refs": refs, "why_testable": str(item.get("why_testable") or ""),
                         "coverage_tags": list(item.get("coverage_tags") or [])})
    return accepted[:5]


def score_queries(resources: list[Mapping[str, Any]], candidates: list[Mapping[str, Any]], llm: LLM) -> list[dict[str, Any]]:
    prompt = """Score every proposed P3 retrieve-only case against current resource anchors. Score 0-100 using: resource relevance, deterministic evidence-chain testability, discrimination of retrieval behavior, and marginal coverage. Reject duplicates, answer-agent-only tasks, external-data requests without a defined missing-evidence outcome, fabricated facts, and cross-document combinations lacking matching entity and period. Return JSON only: {scores:[{id,score,keep,reason,required_resource_refs,coverage_tags}]}.

resources=""" + json.dumps(_catalog(resources), ensure_ascii=False) + "\ncandidates=" + json.dumps(candidates, ensure_ascii=False)
    return [dict(item) for item in (_call_json(llm, prompt).get("scores") or []) if isinstance(item, Mapping)]


def validate_case(case: Mapping[str, Any], resource_by_ref: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    chain = []
    for evidence in case.get("expected_chain") or []:
        if not isinstance(evidence, Mapping):
            continue
        ref, anchor = str(evidence.get("resource_ref") or ""), str(evidence.get("anchor") or "").strip()
        resource = resource_by_ref.get(ref)
        if resource is not None and anchor and anchor in str(resource.get("content") or ""):
            chain.append({"resource_ref": ref, "document_id": resource["document_id"], "content_sha256": resource["content_sha256"],
                          "role": str(evidence.get("role") or "evidence"), "anchor": anchor})
    shape = dict(case.get("evidence_shape") or {})
    if str(case.get("expected_outcome")) == "sufficient_evidence" and not chain:
        raise ValueError(f"{case.get('id')}: sufficient evidence has no verified anchor")
    if shape.get("cross_chunk") and len({item["resource_ref"] for item in chain}) < 2:
        raise ValueError(f"{case.get('id')}: invalid cross-chunk chain")
    if shape.get("cross_document") and (len({item["document_id"] for item in chain}) < 2 or not shape.get("business_entity") or not shape.get("period")):
        raise ValueError(f"{case.get('id')}: invalid cross-document entity/period contract")
    return {**dict(case), "expected_chain": chain, "evidence_shape": shape}


def build_cases(resources: list[Mapping[str, Any]], candidates: list[Mapping[str, Any]], scores: list[Mapping[str, Any]], *, min_score: int, llm: LLM) -> list[dict[str, Any]]:
    score_by_id = {str(item.get("id")): item for item in scores}
    selected = [dict(item) for item in candidates if bool(score_by_id.get(str(item.get("id")), {}).get("keep")) and int(score_by_id[str(item.get("id"))].get("score", 0)) >= min_score]
    refs = {ref for item in selected for ref in (score_by_id[str(item.get("id"))].get("required_resource_refs") or item.get("required_resource_refs") or [])}
    chosen_resources = [item for item in resources if item["resource_ref"] in refs]
    prompt = """Create auditable retrieve-only P3 benchmark cases using only these chunks. For each case give expected_chain entries with resource_ref, unique role and an exact short anchor. Cross-document is allowed only when business_entity and period are explicitly the same. For true insufficiency set expected_outcome=upstream_evidence_needed and do not fabricate evidence. Return JSON only: {cases:[{id,user_query,intent,expected_outcome,expected_chain,chain_logic,evidence_shape:{cross_chunk,cross_document,business_entity,period},replan_expectation,negative_assertions}]}.

selected=""" + json.dumps(selected, ensure_ascii=False) + "\nresources=" + json.dumps(_catalog(chosen_resources, with_content=True), ensure_ascii=False)
    by_ref = {str(item["resource_ref"]): item for item in resources}
    return [validate_case(item, by_ref) for item in (_call_json(llm, prompt).get("cases") or []) if isinstance(item, Mapping)]


def _seeds() -> list[str]:
    return [str(item.get("source_span") or "") for item in _load(SEEDS).get("candidates") or [] if item.get("source_span")]


def _scope_resources(resources: list[Mapping[str, Any]], document_ids: str) -> tuple[list[Mapping[str, Any]], str]:
    ids = [item.strip() for item in document_ids.split(",") if item.strip()]
    if not ids:
        return resources, "all"
    selected = [item for item in resources if str(item.get("document_id")) in ids]
    found = {str(item.get("document_id")) for item in selected}
    missing = set(ids) - found
    if missing:
        raise ValueError(f"unknown or unread document IDs: {sorted(missing)}")
    return selected, "__".join(ids)


def _merge_stage(path: Path, key: str, items: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    current = list(_load(path).get(key) or []) if path.exists() else []
    by_id = {str(item.get("id")): dict(item) for item in current if isinstance(item, Mapping) and item.get("id")}
    for item in items:
        if item.get("id"):
            by_id[str(item["id"])] = dict(item)
    merged = list(by_id.values())
    path.write_text(json.dumps({key: merged}, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def _deepseek_pro(prompt: str) -> str:
    return call_llm(prompt, model=os.getenv("P3_BUILDER_MODEL", "deepseek/deepseek-v4-pro"), max_tokens=5_000,
                    timeout=75.0, max_retries=1,
                    thinking_config={
                        "reasoning_effort": "high",
                        "extra_body": {"thinking": {"type": "enabled"}},
                    },
                    _caller_tag="p3_resource_testset_builder")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export-resources", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--resource-dir", type=Path, default=RESOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--min-score", type=int, default=75)
    parser.add_argument("--stage", choices=("all", "propose", "score", "label"), default="all",
                        help="Run one resumable builder stage; later stages load prior JSON artifacts.")
    parser.add_argument("--document-ids", default="", help="comma-separated document IDs for one document or a defined document pair")
    args = parser.parse_args()
    if not args.export_resources and not args.build:
        parser.error("select --export-resources and/or --build")
    if args.export_resources:
        token = os.getenv("FEISHU_UAT", "")
        if not token:
            raise SystemExit("FEISHU_UAT is required")
        manifest = export_resources(inventory=load_inventory(), token=token, output_dir=args.resource_dir)
        print(f"resource export: {len(manifest['chunks'])} chunks; {len(manifest['failures'])} failed documents")
    if args.build:
        resources = load_resources(args.resource_dir)
        scoped_resources, scope = _scope_resources(resources, args.document_ids)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        candidates_path = args.output_dir / "01_candidates.json"
        scores_path = args.output_dir / "02_scores.json"
        if args.stage in ("all", "propose"):
            proposed = propose_queries(scoped_resources, _seeds(), _deepseek_pro)
            candidates = []
            for item in proposed:
                raw_id = str(item.get("id") or f"q{len(candidates) + 1}")
                candidates.append({**item, "id": f"{scope}:{raw_id}", "builder_scope": scope,
                                   "source_document_ids": sorted({str(row.get('document_id')) for row in scoped_resources})})
            candidates = _merge_stage(candidates_path, "candidates", candidates)
            if args.stage == "propose":
                print(f"candidate stage: {len(proposed)} valid proposals; {len(candidates)} total persisted candidates")
                return 0
        else:
            candidates = list(_load(candidates_path).get("candidates") or [])
        scoped_candidates = [item for item in candidates if item.get("builder_scope") == scope]
        if args.stage in ("all", "score"):
            scored = score_queries(scoped_resources, scoped_candidates, _deepseek_pro)
            scores = _merge_stage(scores_path, "scores", scored)
        else:
            scores = list(_load(scores_path).get("scores") or [])
        if args.stage == "score":
            print(f"score stage: {len(scores)} scores")
            return 0
        cases = build_cases(scoped_resources, scoped_candidates, scores, min_score=args.min_score, llm=_deepseek_pro)
        output = {"format": "p3_resource_first_draft_v1", "generated_at": datetime.now(timezone.utc).isoformat(),
                  "scope": "Feishu retrieve-only; Answer Agent and non-Feishu agents excluded", "min_score": args.min_score,
                  "resource_manifest": str(args.resource_dir / "resource_manifest.json"), "candidates": candidates, "scores": scores, "cases": cases}
        (args.output_dir / "draft_testset.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"draft test set: {len(cases)} validated cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
