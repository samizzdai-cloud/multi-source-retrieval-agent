import unittest
import json
from pathlib import Path

from tools.core.benchmark_contract import evaluate_case, load_query_first_cases, validate_promoted_benchmark
from agents.source_agent_base import _build_eval_only_prompt, _build_partial_eval_prompt, _build_plan_eval_prompt, Focus
from tools.core.llm_contracts import build_plan_eval_model, validate_plan_eval_output
from pydantic import BaseModel


FIXTURE = Path(__file__).parent / "fixtures" / "feishu_query_first_benchmark.json"
CANDIDATES = Path(__file__).parent / "fixtures" / "feishu_query_first_candidates.json"


class BenchmarkContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {case["id"]: case for case in load_query_first_cases(FIXTURE)}

    def test_direct_case_requires_all_conjunctive_evidence(self):
        case = self.cases["real_q01"]
        required = [item["chunk_id"] for item in case["expected_chain"]]
        chunks = [{"chunk_id": item["chunk_id"], "text": item["snippet"]} for item in case["expected_chain"]]
        verdict = evaluate_case(case, direct_chunk_ids=required, final_chunk_ids=required,
                                direct_chunks=chunks, final_chunks=chunks, replan_decision="none")
        self.assertTrue(verdict.passed, verdict.failures)
        verdict = evaluate_case(case, direct_chunk_ids=required[:-1], final_chunk_ids=required[:-1],
                                direct_chunks=chunks[:-1], final_chunks=chunks[:-1], replan_decision="none")
        self.assertFalse(verdict.passed)

    def test_upstream_case_cannot_pass_from_diagnostic_chunks(self):
        case = self.cases["real_q02"]
        diagnostic = [item["chunk_id"] for item in case["expected_chain"]]
        chunks = [{"chunk_id": item["chunk_id"], "text": item["snippet"]} for item in case["expected_chain"]]
        verdict = evaluate_case(case, direct_chunk_ids=diagnostic, final_chunk_ids=diagnostic,
                                direct_chunks=chunks, final_chunks=chunks, replan_decision="none")
        self.assertFalse(verdict.passed)
        self.assertIn("upstream replan", " ".join(verdict.failures))
        verdict = evaluate_case(case, direct_chunk_ids=diagnostic, final_chunk_ids=[],
                                direct_chunks=chunks, replan_decision="upstream_evidence_needed")
        self.assertTrue(verdict.passed, verdict.failures)

    def test_loader_rejects_snapshot_as_a_query_fixture(self):
        snapshot = Path(__file__).parent / "fixtures" / "feishu_cross_doc_fixture.json"
        with self.assertRaises(ValueError):
            load_query_first_cases(snapshot)

    def test_p3_registry_has_sixteen_distinct_live_query_candidates(self):
        payload = json.loads(CANDIDATES.read_text(encoding="utf-8"))
        candidates = payload["candidates"]
        self.assertEqual(len(candidates), 16)
        self.assertEqual(len({item["record_id"] for item in candidates}), 16)
        self.assertTrue(all(item["source_span"].strip() for item in candidates))
        self.assertIn("live content-hash snapshot", payload["labeling_requirements"])

    def test_promotion_gate_requires_live_provenance_and_cross_evidence_shape(self):
        case = {
            "id": "p3_q01", "user_query": "q",
            "provenance": {"record_id": "rec1", "source_span": "q"},
            "live_snapshot": "snapshot.json",
            "expected_chain": [
                {"chunk_id": "c1", "document_id": "d1"},
                {"chunk_id": "c2", "document_id": "d2"},
            ],
            "evidence_shape": {"cross_chunk": True, "cross_document": True,
                               "business_entity": "Hypershell", "period": "2026-06"},
        }
        payload = {"benchmark_state": "promoted_live_labels", "cases": [case] * 16}
        self.assertEqual(validate_promoted_benchmark(payload), ())
        broken = dict(case)
        broken["evidence_shape"] = {"cross_document": True}
        self.assertTrue(validate_promoted_benchmark({"benchmark_state": "promoted_live_labels", "cases": [broken] * 16}))

    def test_plan_eval_trace_fields_default_safely(self):
        class SubQuery(BaseModel):
            query: str

        model = build_plan_eval_model(3, SubQuery)
        parsed = model.model_validate({"decision": "stop", "top_k_chunk_ids": [], "tiers": []})
        self.assertEqual(parsed.focus_decisions, [])
        self.assertEqual(validate_plan_eval_output({"decision": "stop"})["focus_decisions"], [])

    def test_structured_llm_prompts_explicitly_request_json(self):
        focuses = [Focus("focus_0", "test")]
        self.assertIn("JSON", _build_eval_only_prompt(focuses, [], 1))
        self.assertIn("JSON", _build_partial_eval_prompt(focuses, [], "batch"))
        self.assertIn("JSON", _build_plan_eval_prompt(focuses, [], 1, 1, 2, 1))


if __name__ == "__main__":
    unittest.main()
