import unittest

from agents.source_agent_base import Focus
from tools.core.global_coverage import snapshot_global_coverage
from tools.core.models import ChunkEntry


class GlobalCoverageTests(unittest.TestCase):
    def test_snapshot_is_body_free_and_uses_evidence_tiers_only(self):
        chunks = [
            ChunkEntry("c1", "feishu", "secret body", metadata={"doc_id": "d1", "answered_focuses": ["F1"]},
                       target_focuses={"F1"}, tier="critical"),
            ChunkEntry("c2", "google", "another secret", metadata={"doc_id": "d2", "answered_focuses": ["F1"]},
                       target_focuses={"F1"}, tier="important"),
            ChunkEntry("c3", "feishu", "noise body", target_focuses={"F1"}, tier="noise"),
        ]
        rows = snapshot_global_coverage(
            chunks, [Focus("F1", "focus")], {"critical_min": 1, "important_min": 1},
            stage="final_post_global_eval",
        )
        self.assertEqual(rows[0]["coverage_status"], "covered")
        self.assertEqual(rows[0]["unique_document_count"], 2)
        self.assertEqual(rows[0]["contributing_sources"], ["feishu", "google"])
        self.assertNotIn("body", str(rows[0]))

    def test_snapshot_reports_final_cap_regression_as_gap(self):
        before = [ChunkEntry("c1", "feishu", "x", metadata={"answered_focuses": ["F1"]}, target_focuses={"F1"}, tier="critical")]
        after = []
        focus = [Focus("F1", "focus")]
        quota = {"critical_min": 1, "important_min": 0}
        self.assertEqual(snapshot_global_coverage(before, focus, quota, stage="merged")[0]["coverage_status"], "covered")
        self.assertEqual(snapshot_global_coverage(after, focus, quota, stage="final")[0]["coverage_status"], "gap")

    def test_dispatch_target_alone_cannot_count_as_global_evidence(self):
        chunk = ChunkEntry("c1", "feishu", "x", target_focuses={"F1"}, tier="critical")
        row = snapshot_global_coverage(
            [chunk], [Focus("F1", "focus")], {"critical_min": 1, "important_min": 0}, stage="merged",
        )[0]
        self.assertEqual(row["coverage_status"], "gap")


if __name__ == "__main__":
    unittest.main()
