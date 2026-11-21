"""ADK Web 适配入口 —— 导出 root_agent 供 adk web 加载。

目录结构:
    adk_agents/
      rag_retriever/
        agent.py   ← 本文件（导出 root_agent）

运行方式:
    cd /Users/javendai/Documents/workplace/Multi-source_RAG_Agent
    adk web adk_agents --port 8080

然后在浏览器打开 http://127.0.0.1:8080 即可与 RetrieveAgent 交互。
"""

import logging
import sys
from pathlib import Path

# ── 将项目根目录加入 sys.path，确保能导入 retrieve_agent ──
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

# ── 构建 Web 调试工作流（RetrieveAgent → AnswerAgent）──
from answer_agent import answer_agent  # noqa: E402
from root_agent_config import _positive_float_env, build_root_retrieve_agent, load_project_env  # noqa: E402
from web_retrieval_workflow import WebRetrievalWorkflow  # noqa: E402


load_project_env()
_retrieve_timeout = _positive_float_env("RAG_WEB_RETRIEVE_TIMEOUT_S", 200.0)
_retrieve_agent = build_root_retrieve_agent(
    name="retrieve_v1", web_timeout_s=_retrieve_timeout, defer_feishu_oauth=True,
)

# ADK Web 要求的导出变量
root_agent = WebRetrievalWorkflow(
    name="context_rag_agent_v1",
    description="多源并行检索 + 知识问答 Agent (v1, spec_plan v1)",
    sub_agents=[_retrieve_agent, answer_agent],
    retrieve_timeout_s=_retrieve_timeout,
    answer_timeout_s=_positive_float_env("RAG_WEB_ANSWER_TIMEOUT_S", 120.0),
    manage_feishu_oauth=True,
)
