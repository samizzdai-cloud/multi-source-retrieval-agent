from __future__ import annotations

import unittest

from scripts.verify_feishu_release_e2e import build_body_free_summary


class ReleaseE2ESummaryTests(unittest.TestCase):
    def test_summary_is_feishu_only_and_never_serializes_body_or_url(self) -> None:
        summary = build_body_free_summary(
            query="private query text",
            elapsed_s=1.2345,
            raw_payload={"chunks": [{
                "source_tool": "feishu", "text": "private document body",
                "source_url": "https://internal.example/doc", "chunk_id": "private-id",
            }]},
            trace={"events": [{"event": "planner_focus"}, {"event": "source_completed", "route": "block_api"}]},
            log_errors={},
        )

        self.assertTrue(summary["passed"])
        rendered = str(summary)
        self.assertNotIn("private query text", rendered)
        self.assertNotIn("private document body", rendered)
        self.assertNotIn("internal.example", rendered)
        self.assertNotIn("private-id", rendered)
        self.assertEqual(summary["route_counts"], {"block_api": 1})

    def test_mcp_protocol_error_is_a_failed_release_check(self) -> None:
        summary = build_body_free_summary(
            query="q",
            elapsed_s=1.0,
            raw_payload={"chunks": [{"source_tool": "feishu"}]},
            trace={"events": [{"event": "planner_focus"}, {"event": "source_completed"}]},
            log_errors={"mcp_protocol_error": 1},
        )
        self.assertFalse(summary["passed"])
        self.assertFalse(summary["checks"]["no_mcp_protocol_error"])


if __name__ == "__main__":
    unittest.main()
