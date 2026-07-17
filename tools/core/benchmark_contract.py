"""Query-first benchmark contract checks for the GTM retrieval loop.

The benchmark intentionally judges retrieval/evaluation behavior, not answer
text.  A case can require an upstream replan even when the first direct search
found useful diagnostic chunks.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class BenchmarkVerdict:
    case_id: str
    passed: bool
    failures: tuple[str, ...] = ()


def load_query_first_cases(path: str | Path) -> list[dict]:
    """Load only a query-first fixture and reject synthetic metadata fixtures."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("labeling_mode") != "query-first evidence chain":
        raise ValueError("fixture is not a query-first benchmark")
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ValueError("query-first benchmark has no cases")
    for case in cases:
        if case.get("user_query") != (case.get("provenance") or {}).get("source_span"):
            raise ValueError(f"{case.get('id', '<unknown>')}: query is not a verbatim recorded source span")
    return cases


def validate_promoted_benchmark(payload: Mapping[str, Any], *, minimum_cases: int = 16) -> tuple[str, ...]:
    """Return promotion-gate failures for a live, query-first benchmark.

    Candidate registries intentionally fail this gate.  Promotion requires a
    body-free live snapshot reference for every case and explicit proof for
    every cross-document chain that business entity and period agree.
    """
    cases = payload.get("cases")
    failures: list[str] = []
    if payload.get("benchmark_state") != "promoted_live_labels":
        failures.append("benchmark_state is not promoted_live_labels")
    if not isinstance(cases, list) or len(cases) < minimum_cases:
        failures.append(f"requires at least {minimum_cases} cases")
        return tuple(failures)
    for case in cases:
        case_id = str(case.get("id", "<unknown>"))
        provenance = case.get("provenance") or {}
        if not provenance.get("record_id"):
            failures.append(f"{case_id}: missing stable record_id")
        if case.get("user_query") != provenance.get("source_span"):
            failures.append(f"{case_id}: query is not verbatim source span")
        if not case.get("live_snapshot"):
            failures.append(f"{case_id}: missing live snapshot")
        chain = [item for item in case.get("expected_chain", []) if isinstance(item, Mapping)]
        evidence_shape = case.get("evidence_shape") or {}
        if chain and len({item.get("chunk_id") for item in chain}) < 2 and evidence_shape.get("cross_chunk"):
            failures.append(f"{case_id}: cross_chunk requires at least two distinct chunks")
        if evidence_shape.get("cross_document"):
            documents = {item.get("document_id") for item in chain if item.get("document_id")}
            if len(documents) < 2:
                failures.append(f"{case_id}: cross_document requires two document IDs")
            if not evidence_shape.get("business_entity") or not evidence_shape.get("period"):
                failures.append(f"{case_id}: cross_document missing entity/period agreement")
    return tuple(failures)


def _normalize_snippet(value: object) -> str:
    return "".join(str(value or "").casefold().split())


def _matching_roles(expected_chain: Iterable[Mapping[str, object]], chunks: Iterable[Mapping[str, Any]]) -> set[str]:
    """Match stable semantic anchors, never implementation-specific chunk IDs."""
    matched: set[str] = set()
    chunk_texts = [_normalize_snippet(chunk.get("text") or chunk.get("content") or "") for chunk in chunks]
    for item in expected_chain:
        snippet = _normalize_snippet(item.get("snippet"))
        role = str(item.get("role") or snippet)
        if snippet and any(snippet in text for text in chunk_texts):
            matched.add(role)
    return matched


def evaluate_case(case: Mapping[str, object], *, direct_chunk_ids: Iterable[str],
                  replan_decision: str, final_chunk_ids: Iterable[str],
                  direct_chunks: Iterable[Mapping[str, Any]] = (),
                  final_chunks: Iterable[Mapping[str, Any]] = ()) -> BenchmarkVerdict:
    """Evaluate a normalized loop result against one case's retrieval contract.

    ``direct_chunk_ids`` are the chunks retained after the first direct intent;
    ``final_chunk_ids`` are chunks accepted by evaluation for answer payload.
    This separation prevents diagnostic chunks from being mistaken for a final
    answer when an upstream replan is required.
    """
    direct = set(direct_chunk_ids)
    final = set(final_chunk_ids)
    expected_chain = [item for item in case.get("expected_chain", []) if isinstance(item, Mapping)]
    required_roles = {str(item.get("role") or item.get("snippet")) for item in expected_chain}
    direct_roles = _matching_roles(expected_chain, direct_chunks)
    final_roles = _matching_roles(expected_chain, final_chunks)
    direct_round = case.get("direct_round") or {}
    expected_outcome = direct_round.get("expected_outcome")
    expected_replan = (case.get("replan_expectation") or {}).get("decision", "none")
    failures: list[str] = []

    if expected_outcome == "sufficient_evidence":
        missing = required_roles - direct_roles
        if missing:
            failures.append(f"direct evidence missing required snippet roles: {sorted(missing)}")
        missing_final = required_roles - final_roles
        if missing_final:
            failures.append(f"final evidence missing required snippet roles: {sorted(missing_final)}")
    elif expected_outcome == "candidates_but_no_sufficient_evidence":
        if required_roles and not direct_roles:
            failures.append("direct diagnostic evidence did not match any expected snippet role")
        if direct_round.get("must_not_answer_from_direct_evidence") and expected_replan == "upstream_evidence_needed":
            if replan_decision != "upstream_evidence_needed":
                failures.append("direct evidence was insufficient but upstream replan was not selected")
    elif expected_outcome == "zero_candidates" and direct:
        failures.append("expected zero direct candidates but direct chunks were retained")
    else:
        failures.append(f"unknown direct-round outcome: {expected_outcome!r}")

    if replan_decision != expected_replan:
        failures.append(f"expected replan={expected_replan!r}, got {replan_decision!r}")
    return BenchmarkVerdict(str(case.get("id", "")), not failures, tuple(failures))
