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

# ── 构建完整的 SequentialAgent（RetrieveAgent → AnswerAgent）──
from google.adk.agents import SequentialAgent  # noqa: E402
from answer_agent import answer_agent  # noqa: E402
from root_agent_config import build_root_retrieve_agent  # noqa: E402


_retrieve_agent = build_root_retrieve_agent(name="retrieve_v1")

# ADK Web 要求的导出变量
root_agent = SequentialAgent(
    name="context_rag_agent_v1",
    description="多源并行检索 + 知识问答 Agent (v1, spec_plan v1)",
    sub_agents=[_retrieve_agent, answer_agent],
)
