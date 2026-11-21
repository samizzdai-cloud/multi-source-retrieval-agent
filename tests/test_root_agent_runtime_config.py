import os
import unittest
from unittest.mock import patch

from root_agent_config import _positive_float_env, build_root_retrieve_agent
from tools.core.models import GlobalPlannerConfig, RetrieveConfig, THINKING_FLASH, ThinkingConfig
from tools.core.llm_client import parse_json_response
from tools.core import llm_contracts
from retrieve_agent import build_retrieve_agent
from retrieve_agent_client import _merge_agent_config
from tools.core.models import AgentConfig, RetrieveRequest, SourceConfig


class RootAgentRuntimeConfigTests(unittest.TestCase):
    def test_balanced_and_deep_dive_limit_queries_per_focus(self):
        for preset in ("balanced", "deep_dive"):
            self.assertEqual(RetrieveConfig(preset=preset).effective_config_for("feishu").sub_queries_per_focus, 2)

    def test_web_content_rerank_mode_reaches_effective_source_config(self):
        for mode in ("all", "table_pool_only", "off"):
            with patch.dict(os.environ, {"RAG_PRESET": "balanced", "RAG_ENABLED_SOURCES": "feishu",
                                         "RAG_FEISHU_CONTENT_RERANK": mode}), patch(
                "root_agent_config.build_retrieve_agent",
            ) as build:
                build_root_retrieve_agent(web_timeout_s=120)
                cfg = build.call_args.kwargs["config"].effective_config_for("feishu")
            self.assertEqual(cfg.content_rerank_mode, mode)
            self.assertEqual(cfg.source_timeout_s, 120)
            self.assertEqual(cfg.max_loops, 3)

    def test_single_enabled_source_balanced_sets_both_chunk_budgets(self):
        for source in ("feishu", "google", "bailian"):
            cfg = RetrieveConfig(preset="balanced", enabled_sources=[source])
            sa = cfg.effective_config_for(source)
            self.assertEqual((sa.source_top_k, sa.source_top_k_limit), (12, 12))
            self.assertEqual([cfg.final_top_k(n) for n in (1, 2, 4)], [12, 12, 12])
        cfg = RetrieveConfig(preset="balanced", enabled_sources=["feishu", "google"])
        self.assertEqual(cfg.effective_config_for("feishu").source_top_k, 5)
        self.assertEqual(cfg.final_top_k(2), 6)
        lite = RetrieveConfig(preset="lite", enabled_sources=["feishu"])
        self.assertIsNone(lite.effective_config_for("feishu").source_top_k_limit)
        self.assertEqual(lite.final_top_k(2), 6)

    def test_positive_float_env_accepts_valid_override(self):
        with patch.dict(os.environ, {"TEST_TIMEOUT": "180"}, clear=False):
            self.assertEqual(_positive_float_env("TEST_TIMEOUT", 45.0), 180.0)

    def test_positive_float_env_rejects_invalid_value(self):
        with patch.dict(os.environ, {"TEST_TIMEOUT": "-1"}, clear=False):
            self.assertEqual(_positive_float_env("TEST_TIMEOUT", 45.0), 45.0)

    def test_json_parser_accepts_valid_object_before_transport_tail(self):
        self.assertEqual(parse_json_response('{"focuses": []}\nprovider-tail'), {"focuses": []})

    def test_global_planner_explicitly_disables_deepseek_thinking(self):
        config = GlobalPlannerConfig()
        self.assertEqual(config.model, "deepseek/deepseek-flash")
        self.assertEqual(config.thinking_config.to_litellm_kwargs(), THINKING_FLASH.to_litellm_kwargs())
        self.assertEqual(config.thinking_config.to_litellm_kwargs()["extra_body"]["thinking"]["type"], "disabled")

    def test_thinking_config_uses_deepseek_openai_parameter_shape(self):
        kwargs = ThinkingConfig(enabled=True, effort="high").to_litellm_kwargs()
        self.assertEqual(kwargs["extra_body"]["thinking"]["type"], "enabled")
        self.assertEqual(kwargs["reasoning_effort"], "high")

    def test_retrieve_config_defaults_global_eval_to_deepseek(self):
        self.assertEqual(RetrieveConfig.MODEL, "deepseek/deepseek-flash")

    def test_retrieve_agent_explicitly_disables_eval_thinking_by_default(self):
        previous = llm_contracts._THINKING_CONFIG
        try:
            build_retrieve_agent(RetrieveConfig(enabled_sources=["google"]), name="thinking_contract")
            self.assertEqual(
                llm_contracts._THINKING_CONFIG,
                THINKING_FLASH.to_litellm_kwargs(),
            )
        finally:
            llm_contracts.set_thinking_config(previous)

    def test_auth_override_preserves_model_and_thinking_overrides(self):
        planner_thinking = ThinkingConfig(enabled=False)
        eval_thinking = ThinkingConfig(enabled=False)
        base = AgentConfig(
            planner_model="deepseek/deepseek-flash",
            eval_model="deepseek/deepseek-flash",
            planner_thinking=planner_thinking,
            eval_thinking=eval_thinking,
        )
        merged = _merge_agent_config(
            base,
            RetrieveRequest(user_query="test", auth_override=SourceConfig(feishu_uat="override")),
        )
        self.assertIs(merged.planner_thinking, planner_thinking)
        self.assertIs(merged.eval_thinking, eval_thinking)
