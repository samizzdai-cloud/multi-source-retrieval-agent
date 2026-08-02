import unittest

from tools.core.chunk_preview import build_chunk_preview
from tools.core.models import UnifiedChunk
from tools.google.google_retrieve import _select_drive_candidates
from agents.google.google_source_agent import _preserve_api_order_chunks
from retrieve_agent import build_retrieve_agent
from tools.core.models import RetrieveConfig


class GoogleSmokeTests(unittest.TestCase):
    def test_drive_candidates_keep_api_order_and_budget(self):
        files = [{"id": str(i), "name": f"file-{i}"} for i in range(6)]
        self.assertEqual([f["id"] for f in _select_drive_candidates("q", files, top_n=3)], ["0", "1", "2"])

    def test_preview_full_contract_needs_no_oauth(self):
        full = "row1\nrow2\nrow3\nrow4"
        preview = build_chunk_preview(full, {"table_preview": "header\nrow1\nrow2"}, limit=80)
        self.assertIn("header", preview)
        self.assertEqual(full, "row1\nrow2\nrow3\nrow4")

    def test_chunk_bucket_preserves_reader_order(self):
        chunks = [UnifiedChunk(text=str(i), source_name="doc", source_url="", source_tool="google") for i in range(4)]
        selected = _preserve_api_order_chunks("q", "focus", "concept", chunks, top_n=1)
        self.assertEqual([chunk.text for chunk in selected], ["0", "1", "2", "3"])

    def test_google_agent_builds_without_oauth(self):
        agent = build_retrieve_agent(RetrieveConfig(enabled_sources=["google"]), name="google_smoke")
        self.assertEqual(set(agent.sources), {"google"})


if __name__ == "__main__":
    unittest.main()
