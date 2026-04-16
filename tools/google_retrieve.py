"""Google Workspace 检索 — stub，待实现"""

import logging
from typing import List

from .models import UnifiedChunk

logger = logging.getLogger(__name__)


async def google_retrieve(query: str, **kwargs) -> List[UnifiedChunk]:
    """Google Workspace 检索（未实现）。"""
    logger.debug("google_retrieve: not configured, returning empty")
    return []
