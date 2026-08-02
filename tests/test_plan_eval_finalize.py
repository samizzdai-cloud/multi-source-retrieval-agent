import unittest

from agents.source_agent_base import _tiered_top_ids
from agents.source_agent_base import Focus
from tools.core.final_assembler import render_final_payload
from tools.core.models import ChunkEntry


class PlanEvalFinalizeTests(unittest.TestCase):
    def test_uses_existing_critical_and_important_judgments_only(self):
        pool = {
            "api-1": ChunkEntry(id="api-1", source="feishu", content="a"),
            "api-2": ChunkEntry(id="api-2", source="feishu", content="b"),
            "api-3": ChunkEntry(id="api-3", source="feishu", content="c"),
            "api-4": ChunkEntry(id="api-4", source="feishu", content="d"),
        }
        tiers = {
            "api-1": {"tier": "important", "relevance": 0.9},
            "api-2": {"tier": "critical", "relevance": 0.1},
            "api-3": {"tier": "noise", "relevance": 1.0},
        }

        self.assertEqual(_tiered_top_ids(tiers, pool, 3), ["api-2", "api-1"])

    def test_preserves_native_order_for_equal_judgments(self):
        pool = {
            "api-2": ChunkEntry(id="api-2", source="feishu", content="b"),
            "api-1": ChunkEntry(id="api-1", source="feishu", content="a"),
        }
        tiers = {
            "api-2": {"tier": "critical", "relevance": 0.5},
            "api-1": {"tier": "critical", "relevance": 0.5},
        }

        self.assertEqual(_tiered_top_ids(tiers, pool, 2), ["api-2", "api-1"])

    def test_final_payload_contains_full_content_and_only_retained_evidence(self):
        chunks = [
            ChunkEntry(id="c1", source="feishu", content="FULL", preview_content="PREVIEW",
                       tier="important", target_focuses={"F1"}),
            ChunkEntry(id="c2", source="feishu", content="NOISE FULL", preview_content="NOISE",
                       tier="noise", target_focuses={"F1"}),
        ]
        payload = render_final_payload("q", chunks, [Focus("F1", "objective")])
        self.assertEqual([item["chunk_id"] for item in payload["chunks"]], ["c1"])
        self.assertEqual(payload["chunks"][0]["text"], "FULL")
        self.assertNotIn("preview_content", payload["chunks"][0])
