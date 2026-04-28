# tools/ — Multi-source retrieval toolkit
# Shared: core (models, llm_client, query_planner, etc.)
# Per-source entry points: feishu, bailian, google, notion

from .core.models import UnifiedChunk, QueryPlan, RetrievalResult
from .core.llm_client import call_llm, parse_json_response
from .core.query_planner import plan_query
from .bailian.bailian_retrieve import bailian_retrieve_unified
from .google.google_retrieve import google_retrieve
from .notion.notion_retrieve import notion_retrieve

# feishu_retrieve 延迟 import，避免循环：
# tools/__init__ → feishu_retrieve → feishu_read_tool → tools.core.llm_client → tools/__init__
def _lazy_feishu():
    from .feishu.feishu_retrieve import feishu_retrieve  # noqa: PLC0415
    return feishu_retrieve

def __getattr__(name):
    if name == "feishu_retrieve":
        return _lazy_feishu()
    raise AttributeError(f"module 'tools' has no attribute {name!r}")
