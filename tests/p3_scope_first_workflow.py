"""P3 v2 scope-first business-query benchmark workflow.

V2 reuses the exported resources and v1 Resource Map, but deliberately does
not consume v1 query labels.  A scope is either one whole document or an
explicitly permitted document pair.  LLMs see every chunk in that scope and
may propose business queries only with a non-answer evidence plan; deterministic
checks and a second restricted audit decide what becomes a benchmark contract.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.p3_resource_workflow import (  # v1 remains an immutable resource-layer dependency
    RESOURCE_DIR,
    _deepseek_pro,
    _json_call,
    _load,
    _load_if_valid,
    _now,
    _write,
)
from tests.p3_testset_builder import load_resources


RUN_DIR = ROOT / "tests" / "artifacts" / "p3_scope_first_v2"
V1_RUN_DIR = ROOT / "tests" / "artifacts" / "p3_resource_first"
LLM = Callable[[str], str]
CASE_TYPES = {"single_chunk", "cross_chunk", "cross_document", "insufficient_evidence"}
OUTCOMES = {"sufficient_evidence", "upstream_evidence_needed"}
RELATION_TYPES = {
    "strategy_to_execution", "upstream_to_downstream", "state_to_diagnosis",
    "diagnosis_to_action", "action_to_result", "target_to_actual",
    "summary_to_detail", "plan_to_risk_constraint", "comparable_contrast",
    "market_to_brand_strategy",
}
BUSINESS_GUIDANCE = [
    "新品上市操盘", "稳定期运营", "退市操盘", "行业/市场洞察", "产品线洞察",
    "产品矩阵优化", "定价与渠道秩序", "PSI库存与交付治理", "DTC渠道运营",
    "Marketplace渠道运营", "营销项目管理", "合规与本地化", "客户运营", "ToB拓客",
]
RELATION_GUIDE = {
    "strategy_to_execution": "策略、SKU定位或计划，与后续实际经营/渠道结果相连接。",
    "upstream_to_downstream": "预算、供给、定价或计划，与库存、渠道或销售结果相连接。",
    "state_to_diagnosis": "经营现状或异常，与文档中已有的诊断相连接。",
    "diagnosis_to_action": "问题诊断，与已有建议、实验或验证指标相连接。",
    "action_to_result": "已执行或计划动作，与后续指标或预期验证相连接。",
    "target_to_actual": "目标、预算或预测，与实际结果和偏差相连接。",
    "summary_to_detail": "总体结论，与SKU、国家、渠道等不可替代的细节相连接。",
    "plan_to_risk_constraint": "方案或打法，与库存、预算、交付、渠道等约束相连接。",
    "comparable_contrast": "同口径对象的比较；必须有可比的指标、周期和维度。",
    "market_to_brand_strategy": "行业/市场外部事实，与品牌策略相连接；不得把市场趋势当作品牌经营事实。",
}
EXTERNAL_MARKERS = ("mcp", "shopify", "google analytics", "google ads", "meta ads", "上传文件", "excel")
PROMPT_VERSION = "v2_1_scope_brief_catalog"


def _compact_resource(chunk: Mapping[str, Any], *, include_content: bool = True) -> dict[str, Any]:
    result = {
        "resource_ref": str(chunk["resource_ref"]),
        "document_id": str(chunk["document_id"]),
        "heading": str(chunk.get("heading_path") or ""),
        "content_type": str(chunk.get("content_type") or ""),
    }
    if include_content:
        result["content"] = str(chunk.get("content") or "")
    return result


def _document_brief(document: Mapping[str, Any]) -> dict[str, Any]:
    return {key: document.get(key) for key in ("document_id", "title", "document_kind", "primary_entities", "entities", "periods", "information_facets", "granularity", "limitations", "valid_join_keys")}


def _normal(values: Iterable[object]) -> set[str]:
    return {" ".join(str(value).casefold().split()) for value in values if str(value).strip()}


def _is_gtm(document: Mapping[str, Any]) -> bool:
    text = " ".join(str(document.get(key) or "") for key in ("title", "document_kind")).casefold()
    return "gtm" in text or "上市" in text or "策划" in text


def _is_weekly(document: Mapping[str, Any]) -> bool:
    text = " ".join(str(document.get(key) or "") for key in ("title", "document_kind")).casefold()
    return "weekly" in text or "周报" in text or "业务总结" in text


def build_scope_registry(resource_map: Mapping[str, Any], resources: list[Mapping[str, Any]], output_path: Path) -> dict[str, Any]:
    """Create six single-document scopes plus only defensible candidate pairs."""
    resources_by_doc: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for chunk in resources:
        resources_by_doc[str(chunk["document_id"])].append(chunk)
    documents = list(resource_map.get("documents") or [])
    documents_by_id = {str(document["document_id"]): document for document in documents}
    scopes: list[dict[str, Any]] = []
    for document in documents:
        document_id = str(document["document_id"])
        chunks = resources_by_doc.get(document_id, [])
        if not chunks:
            continue
        scopes.append({
            "scope_id": f"single:{document_id}", "scope_type": "single_document",
            "allowed_documents": [document_id], "chunk_refs": [str(chunk["resource_ref"]) for chunk in chunks],
            "document_briefs": [_document_brief(document)],
            "shared_subjects": list(document.get("primary_entities") or document.get("entities") or []),
            "join_dimensions": ["entity", "period", "region", "channel", "spu"],
            "relationship_types": ["state_to_diagnosis", "diagnosis_to_action", "action_to_result", "summary_to_detail", "plan_to_risk_constraint"],
            "business_guidance": BUSINESS_GUIDANCE,
            "limitations": list(document.get("limitations") or []),
            "draft_count": 4,
        })
    # Pair discovery is intentionally narrow. Same primary entity plus a GTM
    # and a weekly-report role creates a *candidate* pair, never a permanent
    # assertion that every query needs both documents.
    for index, left in enumerate(documents):
        left_primary = _normal(left.get("primary_entities") or [])
        for right in documents[index + 1:]:
            shared = left_primary & _normal(right.get("primary_entities") or [])
            if not shared or not ((_is_gtm(left) and _is_weekly(right)) or (_is_gtm(right) and _is_weekly(left))):
                continue
            ids = [str(left["document_id"]), str(right["document_id"])]
            chunks = [chunk for document_id in ids for chunk in resources_by_doc.get(document_id, [])]
            scopes.append({
                "scope_id": "pair:" + "__".join(ids), "scope_type": "cross_document_candidate",
                "allowed_documents": ids, "chunk_refs": [str(chunk["resource_ref"]) for chunk in chunks],
                "document_briefs": [_document_brief(documents_by_id[document_id]) for document_id in ids],
                "shared_subjects": sorted(shared), "join_dimensions": ["entity", "spu", "period", "region", "channel"],
                "relationship_types": ["strategy_to_execution", "diagnosis_to_action", "action_to_result", "plan_to_risk_constraint"],
                "business_guidance": BUSINESS_GUIDANCE,
                "limitations": list(left.get("limitations") or []) + list(right.get("limitations") or []),
                "draft_count": 6,
                "pair_status": "candidate_pair",
                "required_seed_query": "Hypershell X Ultra S 在2026年6月中旬处在怎样的新品生命周期阶段？当前社媒营销策略与结果如何？后续建议如何跟进和优化？" if "hypershell" in shared else None,
            })
    payload = {"format": "p3_scope_registry_v2", "generated_at": _now(), "scopes": scopes}
    _write(output_path, payload)
    return payload


def _draft_prompt(scope: Mapping[str, Any], chunks: list[Mapping[str, Any]]) -> str:
    chunks_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        chunks_by_doc[str(chunk["document_id"])].append(_compact_resource(chunk))
    brief = {key: scope.get(key) for key in ("scope_id", "scope_type", "allowed_documents", "document_briefs", "shared_subjects", "join_dimensions", "relationship_types", "business_guidance", "limitations", "draft_count", "required_seed_query")}
    return """You create candidate BUSINESS RETRIEVAL queries for an authorized Feishu benchmark. You are not an answer agent.

WORK IN THIS ORDER:
1. Read the Scope Brief to understand the allowed documents, business objects, limitations and permitted relationship types.
2. Read the Chunk Catalog, grouped by document. Use only explicit facts in it.
3. Propose exactly the requested number of concrete decision questions. A good query binds a business object (brand/SPU/product line where available), a period or explicit time relation, and a decision need. Do not produce a generic report-summary question.
4. For every query, name the smallest candidate evidence refs and their distinct roles. This is a proposal, not an answer or final Gold Chain.

BUSINESS LENSES are only ways to ask useful questions: product launch, steady operation, exit/clearance, market insight, product line, product matrix, pricing/channel order, inventory/fulfillment, DTC, marketplace, marketing project, localization, client operation, ToB. Do not force a lens when no supporting facts exist.

RELATION GUIDE:\n""" + json.dumps({key: RELATION_GUIDE[key] for key in scope.get("relationship_types") or []}, ensure_ascii=False) + """

HARD RULES:
- Use only supplied resource_ref values. Do not request MCP, external data, uploads, forecasts, or answers.
- `cross_chunk` needs at least two refs with non-substitutable roles. `cross_document` needs refs from both allowed documents. `insufficient_evidence` must request a metric/dimension the scope explicitly lacks and name that limitation.
- Separate facts, recommendations, and hypotheses. A recommendation may support a `recommended_action` role but never proves an execution result or causal root cause.
- If required_seed_query is present, include it verbatim as one draft and supply its candidate evidence plan.

Return JSON only, exactly this shape:
{"drafts":[{"query":"...","business_lens":["..."],"relationship_type":"...","decision_question":"...","subject":{"brand":"","spu":"","region":"","channel":""},"period":"","proposed_case_type":"single_chunk|cross_chunk|cross_document|insufficient_evidence","proposed_evidence":[{"resource_ref":"...","role":"..."}],"known_limitations":["..."]}]}

SCOPE BRIEF=""" + json.dumps(brief, ensure_ascii=False) + "\nCHUNK CATALOG=" + json.dumps(chunks_by_doc, ensure_ascii=False)


def _scope_resources(scope: Mapping[str, Any], by_ref: Mapping[str, Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [by_ref[ref] for ref in scope.get("chunk_refs") or [] if ref in by_ref]


def generate_query_drafts(registry: Mapping[str, Any], resources: list[Mapping[str, Any]], llm: LLM, output_path: Path, *, scope_ids: Iterable[str] = ()) -> dict[str, Any]:
    by_ref = {str(chunk["resource_ref"]): chunk for chunk in resources}
    wanted = set(scope_ids)
    existing = _load(output_path).get("drafts", []) if output_path.exists() else []
    by_id = {str(item.get("draft_id")): dict(item) for item in existing if isinstance(item, Mapping)}
    for scope in registry.get("scopes") or []:
        scope_id = str(scope["scope_id"])
        if wanted and scope_id not in wanted:
            continue
        raw_path = output_path.parent / "raw" / f"drafts__{scope_id.replace(':', '_')}__{PROMPT_VERSION}.json"
        response = _load_if_valid(raw_path) or _json_call(llm, _draft_prompt(scope, _scope_resources(scope, by_ref)), raw_path)
        rows = list(response.get("drafts") or [])
        if len(rows) != int(scope["draft_count"]):
            raise ValueError(f"{scope_id}: expected {scope['draft_count']} drafts, got {len(rows)}")
        required_seed = str(scope.get("required_seed_query") or "").strip()
        if required_seed and required_seed not in {str(row.get("query") or "").strip() for row in rows if isinstance(row, Mapping)}:
            raise ValueError(f"{scope_id}: required seed query was not returned verbatim")
        for index, row in enumerate(rows, 1):
            if isinstance(row, Mapping):
                draft = {"draft_id": f"{scope_id}::D{index}", "scope_id": scope_id, **dict(row)}
                by_id[str(draft["draft_id"])] = draft
    payload = {"format": "p3_query_drafts_v2", "generated_at": _now(), "drafts": list(by_id.values())}
    _write(output_path, payload)
    return payload


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(item) for item in value or [] if str(item).strip()]


def validate_draft(draft: Mapping[str, Any], scope: Mapping[str, Any], resources_by_ref: Mapping[str, Mapping[str, Any]]) -> list[str]:
    failures: list[str] = []
    query = str(draft.get("query") or "").strip()
    if not query:
        failures.append("missing query")
    text = query.casefold()
    if any(marker in text for marker in EXTERNAL_MARKERS):
        failures.append("query requires external source or artifact")
    if "预测" in query or "forecast" in text:
        failures.append("query requests forecast")
    if str(draft.get("relationship_type") or "") not in RELATION_TYPES:
        failures.append("invalid relationship_type")
    case_type = str(draft.get("proposed_case_type") or "")
    if case_type not in CASE_TYPES:
        failures.append("invalid proposed_case_type")
    evidence = [item for item in draft.get("proposed_evidence") or [] if isinstance(item, Mapping)]
    refs = {str(item.get("resource_ref") or "") for item in evidence}
    allowed_refs = set(scope.get("chunk_refs") or [])
    if not evidence or not refs:
        failures.append("missing proposed evidence")
    if refs - allowed_refs:
        failures.append("proposed evidence outside scope")
    if any(not str(item.get("role") or "").strip() for item in evidence):
        failures.append("proposed evidence missing role")
    docs = {str(resources_by_ref[ref]["document_id"]) for ref in refs if ref in resources_by_ref}
    if case_type == "cross_chunk" and len(refs) < 2:
        failures.append("cross_chunk needs two refs")
    if case_type == "cross_document" and (scope.get("scope_type") != "cross_document_candidate" or len(docs) < 2):
        failures.append("cross_document needs two scoped documents")
    if case_type == "insufficient_evidence" and not _strings(draft.get("known_limitations")):
        failures.append("insufficient_evidence needs explicit limitation")
    subject = draft.get("subject") or {}
    if not isinstance(subject, Mapping):
        failures.append("subject must be an object")
    return failures


def _audit_prompt(scope: Mapping[str, Any], drafts: list[Mapping[str, Any]], by_ref: Mapping[str, Mapping[str, Any]]) -> str:
    bundle = []
    for draft in drafts:
        refs = [str(item.get("resource_ref") or "") for item in draft.get("proposed_evidence") or [] if isinstance(item, Mapping)]
        bundle.append({"draft": draft, "candidate_chunks": [_compact_resource(by_ref[ref]) for ref in refs if ref in by_ref]})
    brief = {key: scope.get(key) for key in ("scope_id", "scope_type", "allowed_documents", "document_briefs", "relationship_types", "limitations")}
    return """You are a restricted retrieval-contract auditor. Do not answer any user query and do not introduce evidence beyond each draft's candidate chunks.

For every draft, decide whether its proposed evidence can retrieve every required role. Reject broad summary questions, entity/time mismatches, unsupported causal claims, external-data requests, and any cross-document draft whose roles do not require both documents. Accept an insufficient-evidence control only if the candidate chunks or scope limitation show the requested data is genuinely absent.

Return one audit per draft. Scores are integers 0-100. `required_roles` must match proposed roles that are genuinely needed. `forbidden_inference` must list any conclusion that would exceed the chunks.

Return JSON only:
{"audits":[{"draft_id":"...","decision":"accept|revise|reject","relevance_score":0,"testability_score":0,"case_type":"single_chunk|cross_chunk|cross_document|insufficient_evidence","expected_outcome":"sufficient_evidence|upstream_evidence_needed","required_roles":["..."],"forbidden_inference":["..."],"reason":"short evidence rationale","reject_reason":""}]}

SCOPE BRIEF=""" + json.dumps(brief, ensure_ascii=False) + "\nDRAFTS WITH ONLY THEIR CANDIDATE CHUNKS=" + json.dumps(bundle, ensure_ascii=False)


def audit_query_contracts(registry: Mapping[str, Any], drafts_payload: Mapping[str, Any], resources: list[Mapping[str, Any]], llm: LLM, output_path: Path) -> dict[str, Any]:
    by_ref = {str(chunk["resource_ref"]): chunk for chunk in resources}
    by_scope = {str(scope["scope_id"]): scope for scope in registry.get("scopes") or []}
    drafts_by_scope: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for draft in drafts_payload.get("drafts") or []:
        if isinstance(draft, Mapping):
            drafts_by_scope[str(draft.get("scope_id"))].append(draft)
    contracts, rejected = [], []
    for scope_id, drafts in drafts_by_scope.items():
        scope = by_scope[scope_id]
        preflight = {str(draft["draft_id"]): validate_draft(draft, scope, by_ref) for draft in drafts}
        raw_path = output_path.parent / "raw" / f"audit__{scope_id.replace(':', '_')}__{PROMPT_VERSION}.json"
        response = _load_if_valid(raw_path) or _json_call(llm, _audit_prompt(scope, drafts, by_ref), raw_path)
        audits = {str(item.get("draft_id")): item for item in response.get("audits") or [] if isinstance(item, Mapping)}
        for draft in drafts:
            draft_id = str(draft["draft_id"])
            audit = audits.get(draft_id, {})
            decision = str(audit.get("decision") or "reject")
            case_type = str(audit.get("case_type") or draft.get("proposed_case_type") or "")
            outcome = str(audit.get("expected_outcome") or "")
            validation = preflight[draft_id]
            if decision != "accept" or validation or case_type not in CASE_TYPES or outcome not in OUTCOMES:
                rejected.append({"draft_id": draft_id, "scope_id": scope_id, "draft": dict(draft), "audit": dict(audit), "rejection_reasons": validation + ([str(audit.get("reject_reason") or "audit did not accept")] if decision != "accept" else [])})
                continue
            contracts.append({
                "contract_id": f"contract:{draft_id}", "draft_id": draft_id, "scope_id": scope_id,
                "query_surface": str(draft["query"]), "business_lens": _strings(draft.get("business_lens")),
                "relationship_type": str(draft["relationship_type"]), "decision_question": str(draft.get("decision_question") or ""),
                "subject": dict(draft.get("subject") or {}), "period": str(draft.get("period") or ""),
                "proposed_evidence": [dict(item) for item in draft.get("proposed_evidence") or [] if isinstance(item, Mapping)],
                "required_roles": _strings(audit.get("required_roles")), "case_type": case_type,
                "expected_outcome": outcome, "expected_control": "upstream_evidence_needed" if outcome == "upstream_evidence_needed" else "none",
                "forbidden_inference": _strings(audit.get("forbidden_inference")),
                "relevance_score": int(audit.get("relevance_score") or 0), "testability_score": int(audit.get("testability_score") or 0),
                "audit_reason": str(audit.get("reason") or ""), "allowed_documents": list(scope["allowed_documents"]),
                # A missing-data control is deliberately not answerable from the
                # fixed corpus.  Its admission therefore rests on an accepted,
                # scope-grounded limitation rather than an answerability score.
                "control_eligible": outcome == "upstream_evidence_needed" and bool(_strings(draft.get("known_limitations"))) and bool(str(audit.get("reason") or "").strip()),
            })
    payload = {"format": "p3_query_contracts_v2", "generated_at": _now(), "contracts": contracts, "rejected": rejected}
    _write(output_path, payload)
    return payload


def select_v2_blueprints(contracts_payload: Mapping[str, Any], output_path: Path, *, pilot_size: int = 8) -> dict[str, Any]:
    def is_eligible(item: Mapping[str, Any]) -> bool:
        if item.get("expected_outcome") == "upstream_evidence_needed":
            return bool(item.get("control_eligible"))
        return int(item.get("relevance_score") or 0) >= 70 and int(item.get("testability_score") or 0) >= 70

    eligible = [dict(item) for item in contracts_payload.get("contracts") or [] if is_eligible(item)]
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in eligible:
        by_type[str(item["case_type"])].append(item)
    for values in by_type.values():
        values.sort(key=lambda item: (item["relevance_score"] + item["testability_score"], item["relationship_type"]), reverse=True)
    quotas = {"cross_document": 2, "cross_chunk": 3, "single_chunk": 1, "insufficient_evidence": 2}
    selected: list[dict[str, Any]] = []
    used_relationships: set[str] = set()
    for case_type, amount in quotas.items():
        available = by_type.get(case_type, [])
        picks: list[dict[str, Any]] = []
        for item in available:
            if item["relationship_type"] not in used_relationships:
                picks.append(item)
                used_relationships.add(item["relationship_type"])
            if len(picks) == amount:
                break
        for item in available:
            if len(picks) == amount:
                break
            if item not in picks:
                picks.append(item)
        if len(picks) < amount:
            raise ValueError(f"pilot coverage gate not met: {case_type} needs {amount}, found {len(picks)}")
        selected.extend(picks)
    if len(selected) != pilot_size:
        raise ValueError(f"pilot size mismatch: expected {pilot_size}, got {len(selected)}")
    cases = []
    for index, contract in enumerate(selected, 1):
        cases.append({"case_id": f"v2_pilot_{index:02d}", "contract_id": contract["contract_id"], "user_query": contract["query_surface"], **contract})
    payload = {"format": "p3_scope_first_blueprints_v2", "generated_at": _now(), "pilot_size": pilot_size, "cases": cases}
    _write(output_path, payload)
    return payload


def validate_v2_chain(case: Mapping[str, Any], chain: Mapping[str, Any], by_ref: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    if case["expected_outcome"] == "upstream_evidence_needed":
        return {"case_id": case["case_id"], "minimal_chain": [], "chain_logic": "fixed corpus lacks required upstream evidence", "entity": "", "period": "", "review_required": False}
    allowed_docs = set(case["allowed_documents"])
    items = []
    for item in chain.get("minimal_chain") or []:
        if not isinstance(item, Mapping):
            continue
        ref, anchor = str(item.get("resource_ref") or ""), str(item.get("anchor") or "").strip()
        resource = by_ref.get(ref)
        # JSON model output normalizes CRLF to LF.  Preserve the model anchor,
        # but compare canonical line endings so a Windows export is not rejected
        # for a transport-only difference.
        content = str(resource.get("content") or "").replace("\r\n", "\n")
        if resource and resource["document_id"] in allowed_docs and anchor and anchor.replace("\r\n", "\n") in content:
            items.append({"resource_ref": ref, "document_id": resource["document_id"], "content_sha256": resource["content_sha256"], "role": str(item.get("role") or "evidence"), "anchor": anchor})
    roles = {item["role"] for item in items}
    required = set(case.get("required_roles") or [])
    if not items or not required.issubset(roles):
        raise ValueError(f"{case['case_id']}: missing required gold evidence roles")
    refs, docs = {item["resource_ref"] for item in items}, {item["document_id"] for item in items}
    if case["case_type"] == "cross_chunk" and len(refs) < 2:
        raise ValueError(f"{case['case_id']}: cross_chunk requires two refs")
    if case["case_type"] == "cross_document" and (len(docs) < 2 or not chain.get("entity") or not chain.get("period")):
        raise ValueError(f"{case['case_id']}: cross_document requires two documents, entity, and period")
    return {"case_id": case["case_id"], "minimal_chain": items, "chain_logic": str(chain.get("chain_logic") or ""), "entity": str(chain.get("entity") or ""), "period": str(chain.get("period") or ""), "review_required": case["case_type"] == "cross_document"}


def build_v2_gold_chains(
    blueprints: Mapping[str, Any], resources: list[Mapping[str, Any]], llm: LLM, output_path: Path,
    *, repair_case_ids: set[str] | None = None,
) -> dict[str, Any]:
    by_ref = {str(chunk["resource_ref"]): chunk for chunk in resources}
    chains = []
    for case in blueprints.get("cases") or []:
        if case["expected_outcome"] == "upstream_evidence_needed":
            chains.append(validate_v2_chain(case, {}, by_ref))
            continue
        scope_chunks = [chunk for chunk in resources if chunk["document_id"] in set(case["allowed_documents"])]
        chunks_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for chunk in scope_chunks:
            chunks_by_doc[str(chunk["document_id"])].append(_compact_resource(chunk))
        repair_notice = "" if str(case["case_id"]) not in (repair_case_ids or set()) else """
This is an explicit repair after a previous response used non-contiguous or paraphrased anchors. Choose an anchor as one continuous, verbatim substring. Do not combine table rows that are separated in the source or change their order.
"""
        prompt = """You are the Gold Evidence Chain builder in a retrieve-only benchmark.
Do not answer, advise, summarize, or introduce facts. Select the MINIMUM supplied chunks needed to recover every required evidence role.

Hard rules:
- resource_ref must be supplied below; anchor must be an exact short substring of that chunk.
- role must be copied EXACTLY from required_roles (do not paraphrase or invent a role).
- each selected role must be independently necessary; omit redundant chunks.
- obey forbidden_inference. A chain is evidence, not an answer.
- for cross_document, include evidence from both documents and state the shared entity plus the valid period/time relation.
- Return JSON only: {minimal_chain:[{resource_ref,role,anchor}],chain_logic,entity,period}.
""" + repair_notice + """

Contract=""" + json.dumps({key: case.get(key) for key in ("case_id", "user_query", "relationship_type", "subject", "period", "required_roles", "case_type", "forbidden_inference")}, ensure_ascii=False) + "\n\nFull allowed chunk catalog, grouped by document=" + json.dumps(chunks_by_doc, ensure_ascii=False)
        suffix = "__repair_1" if str(case["case_id"]) in (repair_case_ids or set()) else ""
        raw_path = output_path.parent / "raw" / f"chain__{case['case_id']}{suffix}.json"
        response = _load_if_valid(raw_path) or _json_call(llm, prompt, raw_path)
        chains.append(validate_v2_chain(case, response, by_ref))
    payload = {"format": "p3_gold_chains_v2", "generated_at": _now(), "chains": chains}
    _write(output_path, payload)
    return payload


def promote_v2(blueprints: Mapping[str, Any], chains: Mapping[str, Any], output_path: Path) -> dict[str, Any]:
    by_case = {str(item["case_id"]): item for item in chains.get("chains") or []}
    cases = []
    for blueprint in blueprints.get("cases") or []:
        chain = by_case.get(str(blueprint["case_id"]))
        if not chain:
            raise ValueError(f"missing chain: {blueprint['case_id']}")
        review_required = bool(chain["review_required"])
        cases.append({**blueprint, "expected_chain": chain["minimal_chain"], "chain_logic": chain["chain_logic"], "evidence_shape": {"cross_chunk": blueprint["case_type"] in {"cross_chunk", "cross_document"}, "cross_document": blueprint["case_type"] == "cross_document", "business_entity": chain["entity"], "period": chain["period"]}, "review_required": review_required, "review_status": "pending" if review_required else "not_required"})
    payload = {"format": "p3_scope_first_benchmark_v2", "benchmark_state": "pilot_review_required", "generated_at": _now(), "scope": "Feishu retrieve-only", "resource_manifest": str(RESOURCE_DIR / "resource_manifest.json"), "cases": cases}
    _write(output_path, payload)
    return payload


def write_review_packet(benchmark: Mapping[str, Any], output_path: Path) -> None:
    pending = [case for case in benchmark.get("cases") or [] if case.get("review_required")]
    lines = ["# P3 v2 跨文档 Gold Chain 审核包", "", "仅审核跨文档 case：实体/时间或策略—执行关系、两份文档角色不可替代、chain 最小性，以及推测与事实的边界。", ""]
    for case in pending:
        lines += [f"## {case['case_id']}", "", f"> {case['user_query']}", "", f"- 关系：`{case['relationship_type']}`", f"- 文档：`{', '.join(case['allowed_documents'])}`", f"- 禁止推断：{'; '.join(case.get('forbidden_inference') or ['无'])}", "", "| Ref | Role | Anchor |", "|---|---|---|"]
        for item in case.get("expected_chain") or []:
            lines.append(f"| `{item['resource_ref']}` | {item['role']} | {item['anchor'].replace('|', '\\|')} |")
        lines += ["", "- [ ] `approved`", "- [ ] `approved_with_reduction`（请注明删除的 ref/role）", "- [ ] `rejected`（请注明：not_cross_document / invalid_join / not_minimal / query_too_broad / unsupported_inference）", ""]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def write_benchmark_report(blueprints: Mapping[str, Any], chains: Mapping[str, Any], benchmark: Mapping[str, Any] | None, output_path: Path) -> None:
    """Write a reviewer-friendly body-free report of the frozen benchmark."""
    chain_by_case = {str(item.get("case_id")): item for item in chains.get("chains") or [] if isinstance(item, Mapping)}
    benchmark_by_case = {str(item.get("case_id")): item for item in (benchmark or {}).get("cases") or [] if isinstance(item, Mapping)}
    lines = ["# P3 v2 Scope-first Benchmark Report", "", "此报告只列 Query Contract、Gold Evidence Chain 和审核状态；不含资源正文或模型答案。", ""]
    for case in blueprints.get("cases") or []:
        chain = chain_by_case.get(str(case["case_id"]), {})
        promoted = benchmark_by_case.get(str(case["case_id"]), {})
        lines += [f"## {case['case_id']}", "", f"> {case['user_query']}", "", f"- 类型：`{case['case_type']}`", f"- 业务关系：`{case['relationship_type']}`", f"- 业务 lens：{', '.join(case.get('business_lens') or [])}", f"- 允许文档：{', '.join(case['allowed_documents'])}", f"- 预期：`{case['expected_outcome']}` / control=`{case['expected_control']}`", f"- 所需角色：{', '.join(case.get('required_roles') or [])}", f"- 禁止推断：{'; '.join(case.get('forbidden_inference') or ['无'])}", f"- 跨文档审核：`{promoted.get('review_status', '未生成')}`", "", "| Gold ref | Role | Anchor |", "|---|---|---|"]
        for item in chain.get("minimal_chain") or []:
            anchor = str(item.get("anchor") or "").replace("|", "\\|")
            lines.append(f"| `{item.get('resource_ref')}` | {item.get('role')} | {anchor} |")
        if not chain.get("minimal_chain"):
            lines.append("| — | — | 空链：要求上游证据或尚未完成 Gold Chain |")
        lines.append("")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, choices=("scope-registry", "query-drafts", "audit", "blueprints", "chains", "promote", "review-packet", "report"))
    parser.add_argument("--resource-dir", type=Path, default=RESOURCE_DIR)
    parser.add_argument("--run-dir", type=Path, default=RUN_DIR)
    parser.add_argument("--resource-map", type=Path, default=V1_RUN_DIR / "02_resource_map.json")
    parser.add_argument("--scope-ids", default="", help="comma-separated full scope IDs; recovery boundary only, never chunk splitting")
    parser.add_argument("--repair-chain-case-ids", default="", help="explicit comma-separated chain repairs; each still receives its complete scope")
    args = parser.parse_args()
    args.run_dir.mkdir(parents=True, exist_ok=True)
    resources = load_resources(args.resource_dir)
    scope_path = args.run_dir / "02b_scope_registry.json"
    drafts_path = args.run_dir / "03_query_drafts.json"
    contracts_path = args.run_dir / "04_query_contracts.json"
    blueprints_path = args.run_dir / "05_case_blueprints.json"
    chains_path = args.run_dir / "06_evidence_chains.json"
    benchmark_path = args.run_dir / "07_promoted_benchmark.json"
    if args.stage == "scope-registry":
        build_scope_registry(_load(args.resource_map), resources, scope_path)
    elif args.stage == "query-drafts":
        generate_query_drafts(_load(scope_path), resources, _deepseek_pro, drafts_path, scope_ids=[item.strip() for item in args.scope_ids.split(",") if item.strip()])
    elif args.stage == "audit":
        audit_query_contracts(_load(scope_path), _load(drafts_path), resources, _deepseek_pro, contracts_path)
    elif args.stage == "blueprints":
        select_v2_blueprints(_load(contracts_path), blueprints_path)
    elif args.stage == "chains":
        repair_case_ids = {value.strip() for value in args.repair_chain_case_ids.split(",") if value.strip()}
        build_v2_gold_chains(_load(blueprints_path), resources, _deepseek_pro, chains_path, repair_case_ids=repair_case_ids)
    elif args.stage == "promote":
        promote_v2(_load(blueprints_path), _load(chains_path), benchmark_path)
    elif args.stage == "review-packet":
        write_review_packet(_load(benchmark_path), args.run_dir / "08_cross_document_review.md")
    else:
        benchmark = _load(benchmark_path) if benchmark_path.exists() else None
        write_benchmark_report(_load(blueprints_path), _load(chains_path), benchmark, args.run_dir / "09_benchmark_report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
