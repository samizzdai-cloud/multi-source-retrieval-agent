# tools/ — Multi-source retrieval toolkit
# Shared: models, llm_client, reranker, evaluator, query_planner
# Per-source entry points: feishu_retrieve.py (→ feishu_read_tool/), bailian_retrieve.py, stubs

from .models import UnifiedChunk, QueryPlan, RetrievalResult
from .llm_client import call_llm, parse_json_response
from .reranker import rerank_and_filter
from .evaluator import EvaluationLayer, EvalConfig
from .query_planner import plan_query
from .bailian_retrieve import bailian_retrieve_unified
from .google_retrieve import google_retrieve
from .notion_retrieve import notion_retrieve

# feishu_retrieve 和 search_feishu_file 延迟 import，避免循环：
# tools/__init__ → feishu_retrieve → feishu_read_tool → tools.llm_client → tools/__init__
def _lazy_feishu():
    from .feishu_retrieve import feishu_retrieve  # noqa: PLC0415
    from feishu_read_tool.search import search_feishu_file  # noqa: PLC0415
    return feishu_retrieve, search_feishu_file

def __getattr__(name):
    if name == "feishu_retrieve":
        return _lazy_feishu()[0]
    if name == "search_feishu_file":
        return _lazy_feishu()[1]
    raise AttributeError(f"module 'tools' has no attribute {name!r}")
