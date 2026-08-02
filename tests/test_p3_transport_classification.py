import unittest

from eval.feishu_scope_first_runner import _inconclusive_reasons, _retry_is_quiescent


class P3TransportClassificationTests(unittest.TestCase):
    def test_rerank_wall_timeout_is_inconclusive_even_when_evidence_fails_open(self):
        record = {
            "log_raw": [],
            "retrieval_trace": {"events": [
                {"event": "rerank_wall_timeout", "focus_id": "F1"},
                {"event": "rerank_drain", "remaining_sync_io": 0},
            ]},
        }
        self.assertEqual(_inconclusive_reasons(record), ["rerank_wall_timeout"])
        self.assertTrue(_retry_is_quiescent(record))

    def test_empty_model_response_is_inconclusive_and_unsettled_drain_blocks_retry(self):
        record = {
            "log_raw": ["LLM call failed after 3 attempts: empty response from model"],
            "retrieval_trace": {"events": [
                {"event": "rerank_drain", "remaining_sync_io": 1},
            ]},
        }
        self.assertIn("model_empty_response", _inconclusive_reasons(record))
        self.assertFalse(_retry_is_quiescent(record))

    def test_legacy_rerank_timeout_without_drain_is_not_retry_safe(self):
        record = {"retrieval_trace": {"events": [{"event": "rerank_wall_timeout"}]}}
        self.assertFalse(_retry_is_quiescent(record))

    def test_optional_embedded_sheet_timeout_does_not_poison_parent_doc_attempt(self):
        record = {
            "log_raw": [
                "feishu.read_fail source=type30_sheet kind=ReadTimeout msg=timeout",
            ],
            "retrieval_trace": {"events": [
                {"event": "embedded_sheet_unavailable", "document_id": "doc-1", "count": 1},
            ]},
        }
        self.assertEqual(_inconclusive_reasons(record), [])

    def test_doc_timeout_remains_inconclusive_even_with_embedded_failure(self):
        record = {
            "log_raw": [
                "DocPool: per-doc timeout", "source=type30_sheet kind=ReadTimeout",
            ],
            "retrieval_trace": {"events": [
                {"event": "embedded_sheet_unavailable", "document_id": "doc-1", "count": 1},
            ]},
        }
        self.assertIn("read_timeout", _inconclusive_reasons(record))


if __name__ == "__main__":
    unittest.main()
