import unittest

from agents.feishu.feishu_source_agent import _original_surface_fallback_query
from agents.source_agent_base import SubQuery


class OriginalSurfaceFallbackTests(unittest.TestCase):
    def setUp(self):
        self.query = SubQuery(
            query="Z80 Ultra Meta budget performance",
            target_focus="focus_0",
            variants=["Mexico France United Kingdom", "profile alias"],
            variant_origins={
                "Mexico France United Kingdom": "original_surface",
                "profile alias": "profile",
            },
        )

    def test_only_returns_original_surface_after_both_primary_routes_miss(self):
        result = _original_surface_fallback_query(
            self.query,
            primary_doc_candidate_count=0,
            primary_table_candidate_count=0,
            current_doc_candidates=[],
            current_table_candidates=[],
        )
        self.assertEqual(result, "Mexico France United Kingdom")

    def test_primary_or_profile_candidate_suppresses_original_surface(self):
        self.assertEqual(
            _original_surface_fallback_query(
                self.query,
                primary_doc_candidate_count=1,
                primary_table_candidate_count=0,
                current_doc_candidates=[],
                current_table_candidates=[],
            ),
            "",
        )
        self.assertEqual(
            _original_surface_fallback_query(
                self.query,
                primary_doc_candidate_count=0,
                primary_table_candidate_count=0,
                current_doc_candidates=[{"id": "profile-hit"}],
                current_table_candidates=[],
            ),
            "",
        )


if __name__ == "__main__":
    unittest.main()
