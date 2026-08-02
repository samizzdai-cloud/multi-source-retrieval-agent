import unittest

from tools.core.eval_layer import _normalize_eval_payload


class EvalLayerPayloadTests(unittest.TestCase):
    def test_recovers_one_element_object_wrapper(self):
        result = _normalize_eval_payload([{
            "selected_ids": ["C1"],
            "tiers": [{"chunk_id": "C1", "tier": "critical", "relevance": 0.9}],
        }], top_k=3)
        self.assertEqual(result["selected_ids"], ["C1"])
        self.assertEqual(result["tiers"][0]["chunk_id"], "C1")

    def test_recovers_tier_rows_when_array_has_non_tier_object(self):
        result = _normalize_eval_payload([
            {"note": "ignored"},
            {"chunk_id": "C2", "tier": "important", "relevance": 0.8},
        ], top_k=3)
        self.assertEqual(result["selected_ids"], ["C2"])

    def test_refuses_array_without_schema_shaped_tier_data(self):
        with self.assertRaisesRegex(TypeError, "item_types=str"):
            _normalize_eval_payload(["not a tier"], top_k=3)


if __name__ == "__main__":
    unittest.main()
