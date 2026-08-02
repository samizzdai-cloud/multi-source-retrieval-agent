import unittest

from tools.core.rerank import RerankSettings, rerank_documents, rerank_items, select_ranked_ids


class RerankHttpContractTests(unittest.TestCase):
    def test_workspace_response_is_normalized(self):
        seen = {}

        def sender(url, headers, payload, timeout):
            seen.update(url=url, headers=headers, payload=payload, timeout=timeout)
            return {"results": [
                {"index": 1, "relevance_score": 0.91, "document": {"text": "second"}},
                {"index": 0, "relevance_score": 0.20, "document": {"text": "first"}},
            ]}

        settings = RerankSettings(api_key="test", workspace_id="workspace")
        results = rerank_documents("focus", ["first", "second"], settings=settings, transport=sender)
        self.assertEqual([x.index for x in results], [1, 0])
        self.assertEqual(seen["payload"]["model"], "qwen3-rerank")
        self.assertTrue(seen["url"].endswith("/compatible-api/v1/reranks"))
        self.assertIn("Bearer test", seen["headers"]["Authorization"])

    def test_legacy_output_shape_and_batch_local_selection(self):
        def sender(_url, _headers, payload, _timeout):
            if len(payload["documents"]) == 1:
                return {"output": {"results": [{"index": 0, "relevance_score": 0.8}]}}
            return {"output": {"results": [
                {"index": 1, "relevance_score": 0.8},
                {"index": 0, "relevance_score": 0.1},
            ]}}

        settings = RerankSettings(api_key="test", workspace_id="workspace")
        ranked = rerank_items("focus", [("a", "a"), ("b", "b"), ("c", "c")],
                              settings=settings, batch_size=2, transport=sender)
        self.assertEqual({x.batch_index for x in ranked}, {0, 1})
        self.assertEqual(select_ranked_ids(ranked, keep_ratio=0.5), {"b", "c"})
        self.assertEqual(select_ranked_ids(ranked, keep_ratio=1.0, score_threshold=0.5), {"b", "c"})


if __name__ == "__main__":
    unittest.main()
