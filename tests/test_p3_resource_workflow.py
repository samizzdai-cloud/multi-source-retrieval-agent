import json
import tempfile
import unittest
from pathlib import Path

from tests.p3_resource_workflow import (
    build_resource_map,
    _seed_exclusion_reason,
    _normalize_score_row,
    derive_scopes,
    promote_pilot,
    select_blueprints,
    validate_evidence_chain,
)
from tools.core.benchmark_contract import evaluate_resource_first_case


def _resource(ref, doc, content, title="Report"):
    return {"resource_ref": ref, "document_id": doc, "document_title": title, "heading_path": "H",
            "content_type": "text", "content_sha256": "hash-" + ref, "anchor": content[:30], "content": content}


class P3ResourceWorkflowTests(unittest.TestCase):
    def test_resource_map_retains_unclassified_chunks(self):
        resources = [_resource("a:C1", "a", "A"), _resource("a:C2", "a", "B")]
        response = json.dumps({"document": {"entities": ["A"]}, "chunks": [{"resource_ref": "a:C1", "capabilities": ["fact"]}]})
        with tempfile.TemporaryDirectory() as tmp:
            result = build_resource_map(resources, lambda _: response, Path(tmp) / "map.json")
        self.assertEqual(2, len(result["documents"][0]["chunks"]))

    def test_resource_map_preserves_branded_title_entity_when_llm_omits_it(self):
        resources = [_resource("a:C1", "a", "weekly metrics", title="HYPERSHELL business report")]
        response = json.dumps({"document": {"entities": []}, "chunks": []})
        with tempfile.TemporaryDirectory() as tmp:
            result = build_resource_map(resources, lambda _: response, Path(tmp) / "map.json")
        self.assertIn("HYPERSHELL", result["documents"][0]["entities"])

    def test_seed_gate_excludes_external_and_unanchored_queries(self):
        resource_map = {"documents": [{"primary_entities": ["HYPERSHELL"]}]}
        self.assertIsNotNone(_seed_exclusion_reason("查询 Hypershell 销量，使用 MCP Shopify", resource_map))
        self.assertIsNotNone(_seed_exclusion_reason("CVR 低如何排查", resource_map))
        self.assertIsNotNone(_seed_exclusion_reason("分析 Hypershell 5.20-5.25 数据", resource_map))
        self.assertIsNone(_seed_exclusion_reason("查看 Hypershell 最近策略和结果", resource_map))

    def test_score_normalizer_converts_fraction_and_never_retains_answer_text(self):
        row = _normalize_score_row({"relevance_score": 0.8, "testability_score": 0.75, "case_type": "single_document", "required_capabilities": ["strategy", "performance"], "expected_outcome": "a natural language answer", "reason": "supported"}, {"kind": "single"})
        self.assertEqual((80, 75, "cross_chunk", "sufficient_evidence"), (row["relevance_score"], row["testability_score"], row["case_type"], row["expected_outcome"]))

    def test_intended_missing_data_becomes_negative_control_not_scope_rejection(self):
        row = _normalize_score_row({"reject_reason": "tables are truncated and no visible metrics"}, {"kind": "single", "document_ids": ["a"]}, {"suggested_documents": ["a"]})
        self.assertTrue(row["control_eligible"])
        self.assertEqual("upstream_evidence_needed", row["expected_outcome"])

    def test_scope_requires_shared_entity_and_period_or_strategy_relation(self):
        resource_map = {"documents": [
            {"document_id": "a", "entities": ["H"], "primary_entities": ["H"], "periods": ["W1"], "document_kind": "weekly_report"},
            {"document_id": "b", "entities": ["H"], "primary_entities": ["H"], "periods": ["W1"], "document_kind": "gtm_report"},
            {"document_id": "c", "entities": ["N"], "periods": ["W1"], "document_kind": "weekly_report"},
        ]}
        pairs = [item for item in derive_scopes(resource_map) if item["kind"] == "pair"]
        self.assertEqual([["a", "b"]], [item["document_ids"] for item in pairs])

    def test_scope_accepts_realistic_gtm_to_weekly_name_without_shared_period(self):
        resource_map = {"documents": [
            {"document_id": "gtm", "entities": ["Hypershell"], "primary_entities": ["Hypershell"], "periods": ["launch"], "document_kind": "GTM research report"},
            {"document_id": "weekly", "entities": ["hypershell"], "primary_entities": ["hypershell"], "periods": ["2026-W25"], "document_kind": "weekly_business_report"},
            {"document_id": "whitepaper", "entities": ["Hypershell"], "primary_entities": [], "periods": ["market-baseline"], "document_kind": "market whitepaper"},
        ]}
        pairs = [item["document_ids"] for item in derive_scopes(resource_map) if item["kind"] == "pair"]
        self.assertEqual([["gtm", "weekly"]], pairs)

    def test_cross_document_chain_needs_entity_period_and_two_documents(self):
        case = {"case_id": "x", "case_type": "cross_document", "expected_outcome": "sufficient_evidence", "allowed_documents": ["a", "b"]}
        resources = {"a:C1": _resource("a:C1", "a", "sales 10"), "b:C1": _resource("b:C1", "b", "strategy")}
        with self.assertRaises(ValueError):
            validate_evidence_chain(case, {"minimal_chain": [{"resource_ref": "a:C1", "anchor": "sales 10"}]}, resources)
        result = validate_evidence_chain(case, {"minimal_chain": [{"resource_ref": "a:C1", "role": "actual", "anchor": "sales 10"}, {"resource_ref": "b:C1", "role": "strategy", "anchor": "strategy"}], "entity": "H", "period": "W1"}, resources)
        self.assertTrue(result["review_required"])

    def test_blueprint_selection_rejects_insufficient_complex_coverage(self):
        pool = {"queries": [{"query_id": "q1", "query": "x", "origin": "real_seed"}]}
        matrix = {"rows": [{"query_id": "q1", "scope_id": "a", "document_ids": ["a"], "relevance_score": 90, "testability_score": 90, "case_type": "single_chunk", "expected_outcome": "sufficient_evidence", "required_capabilities": [], "reason": "x", "reject_reason": ""}]}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                select_blueprints(pool, matrix, Path(tmp) / "blueprints.json")

    def test_promotion_marks_cross_document_for_review(self):
        blueprints = {"cases": [{"case_id": "p1", "query_id": "q", "user_query": "x", "origin": "real_seed", "provenance": {}, "case_type": "cross_document", "allowed_documents": ["a", "b"], "required_capabilities": [], "expected_outcome": "sufficient_evidence", "expected_control": "none"}]}
        chains = {"chains": [{"case_id": "p1", "minimal_chain": [{"resource_ref": "a:C1", "document_id": "a", "content_sha256": "h", "role": "x", "anchor": "x"}, {"resource_ref": "b:C1", "document_id": "b", "content_sha256": "h", "role": "y", "anchor": "y"}], "chain_logic": "join", "entity": "H", "period": "W1", "review_required": True}]}
        with tempfile.TemporaryDirectory() as tmp:
            result = promote_pilot(blueprints, chains, [], Path(tmp) / "pilot.json")
        self.assertTrue(result["cases"][0]["review_required"])

    def test_runtime_contract_matches_semantic_anchor_not_chunk_id(self):
        case = {"case_id": "p1", "expected_outcome": "sufficient_evidence", "expected_control": "none", "expected_chain": [{"role": "sales", "anchor": "销量 10"}]}
        verdict = evaluate_resource_first_case(case, final_chunks=[{"chunk_id": "runtime-new-id", "text": "本周销量 10"}], replan_decision="none")
        self.assertTrue(verdict.passed)


if __name__ == "__main__":
    unittest.main()
