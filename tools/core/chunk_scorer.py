"""chunk_scorer.py — BM25 评分模块

主链在读取后统一进行 evidence evaluation；
但在 table path 中 (sheet_reader / bitable_reader)，仍使用 bm25_score 做
行级/记录级粗筛和 chunk score（table chunks 不做模型筛选）。
"""

import logging
import re
from typing import List

logger = logging.getLogger(__name__)

# ── BM25 参数 ────────────────────────────────────────────────────

_BM25_K1 = 1.5
_BM25_B = 0.75
_AVG_DOC_LEN = 200  # 假设平均文档/chunk 长度（词数）


def _tokenize(text: str) -> List[str]:
    """中英文混合分词（regex，≥2 字符）。"""
    return re.findall(r'[\w\u4e00-\u9fff]{2,}', text.lower())


def bm25_score(query: str, text: str) -> float:
    """极简 BM25：query 词在 text 中的 tf 近似。

    无需语料库 IDF（单文档打分场景，IDF 对所有 chunk 相同可忽略）。
    """
    query_terms = set(_tokenize(query))
    if not query_terms:
        return 0.0
    text_lower = text.lower()
    text_words = _tokenize(text_lower)
    doc_len = len(text_words)

    score = 0.0
    for term in query_terms:
        tf = text_lower.count(term)
        if tf > 0:
            norm_tf = (tf * (_BM25_K1 + 1)) / (tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * doc_len / _AVG_DOC_LEN))
            score += norm_tf
    return score
