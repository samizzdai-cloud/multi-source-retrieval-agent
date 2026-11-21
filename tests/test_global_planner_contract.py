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
    def test_research_focus_can_have_multiple_executable_queries(self):
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
        self.assertNotIn("paid efficiency", prompt)
        self.assertNotIn("on-site funnel", prompt)

    def test_bailian_prompt_retains_its_own_filter_contract(self):
        prompt = _build_prompt(
            "某品牌销量",
            ["bailian"],
            GlobalPlannerConfig(),
            None,
        )
        self.assertIn('"tags"', prompt)
        self.assertIn('"indexes"', prompt)
        self.assertNotIn("feishu 禁用英文缩写", prompt)

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
        self.assertEqual(len(plan.focuses[0].sub_queries), 2)

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
        self.assertEqual(_focus_limit_for_sources(["feishu"], 6), 4)
        self.assertEqual(_focus_limit_for_sources(["feishu", "bailian"], 6), 6)
        self.assertEqual(_focus_limit_for_sources(["feishu", "bailian", "google"], 6), 6)
        self.assertEqual(_focus_limit_for_sources(["feishu", "bailian", "google", "notion"], 6), 6)
        self.assertEqual(_focus_limit_for_sources(["feishu", "feishu"], 6), 4)
        self.assertEqual(_focus_limit_for_sources(["feishu"], 6, "deep_dive"), 4)
        self.assertEqual(_focus_limit_for_sources(["feishu"], 6, "lite"), 2)
        self.assertEqual(_focus_limit_for_sources(["feishu"], 2, "balanced"), 2)

    def test_prompt_includes_focus_limit_not_raw_max_focuses(self):
        prompt = _build_prompt(
            "CVR 低的排查维度",
            ["feishu"],
            GlobalPlannerConfig(),
            None,
        )
        self.assertIn("focuses 总数 ≤ 4", prompt)
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
        self.assertEqual(len(plan.focuses[0].sub_queries), 2)

    def test_query_cap_is_independent_for_each_source(self):
        raw = {"focuses": [{"focus_id": "F1", "focus_content": "current state", "sub_queries": [
            {"target_source": src, "query": f"query {i}"}
            for src in ("feishu", "bailian") for i in range(3)
        ]}]}
        plan = _parse_global_plan(raw, "current state", ["feishu", "bailian"], 4)
        self.assertEqual([q.target_source for q in plan.focuses[0].sub_queries], ["feishu", "feishu", "bailian", "bailian"])

    def test_shared_focus_stays_stable_with_source_specific_requests(self):
        text = "核实品牌当前产品销售状态，支撑增长诊断"
        raw = {"focuses": [{"focus_id": "focus_0", "focus_content": text, "sub_queries": [
            {"target_source": "feishu", "query": "品牌 销售实绩", "query_type": "structured"},
            {"target_source": "bailian", "query": "品牌当前产品的实际销售状态及其业务背景"},
        ]}]}
        plan = _parse_global_plan(raw, text, ["feishu", "bailian"], 6)
        normalized = normalize_plan(plan, build_default_validators(enabled_sources=["feishu", "bailian"]),
                                    ["feishu", "bailian"], max_focuses_per_source=4)
        self.assertEqual([(f.focus_id, f.text, f.targets) for f in normalized.focuses],
                         [("focus_0", text, ["bailian", "feishu"])])
        for source in ("feishu", "bailian"):
            query = normalized.pre_planned_by_source[source][0]
            self.assertEqual(query.target_focus, "focus_0")
            self.assertTrue(query.sub_query_id.startswith(f"{source}:focus_0:"))
        self.assertNotEqual(normalized.pre_planned_by_source["feishu"][0].query,
                            normalized.pre_planned_by_source["bailian"][0].query)

    def test_source_focus_budget_does_not_reduce_other_source_capacity(self):
        raw = {"focuses": [{"focus_id": f"focus_{i}", "focus_content": f"独立研究任务{i}",
                            "sub_queries": [{"target_source": src, "query": f"对象 材料{i}"}
                                            for src in (["feishu", "bailian"] if i < 2 else
                                                        ["feishu"] if i < 4 else ["bailian"])]}
                           for i in range(6)]}
        plan = _parse_global_plan(raw, "多业务研究", ["feishu", "bailian"], 6)
        normalized = normalize_plan(plan, build_default_validators(enabled_sources=["feishu", "bailian"]),
                                    ["feishu", "bailian"], max_focuses_per_source=4)
        self.assertEqual(len(normalized.focuses), 6)
        self.assertEqual({src: len({q.target_focus for q in queries})
                          for src, queries in normalized.pre_planned_by_source.items()},
                         {"feishu": 4, "bailian": 4})

    def test_empty_query_fallback_cannot_bypass_source_focus_budget(self):
        raw = {"focuses": [{"focus_id": f"focus_{i}", "focus_content": f"独立任务{i}",
                            "sub_queries": [] if i == 4 else
                            [{"target_source": "feishu", "query": f"对象 材料{i}"}]}
                           for i in range(6)]}
        plan = _parse_global_plan(raw, "研究", ["feishu", "bailian"], 6)
        normalized = normalize_plan(plan, build_default_validators(enabled_sources=["feishu", "bailian"]),
                                    ["feishu", "bailian"], max_focuses_per_source=4)
        self.assertEqual(len(normalized.pre_planned_by_source["feishu"]), 4)
        self.assertEqual([f.focus_id for f in normalized.replan_needed["bailian"]], ["focus_4"])
        self.assertEqual(normalized.focuses[-1].targets, ["bailian"])
        self.assertIn("source_focus_limit:feishu:focus_5", normalized.validation_warnings)


if __name__ == "__main__":
    unittest.main()
