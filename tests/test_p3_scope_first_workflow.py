import json
import tempfile
import unittest
from pathlib import Path

from tests.p3_scope_first_workflow import (
    audit_query_contracts,
    build_scope_registry,
    select_v2_blueprints,
    validate_draft,
    validate_v2_chain,
)


def resource(ref, document_id, content, title="Report"):
    return {
        "resource_ref": ref, "document_id": document_id, "document_title": title,
        "heading_path": "H", "content_type": "text", "content": content,
        "content_sha256": "hash-" + ref,
    }


class ScopeFirstWorkflowTests(unittest.TestCase):
    def test_scope_registry_keeps_singles_and_only_gtm_weekly_candidate_pair(self):
        resources = [resource("gtm:C1", "gtm", "strategy", "Hypershell GTM"), resource("weekly:C1", "weekly", "result", "HYPERSHELL weekly report"), resource("market:C1", "market", "mention", "Market whitepaper")]
        resource_map = {"documents": [
            {"document_id": "gtm", "title": "Hypershell GTM", "document_kind": "GTM report", "primary_entities": ["Hypershell"], "limitations": []},
            {"document_id": "weekly", "title": "HYPERSHELL weekly report", "document_kind": "weekly_business_report", "primary_entities": ["HYPERSHELL"], "limitations": []},
            {"document_id": "market", "title": "Market whitepaper", "document_kind": "market report", "primary_entities": [], "limitations": []},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            registry = build_scope_registry(resource_map, resources, Path(tmp) / "scopes.json")
        pairs = [scope for scope in registry["scopes"] if scope["scope_type"] == "cross_document_candidate"]
        self.assertEqual(3, len([scope for scope in registry["scopes"] if scope["scope_type"] == "single_document"]))
        self.assertEqual(1, len(pairs))
        self.assertEqual({"gtm", "weekly"}, set(pairs[0]["allowed_documents"]))

    def test_draft_validation_rejects_external_ref_and_invalid_cross_document(self):
        chunks = {"a:C1": resource("a:C1", "a", "x")}
        scope = {"scope_type": "single_document", "chunk_refs": ["a:C1"]}
        draft = {"query": "Use MCP Shopify", "relationship_type": "strategy_to_execution", "proposed_case_type": "cross_document", "subject": {}, "proposed_evidence": [{"resource_ref": "out:C1", "role": "x"}], "known_limitations": []}
        failures = validate_draft(draft, scope, chunks)
        self.assertIn("query requires external source or artifact", failures)
        self.assertIn("proposed evidence outside scope", failures)
        self.assertIn("cross_document needs two scoped documents", failures)

    def test_audit_contracts_keeps_only_accepted_preflight_valid_drafts(self):
        chunks = [resource("a:C1", "a", "state"), resource("a:C2", "a", "action")]
        registry = {"scopes": [{"scope_id": "single:a", "scope_type": "single_document", "allowed_documents": ["a"], "chunk_refs": ["a:C1", "a:C2"], "relationship_types": ["diagnosis_to_action"]}]}
        drafts = {"drafts": [
            {"draft_id": "d1", "scope_id": "single:a", "query": "What action follows?", "business_lens": ["DTC"], "relationship_type": "diagnosis_to_action", "decision_question": "x", "subject": {}, "period": "", "proposed_case_type": "cross_chunk", "proposed_evidence": [{"resource_ref": "a:C1", "role": "diagnosis"}, {"resource_ref": "a:C2", "role": "action"}], "known_limitations": []},
            {"draft_id": "d2", "scope_id": "single:a", "query": "MCP data", "business_lens": [], "relationship_type": "diagnosis_to_action", "decision_question": "x", "subject": {}, "period": "", "proposed_case_type": "single_chunk", "proposed_evidence": [{"resource_ref": "a:C1", "role": "x"}], "known_limitations": []},
        ]}
        response = json.dumps({"audits": [
            {"draft_id": "d1", "decision": "accept", "relevance_score": 90, "testability_score": 90, "case_type": "cross_chunk", "expected_outcome": "sufficient_evidence", "required_roles": ["diagnosis", "action"], "forbidden_inference": [], "reason": "supported", "reject_reason": ""},
            {"draft_id": "d2", "decision": "accept", "relevance_score": 90, "testability_score": 90, "case_type": "single_chunk", "expected_outcome": "sufficient_evidence", "required_roles": ["x"], "forbidden_inference": [], "reason": "", "reject_reason": ""},
        ]})
        with tempfile.TemporaryDirectory() as tmp:
            result = audit_query_contracts(registry, drafts, chunks, lambda _: response, Path(tmp) / "contracts.json")
        self.assertEqual(["contract:d1"], [item["contract_id"] for item in result["contracts"]])
        self.assertEqual(1, len(result["rejected"]))

    def test_selection_requires_exact_v2_shape(self):
        contracts = []
        for case_type, amount in (("cross_document", 2), ("cross_chunk", 3), ("single_chunk", 1), ("insufficient_evidence", 2)):
            for index in range(amount):
                contracts.append({"contract_id": f"{case_type}-{index}", "query_surface": f"q-{case_type}-{index}", "relationship_type": f"r-{case_type}-{index}", "case_type": case_type, "expected_outcome": "upstream_evidence_needed" if case_type == "insufficient_evidence" else "sufficient_evidence", "expected_control": "upstream_evidence_needed" if case_type == "insufficient_evidence" else "none", "control_eligible": case_type == "insufficient_evidence", "relevance_score": 80, "testability_score": 80, "allowed_documents": ["a", "b"] if case_type == "cross_document" else ["a"], "required_roles": [], "subject": {}, "period": "", "proposed_evidence": [], "business_lens": [], "decision_question": "", "forbidden_inference": [], "audit_reason": ""})
        with tempfile.TemporaryDirectory() as tmp:
            result = select_v2_blueprints({"contracts": contracts}, Path(tmp) / "blueprints.json")
        self.assertEqual(8, len(result["cases"]))

    def test_selection_admits_a_scope_grounded_missing_data_control_without_answerability_score(self):
        contracts = []
        for case_type, amount in (("cross_document", 2), ("cross_chunk", 3), ("single_chunk", 1)):
            for index in range(amount):
                contracts.append({"contract_id": f"{case_type}-{index}", "query_surface": "q", "relationship_type": f"r-{case_type}-{index}", "case_type": case_type, "expected_outcome": "sufficient_evidence", "relevance_score": 80, "testability_score": 80, "allowed_documents": ["a"], "required_roles": []})
        for index in range(2):
            contracts.append({"contract_id": f"control-{index}", "query_surface": "missing data q", "relationship_type": f"control-{index}", "case_type": "insufficient_evidence", "expected_outcome": "upstream_evidence_needed", "relevance_score": 50, "testability_score": 30, "control_eligible": True, "allowed_documents": ["a"], "required_roles": []})
        with tempfile.TemporaryDirectory() as tmp:
            result = select_v2_blueprints({"contracts": contracts}, Path(tmp) / "blueprints.json")
        self.assertEqual(2, sum(case["case_type"] == "insufficient_evidence" for case in result["cases"]))

    def test_v2_chain_requires_contract_roles_and_two_documents(self):
        by_ref = {"a:C1": resource("a:C1", "a", "strategy evidence"), "b:C1": resource("b:C1", "b", "result evidence")}
        case = {"case_id": "x", "expected_outcome": "sufficient_evidence", "allowed_documents": ["a", "b"], "case_type": "cross_document", "required_roles": ["strategy", "result"]}
        chain = {"minimal_chain": [{"resource_ref": "a:C1", "role": "strategy", "anchor": "strategy evidence"}, {"resource_ref": "b:C1", "role": "result", "anchor": "result evidence"}], "entity": "H", "period": "W1"}
        result = validate_v2_chain(case, chain, by_ref)
        self.assertTrue(result["review_required"])

    def test_v2_chain_accepts_newline_canonical_anchor_but_not_content_changes(self):
        by_ref = {"a:C1": resource("a:C1", "a", "line one\r\nline two")}
        case = {"case_id": "x", "expected_outcome": "sufficient_evidence", "allowed_documents": ["a"], "case_type": "single_chunk", "required_roles": ["fact"]}
        chain = {"minimal_chain": [{"resource_ref": "a:C1", "role": "fact", "anchor": "line one\nline two"}]}
        self.assertEqual(1, len(validate_v2_chain(case, chain, by_ref)["minimal_chain"]))


if __name__ == "__main__":
    unittest.main()
