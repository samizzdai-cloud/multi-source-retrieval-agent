import unittest

from agents.source_agent_base import Focus
from tools.core.final_assembler import enforce_focus_quota
from tools.core.models import ChunkEntry


def _chunk(chunk_id, focus_id, tier="critical", score=0.8):
    return ChunkEntry(
        id=chunk_id, source="feishu", content=chunk_id, preview_content=chunk_id,
        tier=tier, score=score, target_focuses={focus_id},
        metadata={"answered_focuses": [focus_id]},
    )


class FinalFocusQuotaTests(unittest.TestCase):
    def setUp(self):
        self.focuses = [Focus("F1", "one"), Focus("F2", "two")]
        self.quota = {"critical_min": 1, "important_min": 0}

    def test_backfills_missing_focus_from_already_judged_candidates(self):
        first = _chunk("f1", "F1", score=0.9)
        second = _chunk("f2", "F2", score=0.8)
        picked, events = enforce_focus_quota([first], [first, second], self.focuses, 2, self.quota)
        self.assertEqual([chunk.id for chunk in picked], ["f1", "f2"])
        self.assertEqual(events[0]["action"], "backfill")

    def test_replaces_surplus_when_fixed_cap_drops_another_focus(self):
        f1_high = _chunk("f1-high", "F1", score=0.9)
        f1_surplus = _chunk("f1-surplus", "F1", score=0.7)
        f2 = _chunk("f2", "F2", score=0.8)
        picked, events = enforce_focus_quota(
            [f1_high, f1_surplus], [f1_high, f1_surplus, f2], self.focuses, 2, self.quota,
        )
        self.assertEqual({chunk.id for chunk in picked}, {"f1-high", "f2"})
        self.assertEqual(events[0]["action"], "replace_surplus")

    def test_never_promotes_noise_to_meet_a_quota(self):
        f1 = _chunk("f1", "F1")
        noise = _chunk("f2-noise", "F2", tier="noise")
        picked, events = enforce_focus_quota([f1], [f1, noise], self.focuses, 2, self.quota)
        self.assertEqual([chunk.id for chunk in picked], ["f1"])
        self.assertEqual(events[0]["action"], "unmet_no_candidate")


if __name__ == "__main__":
    unittest.main()
