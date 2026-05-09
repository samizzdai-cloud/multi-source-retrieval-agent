import os as _os
import sys as _sys

# 确保项目根目录在 sys.path，供内部绝对 import 使用
_pkg_root = _os.path.dirname(_os.path.abspath(__file__))
if _pkg_root not in _sys.path:
    _sys.path.insert(0, _pkg_root)

# 唯一 ADK Web 入口：adk_agents/rag_retriever/agent.py
# 启动命令：adk web adk_agents --port 8080
