import unittest

from eval.label_feishu_benchmark import _build_label_prompt, _sanitize_label


class LiveLabelSanitizerTests(unittest.TestCase):
    def test_drops_hallucinated_ids_and_invalid_cross_document_claim(self):
        chunks = [{"chunk_id": "feishu:doc-a:one", "source_url": "", "text": "allowed"}]
        raw = {
            "outcome": "sufficient_evidence",
            "replan_decision": "none",
            "expected_chain": [
                {"chunk_id": "feishu:doc-a:one", "role": "allowed", "snippet": "anchor"},
                {"chunk_id": "made-up", "role": "bad", "snippet": "bad"},
            ],
            "evidence_shape": {"cross_chunk": True, "cross_document": True, "business_entity": "x", "period": "y"},
        }
        label = _sanitize_label(raw, chunks)
        self.assertEqual([item["chunk_id"] for item in label["expected_chain"]], ["feishu:doc-a:one"])
        self.assertFalse(label["evidence_shape"]["cross_chunk"])
        self.assertFalse(label["evidence_shape"]["cross_document"])
        self.assertEqual(label["evidence_shape"]["business_entity"], "")

    def test_prompt_uses_runner_chunk_id_field(self):
        prompt = _build_label_prompt({"id": "p3_qx"}, [{"chunk_id": "feishu:doc-a:one", "text": "body"}])
        self.assertIn('"chunk_id": "feishu:doc-a:one"', prompt)

    def test_positive_outcome_without_an_anchor_is_downgraded(self):
        label = _sanitize_label(
            {"outcome": "sufficient_evidence", "replan_decision": "none", "expected_chain": []},
            [{"chunk_id": "feishu:doc-a:one", "text": "allowed"}],
        )
        self.assertEqual(label["outcome"], "partial_evidence")
