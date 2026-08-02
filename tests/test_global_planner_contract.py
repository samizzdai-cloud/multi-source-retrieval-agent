import unittest

from retrieve_agent import _safe_single_focus_plan
from tools.core.global_planner import (
    _build_prompt,
    _focus_limit_for_sources,
    _parse_global_plan,
)
from tools.core.planner_runner import _truncate_google_keywords, build_default_validators, normalize_plan
from tools.core.models import GlobalPlannerConfig
from tools.core.source_query_config import get_source_query_rules


class GlobalPlannerContractTests(unittest.TestCase):
    def test_focus_is_answer_subtask_and_can_have_multiple_evidence_queries(self):
        query = "结合新品的销售情况，判断6月整体销售GMV会到多少"
        raw = {"focuses": [{
            "focus_id": "focus_0",
            "focus_content": "基于新品销售判断6月整体GMV，并识别缺失预测输入",
            "sub_queries": [
                {"target_source": "feishu", "query": "新品 销售 6月 GMV"},
                {"target_source": "feishu", "query": "新品 销售 库存 6月"},
            ],
        }]}
        plan = _parse_global_plan(raw, query, ["feishu"], 6)
        self.assertEqual(len(plan.focuses), 1)
        self.assertEqual([q.query for q in plan.focuses[0].sub_queries], ["新品 销售 6月 GMV", "新品 销售 库存 6月"])

    def test_diagnostic_topic_drift_is_a_plan_warning_not_a_focus_drop(self):
        query = "想了解 CVR 低的话可以从哪些维度排查原因？"
        raw = {"focuses": [{
            "focus_id": "focus_0", "focus_content": "CVR 定义与计算方式",
            "sub_queries": [{"target_source": "feishu", "query": "CVR 定义"}],
        }]}
        plan = _parse_global_plan(raw, query, ["feishu"], 6)
        self.assertEqual(len(plan.focuses), 1)
        self.assertIn("missing_diagnostic_intent", plan.validation_warnings)

    def test_google_width_control_keeps_acronym_and_period_anchors(self):
        query = _truncate_google_keywords("渠道 流量 诊断 新加坡 CVR 2026 五月", 4)
        self.assertIn("CVR", query)
        self.assertIn("2026", query)

    def test_safe_fallback_preserves_original_query_without_source_planning(self):
        query = "新加坡4.27-5.3渠道流量UTM归类"
        normalized = _safe_single_focus_plan(query, ["feishu"])
        self.assertEqual(normalized.focuses[0].text, query)
        self.assertEqual(normalized.pre_planned_by_source["feishu"][0].query, query)
        self.assertEqual(normalized.replan_needed["feishu"], [])

    def test_feishu_prompt_does_not_expose_bailian_fields_or_business_examples(self):
        prompt = _build_prompt(
            "CVR 低的排查维度",
            ["feishu"],
            GlobalPlannerConfig(),
            None,
        )
        self.assertNotIn('"tags"', prompt)
        self.assertNotIn('"indexes"', prompt)
        self.assertNotIn("nubia", prompt.casefold())
        self.assertNotIn("hypershell", prompt.casefold())
        self.assertNotIn("tags", get_source_query_rules("feishu", include_common=False).casefold())
        self.assertNotIn("indexes", get_source_query_rules("feishu", include_common=False).casefold())
        self.assertIn("paid efficiency", prompt)
        self.assertIn("on-site funnel", prompt)
        self.assertIn("not a rewrite of the final decision, an answer contract", prompt)
        self.assertIn("create the minimum number of non-overlapping Focuses", prompt)
        self.assertIn("not entity lists, generic companion queries", prompt)

    def test_bailian_prompt_retains_its_own_filter_contract(self):
        prompt = _build_prompt(
            "某品牌销量",
            ["bailian"],
            GlobalPlannerConfig(),
            None,
        )
        self.assertIn('"tags"', prompt)
        self.assertIn('"indexes"', prompt)

    def test_same_focus_source_keeps_distinct_queries_and_drops_exact_duplicates(self):
        raw = {"focuses": [{
            "focus_id": "focus_0", "focus_content": "判断 6 月 GMV",
            "sub_queries": [
                {"target_source": "feishu", "query": "6 月 GMV"},
                {"target_source": "feishu", "query": "6 月 销售 明细"},
                {"target_source": "feishu", "query": "6 月 销售 表格"},
                {"target_source": "feishu", "query": "6  月  GMV"},
            ],
        }]}
        plan = _parse_global_plan(raw, "判断6月GMV", ["feishu"], 6)
        self.assertEqual(len(plan.focuses[0].sub_queries), 3)

    def test_broad_cvr_diagnostic_does_not_mutate_planner_query(self):
        raw = {"focuses": [{
            "focus_id": "focus_0", "focus_content": "定位 CVR 低的渠道和漏斗原因",
            "sub_queries": [
                {"target_source": "feishu", "query": "CVR 低 漏斗"},
                {"target_source": "feishu", "query": "CVR 低 渠道 数据"},
            ],
        }]}
        plan = _parse_global_plan(raw, "CVR 低可以从哪些维度排查原因", ["feishu"], 6)
        raw_query = plan.focuses[0].sub_queries[1].query
        self.assertIn("CVR", raw_query)
        self.assertNotIn("ROAS", raw_query)
        self.assertNotIn("CPA", raw_query)

    def test_source_query_preserves_original_english_surface_as_fallback_only(self):
        original = (
            "Should the Meta advertising budget for the Z80 Ultra teasing phase "
            "be shifted from Mexico and France to the United Kingdom?"
        )
        raw = {"focuses": [{
            "focus_id": "focus_0",
            "focus_content": "判断 Z80 Ultra 广告预算转移",
            "sub_queries": [{
                "target_source": "feishu",
                "query": "Z80 Ultra 预热 Meta 广告 预算 转移",
                "query_type": "structured",
            }],
        }]}
        plan = _parse_global_plan(raw, original, ["feishu"], 6)
        queries = plan.focuses[0].sub_queries
        self.assertEqual(len(queries), 1)
        self.assertEqual(queries[0].query, "Z80 Ultra 预热 Meta 广告 预算 转移")
        self.assertEqual(queries[0].fallback_variants, ["Mexico France United Kingdom"])
        self.assertEqual(
            queries[0].fallback_variant_origins,
            {"Mexico France United Kingdom": "original_surface"},
        )
        self.assertNotIn("Should", queries[0].fallback_variants[0])

    def test_original_surface_fallback_metadata_reaches_runtime_without_new_query(self):
        original = "Should Z80 Ultra move budget from Mexico and France to the United Kingdom?"
        raw = {"focuses": [{
            "focus_id": "focus_0", "focus_content": "judge Z80 Ultra budget shift from country performance",
            "sub_queries": [{"target_source": "feishu", "query": "Z80 Ultra Meta budget \u9884\u7b97", "query_type": "structured"}],
        }]}
        global_plan = _parse_global_plan(raw, original, ["feishu"], 6)
        normalized = normalize_plan(
            global_plan, build_default_validators(enabled_sources=["feishu"]), ["feishu"],
        )
        runtime_query = normalized.pre_planned_by_source["feishu"][0]
        self.assertEqual(len(normalized.pre_planned_by_source["feishu"]), 1)
        self.assertIn("Mexico France United Kingdom", runtime_query.variants)
        self.assertEqual(runtime_query.variant_origins["Mexico France United Kingdom"], "original_surface")


    def test_focus_limit_respects_source_budget_formula(self):
        self.assertEqual(_focus_limit_for_sources(["feishu"], 6), 2)
        self.assertEqual(_focus_limit_for_sources(["feishu", "bailian"], 6), 3)
        self.assertEqual(_focus_limit_for_sources(["feishu", "bailian", "google"], 6), 4)
        self.assertEqual(_focus_limit_for_sources(["feishu", "bailian", "google", "notion"], 6), 4)

    def test_prompt_includes_focus_limit_not_raw_max_focuses(self):
        prompt = _build_prompt(
            "CVR 低的排查维度",
            ["feishu"],
            GlobalPlannerConfig(),
            None,
        )
        self.assertIn("focuses 总数 ≤ 2", prompt)
        self.assertNotIn("focuses 总数 ≤ 6", prompt)

    def test_parser_enforces_per_focus_sub_query_cap(self):
        raw = {"focuses": [{
            "focus_id": "focus_0", "focus_content": "判断 6 月 GMV",
            "sub_queries": [
                {"target_source": "feishu", "query": "query A"},
                {"target_source": "feishu", "query": "query B"},
                {"target_source": "feishu", "query": "query C"},
                {"target_source": "feishu", "query": "query D"},
                {"target_source": "feishu", "query": "query E"},
            ],
        }]}
        plan = _parse_global_plan(raw, "判断6月GMV", ["feishu"], 6)
        self.assertEqual(len(plan.focuses), 1)
        self.assertEqual(len(plan.focuses[0].sub_queries), 3)


if __name__ == "__main__":
    unittest.main()
