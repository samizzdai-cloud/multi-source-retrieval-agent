import unittest

from agents.source_agent_base import Focus
from tools.core.global_plan_eval import evaluate_merged_plan
from tools.core.models import ChunkEntry


class GlobalPlanEvalTests(unittest.TestCase):
    def test_covered_source_verdict_requires_judged_retained_evidence(self):
        result = evaluate_merged_plan(
            [ChunkEntry("c1", "feishu", "x", tier="important", metadata={"answered_focuses": ["F1"]})],
            [Focus("F1", "objective")], {"critical_min": 2, "important_min": 0},
            {"feishu": [{"event": "plan_eval", "focus_decisions": [
                {"focus_id": "F1", "status": "covered", "gap_type": "none"},
            ]}]},
        )
        self.assertEqual(result.decision, "stop")
        self.assertEqual(result.focus_decisions[0]["status"], "covered")

    def test_upstream_gap_is_terminal_even_when_diagnostic_evidence_exists(self):
        result = evaluate_merged_plan(
            [ChunkEntry("c1", "feishu", "x", tier="important", metadata={"answered_focuses": ["F1"]})],
            [Focus("F1", "objective")], {"critical_min": 2, "important_min": 0},
            {"feishu": [{"event": "plan_eval", "focus_decisions": [
                {"focus_id": "F1", "status": "gap", "gap_type": "upstream_evidence_needed"},
            ]}]},
        )
        self.assertEqual(result.decision, "stop")
        self.assertEqual(result.focus_decisions[0]["gap_type"], "upstream_evidence_needed")

    def test_query_refinement_is_an_eligible_but_not_executed_global_replan(self):
        result = evaluate_merged_plan(
            [], [Focus("F1", "objective")], {"critical_min": 1, "important_min": 0},
            {"feishu": [{"event": "plan_eval", "focus_decisions": [
                {"focus_id": "F1", "status": "gap", "gap_type": "query_refinement"},
            ]}]},
        )
        self.assertEqual(result.decision, "continue")
        self.assertEqual(result.replan_focus_ids, ("F1",))
        self.assertEqual(result.trace_event()["event"], "global_plan_eval")

    def test_lost_covered_evidence_is_retention_gap_not_a_duplicate_replan(self):
        result = evaluate_merged_plan(
            [], [Focus("F1", "objective")], {"critical_min": 1, "important_min": 0},
            {"feishu": [{"event": "plan_eval", "focus_decisions": [
                {"focus_id": "F1", "status": "covered", "gap_type": "none"},
            ]}]},
        )
        self.assertEqual(result.decision, "stop")
        self.assertEqual(result.focus_decisions[0]["gap_type"], "evidence_gap")

    def test_target_focus_without_judgment_does_not_cover(self):
        result = evaluate_merged_plan(
            [ChunkEntry("c1", "feishu", "x", tier="critical", target_focuses={"F1"})],
            [Focus("F1", "objective")], {"critical_min": 1, "important_min": 0}, {},
        )
        self.assertEqual(result.focus_decisions[0]["status"], "gap")
        self.assertEqual(result.replan_focus_ids, ("F1",))


if __name__ == "__main__":
    unittest.main()
