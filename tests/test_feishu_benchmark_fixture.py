import json
import unittest
from pathlib import Path


class FeishuBenchmarkFixtureTests(unittest.TestCase):
    def test_snapshot_is_not_a_query_benchmark(self):
        path = Path(__file__).parent / "fixtures" / "feishu_cross_doc_fixture.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(fixture["snapshot"]), 4)
        self.assertTrue(fixture["do_not_use_as_query_benchmark"])
        self.assertNotIn("cases", fixture)
        self.assertIn("only form_cross_document_evidence_when_business_entity_and_period_match", fixture["required_behaviors"])

    def test_query_first_manifest_requires_evidence_chain_or_explicit_gap(self):
        path = Path(__file__).parent / "fixtures" / "feishu_query_first_benchmark.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(fixture["labeling_mode"], "query-first evidence chain")
        self.assertGreaterEqual(len(fixture["cases"]), 6)
        for case in fixture["cases"]:
            self.assertEqual(case["user_query"], case["provenance"]["source_span"])
            self.assertTrue(case["expected_chain"] or case["replan_expectation"]["decision"] == "upstream_evidence_needed")
            self.assertIn(case["direct_round"]["expected_outcome"], {"sufficient_evidence", "candidates_but_no_sufficient_evidence", "zero_candidates"})

    def test_upstream_cases_explicitly_refuse_direct_answering(self):
        path = Path(__file__).parent / "fixtures" / "feishu_query_first_benchmark.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        upstream = [case for case in fixture["cases"] if case["replan_expectation"]["decision"] == "upstream_evidence_needed"]
        self.assertGreaterEqual(len(upstream), 4)
        for case in upstream:
            self.assertEqual(case["direct_round"]["expected_outcome"], "candidates_but_no_sufficient_evidence")
            self.assertTrue(case["direct_round"]["must_not_answer_from_direct_evidence"])
            self.assertIn("raw_evidence", case["replan_expectation"]["allowed_query_intents"])


if __name__ == "__main__":
    unittest.main()
