"""Context RAG Agent v1 入口（spec_plan.md v1）。

架构: SequentialAgent
  Step 1: RetrieveAgent (v1) — 多源并行 + plan_only/eval_only loop, 结果写 state
  Step 2: AnswerAgent         — 读取 chunks, 生成中文答复（沿用旧 answer_agent）

Phase 3 配置：feishu + bailian 双源并发，各源默认 single-loop。
旧 ``root_agent.py`` 仍然保留，作对比基线。
"""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

from google.adk.agents import SequentialAgent

from answer_agent import answer_agent
from retrieve_agent import build_retrieve_agent
from tools.models import RetrieveConfig, SourceAgentConfig, SourceConfig


_SOURCE_CFG = SourceAgentConfig(
    source_top_k=8,
    min_loops=1,
    max_loops=1,
    source_timeout_s=45.0,   # feishu 读文档较慢，放宽超时
    sub_query_top_n=8,
)

_retrieve_agent = build_retrieve_agent(
    config=RetrieveConfig(
        enabled_sources=["feishu", "bailian"],
        source_configs={
            "feishu":  _SOURCE_CFG,
            "bailian": _SOURCE_CFG,
        },
        eval_layer_enabled=False,
        # RetrieveAgent 整体超时 = source_timeout_s，由每源 cfg 提供
        # model 来自 RetrieveConfig 默认值："gemini-3.1-flash-lite-preview"
    ),
    source_config=SourceConfig(load_from_env=True),
    name="retrieve_v1",
)

root_agent = SequentialAgent(
    name="context_rag_agent_v1",
    description="多源并行检索 + 知识问答 Agent (v1, spec_plan v1)",
    sub_agents=[_retrieve_agent, answer_agent],
)
