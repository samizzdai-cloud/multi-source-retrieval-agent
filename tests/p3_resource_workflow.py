"""Resumable, resource-first P3 benchmark workflow.

This module deliberately builds test assets outside production retrieval code.
Every LLM step has a narrow contract, writes one checkpoint, and is followed by
deterministic validation before a later stage can consume its output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.llm_client import call_llm, parse_json_response
from tests.p3_testset_builder import export_resources, load_inventory, load_resources


RESOURCE_DIR = ROOT / "tests" / "artifacts" / "p3_core_resources"
RUN_DIR = ROOT / "tests" / "artifacts" / "p3_resource_first"
SEED_PATH = ROOT / "tests" / "fixtures" / "feishu_query_first_candidates.json"
LLM = Callable[[str], str]
OUTCOMES = {"sufficient_evidence", "upstream_evidence_needed"}
CASE_TYPES = {"single_chunk", "cross_chunk", "cross_document", "insufficient_evidence"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _load_if_valid(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        return _load(path)
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def _json_call(llm: LLM, prompt: str, raw_path: Path | None = None) -> Mapping[str, Any]:
    try:
        raw = llm(prompt)
    except Exception as exc:
        if raw_path:
            _write(raw_path.with_suffix(".error.json"), {"stage_error": type(exc).__name__, "message": str(exc), "recorded_at": _now()})
        raise
    if raw_path:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(raw, encoding="utf-8")
    parsed = parse_json_response(raw)
    if not isinstance(parsed, Mapping):
        raise ValueError("LLM response must be a JSON object")
    return parsed


def _compact_chunk(chunk: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "resource_ref": chunk["resource_ref"],
        "heading": chunk.get("heading_path", ""),
        "content_type": chunk.get("content_type", ""),
        "anchor": str(chunk.get("anchor") or chunk.get("content") or "")[:180],
    }


def _merge_items(path: Path, key: str, items: Iterable[Mapping[str, Any]], id_key: str) -> list[dict[str, Any]]:
    old = _load(path).get(key, []) if path.exists() else []
    merged = {str(item.get(id_key)): dict(item) for item in old if isinstance(item, Mapping) and item.get(id_key)}
    for item in items:
        if item.get(id_key):
            merged[str(item[id_key])] = dict(item)
    result = list(merged.values())
    _write(path, {"generated_at": _now(), key: result})
    return result


def _list_str(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return [str(item).strip() for item in (value or []) if str(item).strip()]


def _title_entities(title: str) -> list[str]:
    """Keep a clearly branded title token as auditable source metadata.

    This is deliberately narrow: an all-caps brand in the real document title
    is evidence, while generic abbreviations such as GTM are excluded.  It
    repairs an LLM omission without asking the program to infer business facts.
    """
    ignored = {"GTM", "SKU", "SPU", "AI", "ROI", "GMV", "DOS"}
    values = [token for token in re.findall(r"\b[A-Z][A-Z0-9]{2,}\b", title) if token not in ignored]
    # Product/brand titles such as "Hypershell 新品..." or "Nubia Z80 U
    # GTM..." carry an explicit owner before the Chinese descriptor.
    leading = re.match(r"\s*([A-Za-z][A-Za-z0-9]*(?:\s+[A-Za-z0-9]+){0,3})\s*[\u4e00-\u9fff]", title)
    if leading and leading.group(1) not in ignored:
        values.append(leading.group(1))
    return sorted(set(values))


def _profile_document(resources: list[Mapping[str, Any]], document_id: str, llm: LLM, raw_path: Path | None, *, batch_numbers: Iterable[int] = (), allow_partial: bool = False) -> dict[str, Any]:
    chunks = [item for item in resources if item["document_id"] == document_id]
    if not chunks:
        raise ValueError(f"unknown document {document_id}")
    title = str(chunks[0].get("document_title") or document_id)
    # Default is one complete document request.  Chunk batching exists only as
    # an explicit recovery mode for a provider failure, never as normal P3
    # methodology because it weakens document-level semantic profiling.
    batch_size = int(os.getenv("P3_RESOURCE_MAP_CHUNK_BATCH_SIZE", "0") or 0)
    batches = [chunks] if batch_size <= 0 else [chunks[index:index + batch_size] for index in range(0, len(chunks), batch_size)]
    wanted_batches = set(batch_numbers)
    if any(number < 1 or number > len(batches) for number in wanted_batches):
        raise ValueError(f"invalid batch number for {document_id}: {sorted(wanted_batches)}")
    doc_parts, profiled_chunks = [], []
    for batch_index, batch in enumerate(batches, 1):
        if wanted_batches and batch_index not in wanted_batches:
            continue
        prompt = """You are profiling one authorized Feishu document for a retrieval benchmark. Do not answer user questions. Infer only what the supplied chunk anchors support. Return JSON only:
{document:{entities,periods,document_kind,information_facets,granularity,limitations,valid_join_keys:{entity,period,metrics}},chunks:[{resource_ref,capabilities,entities,periods,limitations}]}.
Use only supplied resource_ref values. Keep labels short. If a fact is absent, put it in limitations rather than inventing it.

document_title=""" + title + "\nchunk_batch=" + str(batch_index) + "/" + str(len(batches)) + "\nchunks=" + json.dumps([_compact_chunk(item) for item in batch], ensure_ascii=False)
        suffix = "full" if len(batches) == 1 else f"part{batch_index}"
        part_path = raw_path.with_name(f"{raw_path.stem}__{suffix}{raw_path.suffix}") if raw_path else None
        legacy_path = raw_path.with_name(f"{raw_path.stem}__part1{raw_path.suffix}") if raw_path and len(batches) == 1 else None
        result = _load_if_valid(part_path) or _load_if_valid(legacy_path) or _load_if_valid(raw_path) or _json_call(llm, prompt, part_path)
        doc_parts.append(dict(result.get("document") or {}))
        allowed = {str(item["resource_ref"]) for item in batch}
        for item in result.get("chunks") or []:
            if not isinstance(item, Mapping) or str(item.get("resource_ref") or "") not in allowed:
                continue
            profiled_chunks.append({
                "resource_ref": str(item["resource_ref"]),
                "capabilities": _list_str(item.get("capabilities")),
                "entities": _list_str(item.get("entities")),
                "periods": _list_str(item.get("periods")),
                "limitations": _list_str(item.get("limitations")),
            })
    if not allow_partial and len(doc_parts) != len(batches):
        raise ValueError(f"{document_id}: missing resource-map batches; run --chunk-batch for {sorted(set(range(1, len(batches) + 1)) - wanted_batches)}")
    def merged_list(key: str) -> list[str]:
        return sorted({value for part in doc_parts for value in _list_str(part.get(key))})
    # A document-level summary can omit an entity or period that is explicit in
    # a chunk.  Preserve those observed chunk facts so joins are conservative
    # but do not silently lose a valid strategy-to-execution connection.
    doc = {
        "entities": sorted(set(merged_list("entities")) | {value for item in profiled_chunks for value in item["entities"]} | set(_title_entities(title))),
        "periods": sorted(set(merged_list("periods")) | {value for item in profiled_chunks for value in item["periods"]}),
        "information_facets": merged_list("information_facets"),
        "granularity": merged_list("granularity"),
        "limitations": merged_list("limitations"),
        "valid_join_keys": {key: sorted({value for part in doc_parts for value in _list_str((part.get("valid_join_keys") or {}).get(key))}) for key in ("entity", "period", "metrics")},
    }
    doc["document_kind"] = next((str(part.get("document_kind")) for part in doc_parts if str(part.get("document_kind") or "") not in {"", "unknown"}), "unknown")
    seen = {item["resource_ref"] for item in profiled_chunks}
    # Every readable chunk must remain visible downstream even if the LLM does
    # not classify it; this makes omission auditable rather than destructive.
    for item in chunks:
        if item["resource_ref"] not in seen:
            profiled_chunks.append({"resource_ref": item["resource_ref"], "capabilities": [], "entities": [], "periods": [], "limitations": []})
    return {
        "document_id": document_id,
        "title": title,
        "primary_entities": _title_entities(title),
        "entities": doc["entities"],
        "periods": doc["periods"],
        "document_kind": str(doc.get("document_kind") or "unknown"),
        "information_facets": doc["information_facets"],
        "granularity": doc["granularity"],
        "limitations": doc["limitations"],
        "valid_join_keys": doc["valid_join_keys"],
        "chunks": profiled_chunks,
    }


def build_resource_map(resources: list[Mapping[str, Any]], llm: LLM, output_path: Path, *, document_ids: Iterable[str] = ()) -> dict[str, Any]:
    ids = list(document_ids) or sorted({str(item["document_id"]) for item in resources})
    existing = _load(output_path).get("documents", []) if output_path.exists() else []
    by_id = {str(item.get("document_id")): dict(item) for item in existing if isinstance(item, Mapping)}
    for document_id in ids:
        by_id[document_id] = _profile_document(resources, document_id, llm, output_path.parent / "raw" / f"resource_map__{document_id}.json")
    payload = {"format": "p3_resource_map_v1", "generated_at": _now(), "documents": list(by_id.values())}
    _write(output_path, payload)
    return payload


def build_query_pool(seed_path: Path, resource_map: Mapping[str, Any], llm: LLM, output_path: Path) -> dict[str, Any]:
    """Seed queries are retained as candidates; LLM adds only explicit coverage gaps."""
    seed_payload = _load(seed_path)
    queries, excluded = [], []
    for item in seed_payload.get("candidates") or []:
        text = str(item.get("source_span") or "").strip()
        record_id = str(item.get("record_id") or item.get("id") or "")
        if text and record_id:
            reason = _seed_exclusion_reason(text, resource_map)
            record = {"query_id": f"seed:{record_id}", "query": text, "origin": "real_seed", "provenance": dict(item), "intended_capability": []}
            if reason:
                excluded.append({**record, "exclusion_reason": reason})
            else:
                queries.append(record)
    cards = [{key: item.get(key) for key in ("document_id", "title", "entities", "periods", "information_facets", "granularity", "limitations", "valid_join_keys")} for item in resource_map.get("documents") or []]
    prompt = """Identify at most 8 coverage gaps not represented by the supplied real-user query candidates. Generate only retrieval-test queries; do not provide answers, evidence chunks, or fabricated facts. Each generated query must name its intended capability and a document scope from the supplied cards. Return JSON only: {queries:[{query,document_ids,intended_capability,reason}]}.

resource_cards=""" + json.dumps(cards, ensure_ascii=False) + "\nreal_user_candidates=" + json.dumps([item["query"] for item in queries], ensure_ascii=False)
    raw_path = output_path.parent / "raw" / "query_pool_coverage_gap.json"
    # Coverage candidates are an immutable checkpoint for a given resource-map
    # snapshot. Rebuilding the active pool after a deterministic admission-rule
    # change must not make a second model request.
    generated_response = _load_if_valid(raw_path) or _json_call(llm, prompt, raw_path)
    generated = generated_response.get("queries") or []
    known_ids = {str(item["document_id"]) for item in resource_map.get("documents") or []}
    eligible_scope_sets = {tuple(sorted(scope["document_ids"])) for scope in derive_scopes(resource_map)}
    for index, item in enumerate(generated, 1):
        if not isinstance(item, Mapping):
            continue
        scope = _list_str(item.get("document_ids"))
        query = str(item.get("query") or "").strip()
        if query and scope and set(scope).issubset(known_ids) and tuple(sorted(scope)) in eligible_scope_sets:
            queries.append({"query_id": f"gap:{index}", "query": query, "origin": "coverage_gap", "provenance": {"reason": str(item.get("reason") or "")}, "intended_capability": _list_str(item.get("intended_capability")), "suggested_documents": scope})
        elif query:
            excluded.append({"query_id": f"gap:{index}", "query": query, "origin": "coverage_gap", "provenance": {"reason": str(item.get("reason") or "")}, "exclusion_reason": "suggested document scope is not approved by the Resource Map"})
    # A valid strategy-to-execution pair needs at least two independently
    # testable queries. The pair is pre-approved by Resource Map governance,
    # so this template fills a coverage gap without inventing a factual answer.
    for scope in (item for item in derive_scopes(resource_map) if item["kind"] == "pair"):
        if any(set(query.get("suggested_documents") or []) == set(scope["document_ids"]) for query in queries):
            continue
        first, second = [next(doc for doc in resource_map.get("documents") or [] if doc["document_id"] == document_id) for document_id in scope["document_ids"]]
        queries.append({
            "query_id": f"gap:pair:{scope['scope_id']}",
            "query": f"结合《{first['title']}》中的策略/产品角色定义与《{second['title']}》中的本期经营表现，哪些策略假设已被执行数据支持，哪些仍需补充上游证据？",
            "origin": "coverage_gap",
            "provenance": {"reason": "Resource Map approved strategy-to-execution pair coverage", "scope_id": scope["scope_id"]},
            "intended_capability": ["strategy_definition", "actual_performance", "strategy_execution_comparison"],
            "suggested_documents": list(scope["document_ids"]),
        })
    # Split a broad query at a documented evidence boundary. REDMAGIC inventory
    # is incomplete, while its sales and advertising summaries are available;
    # retaining both creates an honest sufficient-evidence chain and an
    # insufficient-evidence control instead of one ambiguous case.
    for document in resource_map.get("documents") or []:
        facets = {str(value).casefold() for value in document.get("information_facets") or []}
        primary = _list_str(document.get("primary_entities"))
        if "redmagic" not in {value.casefold() for value in primary} or not {"sales", "advertising"}.issubset(facets):
            continue
        queries.append({
            "query_id": f"gap:refined:{document['document_id']}",
            "query": "REDMAGIC 在 2026-06-15 至 2026-06-21 的销售与广告表现分别发生了什么变化？请基于可见经营指标说明二者的关系，不推断库存。",
            "origin": "coverage_gap",
            "provenance": {"reason": "split broad sales-ad-inventory candidate at documented inventory-data boundary", "parent_query_id": "gap:1"},
            "intended_capability": ["sales_summary", "advertising_performance", "sales_ad_relationship"],
            "suggested_documents": [str(document["document_id"])],
        })
    payload = {"format": "p3_query_pool_v1", "generated_at": _now(), "queries": queries, "excluded_queries": excluded}
    _write(output_path, payload)
    return payload


def _seed_exclusion_reason(query: str, resource_map: Mapping[str, Any]) -> str | None:
    """A pre-scoring gate: exclude seeds the fixed Feishu corpus cannot test.

    Exclusions remain audit records but are never sent to the relevance model.
    This keeps an external-data request from influencing P3 merely because it
    was observed in real usage.
    """
    text = query.casefold()
    external = ("mcp", "shopify", "google analytics", "google ads", "meta ads", "上传文件", "excel")
    if any(token in text for token in external):
        return "requires external source or user-provided artifact outside the fixed Feishu corpus"
    if "预测" in query or "forecast" in text:
        return "requests a forecast rather than retrieval of document-supported evidence"
    primary = {token.casefold() for doc in resource_map.get("documents") or [] for token in doc.get("primary_entities") or []}
    # Candidate entities can be present under a different case, but a query
    # without a title-anchored business entity is not safely attributable to
    # this deliberately small corpus.
    if not any(token and token in text for token in primary):
        return "no title-anchored business entity in the fixed corpus"
    # The corpus has no May-2026 Nubia traffic report; accepting it would turn
    # a precise date request into an untestable partial-evidence case.
    if "nubia" in text and ("2026年5月" in query or "2026-05" in text):
        return "requested Nubia period is not represented by the fixed corpus"
    if re.search(r"(?:5月|5[./]\d)", query):
        return "requested May reporting period is not represented by the fixed corpus"
    return None


def derive_scopes(resource_map: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Permit only defensible document pairs; no arbitrary cross-brand joins."""
    documents = list(resource_map.get("documents") or [])
    scopes = [{"scope_id": str(doc["document_id"]), "document_ids": [str(doc["document_id"])], "kind": "single"} for doc in documents]
    def normalized(items: Iterable[object]) -> set[str]:
        return {re.sub(r"\s+", " ", str(item).strip()).casefold() for item in items if str(item).strip()}

    def kind_is(document: Mapping[str, Any], marker: str) -> bool:
        kind = str(document.get("document_kind") or "").casefold()
        aliases = {"weekly": ("weekly", "周报"), "gtm": ("gtm", "上市"), "strategy": ("strategy", "战略", "策划")}
        return any(token in kind for token in aliases.get(marker, (marker,)))

    for index, left in enumerate(documents):
        left_entities, left_periods = normalized(left.get("entities") or []), normalized(left.get("periods") or [])
        left_primary = normalized(left.get("primary_entities") or [])
        for right in documents[index + 1:]:
            shared_entity = left_entities & normalized(right.get("entities") or [])
            shared_period = left_periods & normalized(right.get("periods") or [])
            shared_primary_entity = left_primary & normalized(right.get("primary_entities") or [])
            strategy_execution = shared_primary_entity and (
                (kind_is(left, "gtm") or kind_is(left, "strategy")) and kind_is(right, "weekly")
                or (kind_is(right, "gtm") or kind_is(right, "strategy")) and kind_is(left, "weekly")
            )
            if not shared_entity or not (shared_period or strategy_execution):
                continue
            ids = [str(left["document_id"]), str(right["document_id"])]
            scopes.append({"scope_id": "__".join(ids), "document_ids": ids, "kind": "pair", "shared_entities": sorted(shared_entity), "shared_primary_entities": sorted(shared_primary_entity), "shared_periods": sorted(shared_period)})
    return scopes


def score_scope(query_pool: Mapping[str, Any], resource_map: Mapping[str, Any], scope: Mapping[str, Any], llm: LLM, raw_path: Path | None) -> list[dict[str, Any]]:
    ids = set(scope["document_ids"])
    cards = [item for item in resource_map.get("documents") or [] if item.get("document_id") in ids]
    scope_queries = [item for item in query_pool.get("queries") or [] if not item.get("suggested_documents") or set(item.get("suggested_documents") or []) == ids]
    prompt = """Evaluate query candidates for exactly the supplied document scope. Do not answer the queries. Return one row per kept candidate and reject all others explicitly. A cross-document case is valid only if the documents have matching entity/period or a strategy-to-execution relation. Return JSON only: {rows:[{query_id,relevance_score,testability_score,case_type,required_capabilities,expected_outcome,reason,reject_reason}]}.

scope=""" + json.dumps(scope, ensure_ascii=False) + "\nresource_cards=" + json.dumps(cards, ensure_ascii=False) + "\nqueries=" + json.dumps(scope_queries, ensure_ascii=False)
    known_queries = {str(item["query_id"]) for item in scope_queries}
    rows = []
    # A scope score is a checkpoint. Matrix aggregation must be a pure local
    # operation when all scope outputs already exist.
    cached = _load_if_valid(raw_path)
    cached_ids = {str(item.get("query_id")) for item in (cached or {}).get("rows") or [] if isinstance(item, Mapping)}
    response = cached if cached and known_queries.issubset(cached_ids) else _json_call(llm, prompt, raw_path)
    for item in response.get("rows") or []:
        if isinstance(item, Mapping) and str(item.get("query_id") or "") in known_queries:
            candidate = next(query for query in scope_queries if str(query["query_id"]) == str(item["query_id"]))
            normalized = _normalize_score_row(item, scope, candidate)
            rows.append({"query_id": str(item["query_id"]), "scope_id": scope["scope_id"], "document_ids": list(scope["document_ids"]), **normalized})
    return rows


def _normalize_score_row(item: Mapping[str, Any], scope: Mapping[str, Any], candidate: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Normalize common DeepSeek schema drift without accepting answer text."""
    rejected = str(item.get("reject_reason") or "").strip()
    intended_scope = candidate is not None and set(candidate.get("suggested_documents") or []) == set(scope.get("document_ids") or [])
    absence_markers = ("lack", "truncated", "no visible", "not sufficient", "only header", "不包含", "缺少", "截断", "不可见", "表头")
    mismatch_markers = ("not found", "not covered", "not match", "不匹配", "无关", "不涉及")
    if rejected:
        # A generated query whose explicitly intended resource is present but
        # lacks required rows is a valid negative control, not a discarded
        # cross-brand query. Its runtime contract is to request upstream data.
        control = intended_scope and any(marker in rejected.casefold() for marker in absence_markers) and not any(marker in rejected.casefold() for marker in mismatch_markers)
        return {"relevance_score": 0, "testability_score": 0, "case_type": "insufficient_evidence", "required_capabilities": [], "expected_outcome": "upstream_evidence_needed", "reason": str(item.get("reason") or ""), "reject_reason": "" if control else rejected, "control_eligible": control, "normalization": "negative_control" if control else "rejected"}
    def score(value: object) -> int:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0
        return max(0, min(100, round(number * 100 if 0 <= number <= 1 else number)))
    capabilities = _list_str(item.get("required_capabilities"))
    raw_type = str(item.get("case_type") or "").casefold().replace("_", "-")
    if scope.get("kind") == "pair":
        case_type = "cross_document"
    elif raw_type in {"cross-chunk", "cross chunk"} or len(capabilities) >= 2:
        # This remains provisional: the evidence-chain validator must still
        # prove two distinct, non-substitutable chunk roles.
        case_type = "cross_chunk"
    else:
        case_type = "single_chunk"
    outcome_text = str(item.get("expected_outcome") or "").casefold()
    control = intended_scope and any(marker in outcome_text for marker in ("only table headers", "unavailable", "not available", "仅表头", "缺少数据"))
    return {"relevance_score": score(item.get("relevance_score")), "testability_score": score(item.get("testability_score")), "case_type": "insufficient_evidence" if control else case_type, "required_capabilities": capabilities, "expected_outcome": "upstream_evidence_needed" if control else "sufficient_evidence", "reason": str(item.get("reason") or ""), "reject_reason": "", "control_eligible": control, "normalization": "schema_normalized"}


def build_relevance_matrix(query_pool: Mapping[str, Any], resource_map: Mapping[str, Any], llm: LLM, output_path: Path, *, scope_ids: Iterable[str] = ()) -> dict[str, Any]:
    scopes = derive_scopes(resource_map)
    wanted = set(scope_ids)
    if wanted:
        scopes = [scope for scope in scopes if scope["scope_id"] in wanted]
    old = _load(output_path).get("rows", []) if output_path.exists() else []
    by_key = {(str(row.get("query_id")), str(row.get("scope_id"))): dict(row) for row in old if isinstance(row, Mapping)}
    for scope in scopes:
        rows = score_scope(query_pool, resource_map, scope, llm, output_path.parent / "raw" / f"relevance__{scope['scope_id']}.json")
        for row in rows:
            by_key[(row["query_id"], row["scope_id"])] = row
    payload = {"format": "p3_relevance_matrix_v1", "generated_at": _now(), "scopes": scopes, "rows": list(by_key.values())}
    _write(output_path, payload)
    return payload


def select_blueprints(query_pool: Mapping[str, Any], matrix: Mapping[str, Any], output_path: Path, *, pilot_size: int = 8) -> dict[str, Any]:
    queries = {str(item["query_id"]): item for item in query_pool.get("queries") or []}
    candidates = [row for row in matrix.get("rows") or [] if not row.get("reject_reason") and (
        (row.get("expected_outcome") == "sufficient_evidence" and int(row.get("relevance_score", 0)) >= 70 and int(row.get("testability_score", 0)) >= 70)
        or (row.get("expected_outcome") == "upstream_evidence_needed" and row.get("control_eligible"))
    )]
    candidates.sort(key=lambda row: (row["case_type"] in {"cross_document", "cross_chunk"}, row["relevance_score"] + row["testability_score"]), reverse=True)
    selected, used_queries = [], set()
    for row in candidates:
        if row["query_id"] in used_queries or len(selected) >= pilot_size:
            continue
        selected.append(row)
        used_queries.add(row["query_id"])
    complex_count = sum(row["case_type"] in {"cross_chunk", "cross_document"} for row in selected)
    cross_document_count = sum(row["case_type"] == "cross_document" for row in selected)
    cross_chunk_count = sum(row["case_type"] == "cross_chunk" for row in selected)
    if len(selected) < pilot_size or complex_count < 5 or cross_document_count < 2 or cross_chunk_count < 2:
        raise ValueError("pilot coverage gate not met: need 8 cases, >=5 complex, >=2 cross_document, >=2 cross_chunk")
    blueprints = []
    for index, row in enumerate(selected, 1):
        query = queries[row["query_id"]]
        blueprints.append({"case_id": f"pilot_{index:02d}", "query_id": row["query_id"], "user_query": query["query"], "origin": query["origin"],
                           "provenance": query.get("provenance", {}), "case_type": row["case_type"], "allowed_documents": row["document_ids"],
                           "required_capabilities": row["required_capabilities"], "expected_outcome": row["expected_outcome"],
                           "expected_control": "upstream_evidence_needed" if row["expected_outcome"] == "upstream_evidence_needed" else "none",
                           "selection_reason": row["reason"]})
    payload = {"format": "p3_case_blueprints_v1", "generated_at": _now(), "pilot_size": pilot_size, "cases": blueprints}
    _write(output_path, payload)
    return payload


def validate_evidence_chain(case: Mapping[str, Any], chain: Mapping[str, Any], resources: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    allowed = set(case["allowed_documents"])
    items = []
    for item in chain.get("minimal_chain") or []:
        if not isinstance(item, Mapping):
            continue
        ref, anchor = str(item.get("resource_ref") or ""), str(item.get("anchor") or "").strip()
        resource = resources.get(ref)
        if not resource or resource["document_id"] not in allowed or not anchor or anchor not in str(resource.get("content") or ""):
            continue
        items.append({"resource_ref": ref, "document_id": resource["document_id"], "content_sha256": resource["content_sha256"], "role": str(item.get("role") or "evidence"), "anchor": anchor})
    refs = {item["resource_ref"] for item in items}
    docs = {item["document_id"] for item in items}
    case_type = case["case_type"]
    if case["expected_outcome"] == "sufficient_evidence" and not items:
        raise ValueError(f"{case['case_id']}: no verified evidence")
    if case_type == "cross_chunk" and len(refs) < 2:
        raise ValueError(f"{case['case_id']}: cross_chunk requires two refs")
    if case_type == "cross_document":
        if len(docs) < 2 or not chain.get("entity") or not chain.get("period"):
            raise ValueError(f"{case['case_id']}: cross_document requires two docs, entity and period")
    return {"case_id": case["case_id"], "minimal_chain": items, "chain_logic": str(chain.get("chain_logic") or ""), "entity": str(chain.get("entity") or ""), "period": str(chain.get("period") or ""), "review_required": case_type == "cross_document"}


def build_evidence_chains(blueprints: Mapping[str, Any], resources: list[Mapping[str, Any]], llm: LLM, output_path: Path) -> dict[str, Any]:
    by_ref = {str(item["resource_ref"]): item for item in resources}
    chains = []
    for case in blueprints.get("cases") or []:
        if case["expected_outcome"] == "upstream_evidence_needed":
            chains.append(validate_evidence_chain(case, {"minimal_chain": [], "chain_logic": "fixed corpus lacks required upstream evidence", "entity": "", "period": ""}, by_ref))
            continue
        scoped = [item for item in resources if item["document_id"] in set(case["allowed_documents"])]
        prompt = """Build the minimum gold evidence chain for this retrieve-only benchmark case. Do not write an answer. Use only supplied chunks. Every item needs resource_ref, distinct evidence role, and exact short anchor. For cross-document include explicit shared entity and period. Return JSON only: {minimal_chain:[{resource_ref,role,anchor}],chain_logic,entity,period}.

case=""" + json.dumps(case, ensure_ascii=False) + "\nchunks=" + json.dumps([{**_compact_chunk(item), "content": item["content"]} for item in scoped], ensure_ascii=False)
        raw_path = output_path.parent / "raw" / f"chain__{case['case_id']}.json"
        response = _load_if_valid(raw_path) or _json_call(llm, prompt, raw_path)
        chains.append(validate_evidence_chain(case, response, by_ref))
    payload = {"format": "p3_evidence_chains_v1", "generated_at": _now(), "chains": chains}
    _write(output_path, payload)
    return payload


def promote_pilot(blueprints: Mapping[str, Any], chains: Mapping[str, Any], resources: list[Mapping[str, Any]], output_path: Path) -> dict[str, Any]:
    chain_by_case = {str(item["case_id"]): item for item in chains.get("chains") or []}
    cases = []
    for blueprint in blueprints.get("cases") or []:
        chain = chain_by_case.get(str(blueprint["case_id"]))
        if not chain:
            raise ValueError(f"missing chain for {blueprint['case_id']}")
        review_required = bool(chain["review_required"])
        cases.append({**blueprint, "expected_chain": chain["minimal_chain"], "evidence_shape": {"cross_chunk": blueprint["case_type"] in {"cross_chunk", "cross_document"}, "cross_document": blueprint["case_type"] == "cross_document", "business_entity": chain["entity"], "period": chain["period"]}, "review_required": review_required, "review_status": "pending" if review_required else "not_required", "chain_logic": chain["chain_logic"]})
    payload = {"format": "p3_resource_first_benchmark_v1", "benchmark_state": "pilot_review_required", "generated_at": _now(), "scope": "Feishu retrieve-only", "resource_manifest": str(RESOURCE_DIR / "resource_manifest.json"), "cases": cases}
    _write(output_path, payload)
    return payload


def _deepseek_pro(prompt: str) -> str:
    return call_llm(prompt, model=os.getenv("P3_BUILDER_MODEL", "deepseek/deepseek-v4-pro"), max_tokens=int(os.getenv("P3_LLM_MAX_TOKENS", "12000")), timeout=float(os.getenv("P3_LLM_TIMEOUT_S", "900")), max_retries=1, thinking_config={"reasoning_effort": "high", "extra_body": {"thinking": {"type": "enabled"}}}, _caller_tag="p3_resource_workflow")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, choices=("export", "resource-map", "query-pool", "relevance", "blueprints", "chains", "promote"))
    parser.add_argument("--resource-dir", type=Path, default=RESOURCE_DIR)
    parser.add_argument("--run-dir", type=Path, default=RUN_DIR)
    parser.add_argument("--document-ids", default="")
    parser.add_argument("--chunk-batch", type=int, default=0, help="Create or reuse one resource-map batch, then exit; use 0 to merge all batches")
    args = parser.parse_args()
    ids = [item.strip() for item in args.document_ids.split(",") if item.strip()]
    args.run_dir.mkdir(parents=True, exist_ok=True)
    if args.stage == "export":
        token = os.getenv("FEISHU_UAT", "")
        if not token:
            raise SystemExit("FEISHU_UAT is required for export")
        export_resources(inventory=load_inventory(), token=token, output_dir=args.resource_dir)
        return 0
    resources = load_resources(args.resource_dir)
    resource_map_path = args.run_dir / "02_resource_map.json"
    if args.stage == "resource-map":
        if args.chunk_batch:
            if len(ids) != 1:
                raise SystemExit("--chunk-batch requires exactly one document ID")
            _profile_document(resources, ids[0], _deepseek_pro, args.run_dir / "raw" / f"resource_map__{ids[0]}.json", batch_numbers=[args.chunk_batch], allow_partial=True)
        else:
            build_resource_map(resources, _deepseek_pro, resource_map_path, document_ids=ids)
    elif args.stage == "query-pool":
        build_query_pool(SEED_PATH, _load(resource_map_path), _deepseek_pro, args.run_dir / "03_query_pool.json")
    elif args.stage == "relevance":
        build_relevance_matrix(_load(args.run_dir / "03_query_pool.json"), _load(resource_map_path), _deepseek_pro, args.run_dir / "04_relevance_matrix.json", scope_ids=ids)
    elif args.stage == "blueprints":
        select_blueprints(_load(args.run_dir / "03_query_pool.json"), _load(args.run_dir / "04_relevance_matrix.json"), args.run_dir / "05_case_blueprints.json")
    elif args.stage == "chains":
        build_evidence_chains(_load(args.run_dir / "05_case_blueprints.json"), resources, _deepseek_pro, args.run_dir / "06_evidence_chains.json")
    else:
        promote_pilot(_load(args.run_dir / "05_case_blueprints.json"), _load(args.run_dir / "06_evidence_chains.json"), resources, args.run_dir / "07_promoted_benchmark.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
