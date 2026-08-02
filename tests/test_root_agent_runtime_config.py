import os
import unittest
from unittest.mock import patch

from root_agent_config import _positive_float_env
from tools.core.models import GlobalPlannerConfig, RetrieveConfig, THINKING_FLASH, ThinkingConfig
from tools.core.llm_client import parse_json_response
from tools.core import llm_contracts
from retrieve_agent import build_retrieve_agent
from retrieve_agent_client import _merge_agent_config
from tools.core.models import AgentConfig, RetrieveRequest, SourceConfig


class RootAgentRuntimeConfigTests(unittest.TestCase):
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
        self.assertEqual(config.model, "deepseek/deepseek-v4-flash")
        self.assertEqual(config.thinking_config.to_litellm_kwargs(), THINKING_FLASH.to_litellm_kwargs())
        self.assertEqual(config.thinking_config.to_litellm_kwargs()["extra_body"]["thinking"]["type"], "disabled")

    def test_thinking_config_uses_deepseek_openai_parameter_shape(self):
        kwargs = ThinkingConfig(enabled=True, effort="high").to_litellm_kwargs()
        self.assertEqual(kwargs["extra_body"]["thinking"]["type"], "enabled")
        self.assertEqual(kwargs["reasoning_effort"], "high")

    def test_retrieve_config_defaults_global_eval_to_deepseek(self):
        self.assertEqual(RetrieveConfig.MODEL, "deepseek/deepseek-v4-flash")

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
            planner_model="deepseek/deepseek-v4-flash",
            eval_model="deepseek/deepseek-v4-flash",
            planner_thinking=planner_thinking,
            eval_thinking=eval_thinking,
        )
        merged = _merge_agent_config(
            base,
            RetrieveRequest(user_query="test", auth_override=SourceConfig(feishu_uat="override")),
        )
        self.assertIs(merged.planner_thinking, planner_thinking)
        self.assertIs(merged.eval_thinking, eval_thinking)
