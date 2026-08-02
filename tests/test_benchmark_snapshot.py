import json
import unittest
from pathlib import Path

from eval.capture_feishu_benchmark_snapshots import _body_free


class BenchmarkSnapshotTests(unittest.TestCase):
    def test_snapshot_has_hashes_but_never_document_bodies(self):
        record = {"raw_payload": {"chunks": [{"chunk_id": "c1", "text": "private body", "heading_path": "H"}]}, "retrieval_trace": {"events": [{"event": "read", "chunk_id": "c1", "content_sha256": "a" * 64}]}}
        snap = _body_free(record, {"id": "p3_q01"})
        chunk = snap["raw_payload"]["chunks"][0]
        self.assertNotIn("text", chunk)
        self.assertEqual(chunk["content_chars"], len("private body"))
        self.assertEqual(len(chunk["content_sha256"]), 64)
        self.assertEqual(snap["read_snapshot"], record["retrieval_trace"]["events"])
        self.assertEqual(snap["snapshot_status"], "captured")

    def test_empty_or_timeout_snapshot_is_not_reported_as_captured(self):
        self.assertEqual(_body_free({"raw_payload": {"chunks": []}}, {"id": "p3_q01"})["snapshot_status"], "inconclusive_empty")
        self.assertEqual(_body_free({"raw_payload": {"chunks": []}, "log_raw": ["source timed out after 240.0s"]}, {"id": "p3_q01"})["snapshot_status"], "inconclusive_timeout")

    def test_read_trace_is_a_valid_snapshot_when_final_evaluation_selects_nothing(self):
        record = {"raw_payload": {"chunks": []}, "retrieval_trace": {"events": [{"event": "read", "chunk_id": "c1"}]}}
        self.assertEqual(_body_free(record, {"id": "p3_q01"})["snapshot_status"], "captured")


if __name__ == "__main__":
    unittest.main()
