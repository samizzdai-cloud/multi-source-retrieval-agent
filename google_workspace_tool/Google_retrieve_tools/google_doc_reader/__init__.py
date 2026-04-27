"""
Google Docs 结构化读取与 Markdown 转换模块。

将 drive_content_reader.py 中的 Docs 读取能力解耦为独立模块，支持：
- 批量请求（可控并发）
- 多 Tabs 结构感知
- 删除线/建议删除内容过滤
- 丰富的 Markdown 结构输出
"""

from .fetcher import BatchDocFetcher
from .parser import DocParser
from .markdown_renderer import MarkdownRenderer
from .models import DocContent, TabContent, Block, InlineElement, ListInfo

__all__ = [
    "BatchDocFetcher",
    "DocParser",
    "MarkdownRenderer",
    "DocContent",
    "TabContent",
    "Block",
    "InlineElement",
    "ListInfo",
]
