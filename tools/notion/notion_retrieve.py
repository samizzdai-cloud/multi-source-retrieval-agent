"""Notion 检索 — stub，待实现"""

import logging
from typing import List

from tools.core.models import UnifiedChunk

logger = logging.getLogger(__name__)


async def notion_retrieve(query: str, **kwargs) -> List[UnifiedChunk]:
    """Notion 检索（未实现）。"""
    logger.debug("notion_retrieve: not configured, returning empty")
    return []
