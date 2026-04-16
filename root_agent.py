"""Context RAG Agent — ADK 入口

架构: SequentialAgent
  Step 1: RetrievalAgent  — 多源并行检索, 结果写入 session.state
  Step 2: AnswerAgent     — 读取 chunks, 生成自然语言回答
"""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

from google.adk.agents import SequentialAgent

from retrieval_agent import RetrievalAgent
from answer_agent import answer_agent
from tools.evaluator import EvalConfig
from tools.models import SourceConfig

# ── P3 Final 生产配置 (2026-04-15) ──────────────────────────────
# Benchmark: 25q, score_mean=0.770, lo_pct=3.2%, feishu_q=25/25, lat≈19s
# 核心：min_sources=2 确保飞书有时间返回高质量文档级 chunk
_retrieval_agent = RetrievalAgent(
    name="retrieval",
    description="多源并行检索 — 返回结构化 chunks JSON 并写入 session state",
    eval_config=EvalConfig(
        # ── EvaluationLayer: 早停条件 ──────────────────────────────
        min_good_chunks=999,             # 禁用 chunk 数早停，纯靠 focus 满足度控制
        min_relevance_score=0.45,        # focus 命中门槛（实测 0.45 在质量/召回间最优）
        hard_timeout_seconds=25.0,       # 硬超时，25s 可覆盖 95% query（P90=23.4s）
        min_sources=1,                   # v4.0: 单源满足即可早停（延迟 -75%，score 持平）
        min_per_focus=2,                 # 每个 focus ≥2 个 good chunk 才算满足
        early_stop_min_elapsed_s=0.0,    # 用 min_sources 保护，不再用时间门槛
        # ── Reranker: 输出控制 ────────────────────────────────────
        min_per_focus_select=3,          # 每个 focus 选 3 个代表 chunk（覆盖多视角）
        top_k_chunks=25,                 # 最终输出上限（给 AnswerAgent 充足上下文）
        use_embedding_for_select=True,   # 启用 embedding 语义匹配（v4.0: select_by_focus 依赖 sim_matrix）
        embedding_sim_threshold=0.4,
        embedding_dim=512,
        # ── 混合评分权重 ──────────────────────────────────────────
        emb_weight=0.4,
        kw_weight=0.15,
    ),
    source_config=SourceConfig(
        load_from_env=True,
    ),
)

root_agent = SequentialAgent(
    name="context_rag_agent",
    description="多源并行检索 + 知识问答 Agent",
    sub_agents=[_retrieval_agent, answer_agent],
)
