"""chunk_scorer.py — BM25 + embedding 混合评分模块

当前主链已由 qwen3-rerank 统一做 candidate/chunk 评分；本模块仅在
docx_pipeline 的 skip_scoring=False 遗留模式下使用（Stage 2 candidate trim /
Stage 5 chunk scoring）。

Score 归一化到 [0.3, 0.95]，与 bailian rerank 分数量纲对齐。
"""

import logging
import math
import os
import re
from typing import Dict, List, Optional, Tuple

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


def bm25_score_batch(query: str, texts: List[str]) -> List[float]:
    """批量 BM25 评分。"""
    return [bm25_score(query, t) for t in texts]


# ── 归一化 ───────────────────────────────────────────────────────

def normalize_scores(
    scores: List[float],
    target_min: float = 0.3,
    target_max: float = 0.95,
) -> List[float]:
    """将原始分数线性归一化到 [target_min, target_max]。

    与 bailian rerank 分数量纲对齐（bailian rerank_min_score=0.5, 实际范围 ~0.5-0.95）。
    """
    if not scores:
        return []
    raw_min = min(scores)
    raw_max = max(scores)
    if raw_max <= raw_min:
        return [target_min] * len(scores)
    return [
        target_min + (s - raw_min) / (raw_max - raw_min) * (target_max - target_min)
        for s in scores
    ]


# ── BM25 + Embedding 混合评分 ───────────────────────────────────

def hybrid_score_chunks(
    query: str,
    chunk_texts: List[str],
    use_embedding: bool = True,
    bm25_weight: float = 0.5,
    emb_weight: float = 0.5,
    api_key: str = "",
) -> List[float]:
    """BM25 + embedding 混合评分。

    Args:
        query:         搜索查询
        chunk_texts:   chunk 内容列表
        use_embedding: False 时只用 BM25（开关）
        bm25_weight:   BM25 分数权重（默认 0.5）
        emb_weight:    embedding 分数权重（默认 0.5）
        api_key:       DashScope API key（use_embedding=True 时必须）

    Returns:
        归一化到 [0.3, 0.95] 的分数列表，与 bailian 量纲对齐。
    """
    if not chunk_texts:
        return []

    # BM25 分数
    bm25_raw = bm25_score_batch(query, chunk_texts)
    bm25_norm = normalize_scores(bm25_raw, 0.0, 1.0)

    if not use_embedding or not api_key:
        # 纯 BM25 模式
        return normalize_scores(bm25_raw)

    # Embedding 分数
    try:
        from .embedding import batch_embed, _cosine_similarity
        # embed query + chunks 一起（query 在位置 0）
        all_texts = [query[:500]] + [t[:1500] for t in chunk_texts]
        embeddings = batch_embed(all_texts, api_key)
        if len(embeddings) < len(all_texts):
            logger.warning("chunk_scorer: embedding returned %d/%d, fallback to BM25",
                           len(embeddings), len(all_texts))
            return normalize_scores(bm25_raw)

        query_emb = embeddings[0]
        chunk_embs = embeddings[1:]
        emb_sims = [_cosine_similarity(query_emb, ce) for ce in chunk_embs]
        emb_norm = normalize_scores(emb_sims, 0.0, 1.0)

    except Exception as e:
        logger.warning("chunk_scorer: embedding failed (%s), fallback to BM25", e)
        return normalize_scores(bm25_raw)

    # 混合分数
    hybrid = [
        bm25_weight * b + emb_weight * e
        for b, e in zip(bm25_norm, emb_norm)
    ]
    return normalize_scores(hybrid)


# ── 文档级快速筛选（遗留模式，仅在 skip_scoring=False 时使用）──────────────

def bm25_filter_candidates(
    query: str,
    candidates: List[dict],
    max_candidates: int = 10,
) -> List[str]:
    """用 BM25 对候选文档排序，返回 top-N 的 doc_id 列表。

    当前主链已由 qwen3-rerank 做 candidate selection；此函数仅在 docx_pipeline
    的 skip_scoring=False 遗留模式下使用。

    Args:
        query:           搜索查询
        candidates:      候选文档列表，每条需含 id/title/summary 字段
        max_candidates:  保留最多文档数

    Returns:
        按相关性排序的 doc_id 列表（最多 max_candidates 个）
    """
    scored: List[Tuple[float, str]] = []
    for c in candidates:
        doc_id = c.get("id", c.get("document_id", c.get("doc_id", "")))
        text = (c.get("title", "") + " " + c.get("summary", "")).strip()
        s = bm25_score(query, text)
        scored.append((s, doc_id))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [doc_id for _, doc_id in scored[:max_candidates]]
