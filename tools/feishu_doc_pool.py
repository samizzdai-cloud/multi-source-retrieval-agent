"""FeishuDocPool — 跨 sub_query 共享的飞书文档读取池

核心职责：
  1. 文档去重：多个 sub_query 搜到相同 doc_id → 只读一次
  2. API 调度：Block API 4/s rate limiter + MCP 无限频并发
  3. 路由：query_type + 未缓存文档数 → Block API / MCP
  4. 渐进缓存：一篇文档 chunk 完立即可用，不等全部完成
  5. BM25 评分：按各 sub_query 独立评分 → 每个 task 输出不同 top-K

生命周期：RetrievalAgent._run_async_impl 创建一个实例，传给所有 feishu task。

设定参数 (query_type → top-N / per-doc-K / max-output):
  data:    top-3 docs, per-doc top-3 chunks, max 10 output
  concept: top-6 docs, per-doc top-5 chunks, max 15 output
  mixed:   top-4 docs, per-doc top-4 chunks, max 12 output
"""

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from feishu_read_tool.pipeline_models import Chunk, PipelineConfig

if TYPE_CHECKING:
    from tools.models import UnifiedChunk

logger = logging.getLogger(__name__)

# ── 路由参数 ─────────────────────────────────────────────────────

@dataclass
class PoolConfig:
    """query_type 分化参数。"""
    top_n_docs: int = 4         # 文档级 BM25 筛选保留数
    max_per_doc: int = 4        # 每篇文档输出 top-K chunks
    max_output: int = 12        # feishu_retrieve 单次输出上限
    mcp_threshold: int = 4      # 未缓存文档数 > 此值时触发 MCP 路径
    block_api_concurrency: int = 3  # Block API 安全并发数（实测 4 触发 99991400 限频）
    eval_mode: str = "bm25"     # "bm25" | "bm25+embedding"

    @classmethod
    def for_query_type(cls, query_type: str) -> "PoolConfig":
        # MCP 配比：data 全走 Block API（P3-B 验证 MCP 反而更慢），concept/mixed 按阈值走
        if query_type == "data":
            return cls(top_n_docs=5, max_per_doc=4, max_output=20, mcp_threshold=99)
        elif query_type == "concept":
            return cls(top_n_docs=8, max_per_doc=5, max_output=25, mcp_threshold=3)
        else:  # mixed
            return cls(top_n_docs=6, max_per_doc=5, max_output=22, mcp_threshold=4)


# ── BM25 轻量评分 ───────────────────────────────────────────────

def _bm25_score(query: str, text: str, k1: float = 1.5, b: float = 0.75,
                idf: Optional[Dict[str, float]] = None) -> float:
    """BM25 with optional IDF weighting.

    When idf dict is provided, rare terms get higher weight.
    Without IDF, all terms contribute equally (original behavior).
    """
    query_terms = set(re.findall(r'[\w\u4e00-\u9fff]{2,}', query.lower()))
    if not query_terms:
        return 0.0
    text_lower = text.lower()
    text_words = re.findall(r'[\w\u4e00-\u9fff]+', text_lower)
    doc_len = len(text_words)
    avg_dl = 200
    score = 0.0
    for term in query_terms:
        tf = text_lower.count(term)
        if tf > 0:
            norm_tf = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / avg_dl))
            term_idf = idf.get(term, 1.0) if idf else 1.0
            score += norm_tf * term_idf
    return score


def _compute_idf(chunks_by_doc: Dict[str, list]) -> Dict[str, float]:
    """Compute IDF from all chunks in the DocPool.

    IDF(term) = log(N / (1 + df(term)))
    where N = total number of chunks, df = number of chunks containing term.
    """
    import math
    all_chunks = [c for doc_chunks in chunks_by_doc.values() for c in doc_chunks]
    N = len(all_chunks) or 1
    df: Dict[str, int] = {}
    for chunk in all_chunks:
        text = chunk.content.lower() if hasattr(chunk, 'content') else str(chunk).lower()
        terms = set(re.findall(r'[\w\u4e00-\u9fff]{2,}', text))
        for t in terms:
            df[t] = df.get(t, 0) + 1
    # Use log(1 + N/(1+count)) to ensure IDF >= 0
    return {t: math.log(1 + N / (1 + count)) for t, count in df.items()}


def _bm25_rank_candidates(query: str, candidates: List[dict]) -> List[dict]:
    """按 BM25(query, title+summary) 对候选文档排序。"""
    scored = []
    for c in candidates:
        text = (c.get("title", "") + " " + c.get("summary", "")).strip()
        s = _bm25_score(query, text)
        scored.append((s, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored]


def _text_hash(text: str) -> str:
    """chunk 去重 hash。"""
    normalized = re.sub(r"\s+", " ", text[:500].strip().lower())
    return hashlib.md5(normalized.encode()).hexdigest()


# ── FeishuDocPool ────────────────────────────────────────────────

class FeishuDocPool:
    """跨 sub_query 共享的飞书文档读取池。"""

    def __init__(self, uat: str, query_type: str = "mixed",
                 pool_config_override: Optional["PoolConfig"] = None):
        self.uat = uat
        self.query_type = query_type
        self.pool_config = pool_config_override or PoolConfig.for_query_type(query_type)

        # 共享缓存: doc_id → List[Chunk]
        self._doc_chunks: Dict[str, List[Chunk]] = {}
        # doc_id → doc metadata (title, url, doc_type)
        self._doc_meta: Dict[str, dict] = {}
        # doc_id → 读取路径 ("block_api" | "mcp")
        self._doc_route: Dict[str, str] = {}
        # 正在读取中的 doc_ids (防止重复调度)
        self._reading: Set[str] = set()
        # 已完成的 doc_ids
        self._done: Set[str] = set()
        # 全局锁
        self._lock = asyncio.Lock()
        # Block API 全局并发控制（跨所有 sub_query 共享，防止 99991400 限频）
        self._block_api_sem = asyncio.Semaphore(self.pool_config.block_api_concurrency)
        # doc 完成通知 (渐进式：一篇完成立即通知等待的 task)
        self._doc_ready_events: Dict[str, asyncio.Event] = {}
        # doc_id → 第一个请求该文档的 sub_query（用于 Stage 4 大表 LLM 提取）
        self._doc_query: Dict[str, str] = {}
        # sub_query → expanded BM25 query string（缓存，避免重复展开）
        self._expanded_cache: Dict[str, str] = {}
        # Embedding 混合评分（由 retrieval_agent 注入）
        self._embedding_cache: Optional[object] = None   # EmbeddingCache 实例
        self._focuses: List[str] = []                     # flat focus 列表
        self._focus_embeddings: Optional[List] = None     # focus embedding 向量
        # doc_id → 请求过该文档的 focus_group indices
        self._doc_group_indices: Dict[str, Set[int]] = {}

    def set_embedding_cache(self, cache: object, focuses: List[str]) -> None:
        """注入 EmbeddingCache 和 focus 列表，启用混合评分。

        在 retrieval_agent 创建 DocPool 后立即调用。
        cache 的 focus embedding 可能尚未就绪（precompute_focuses 正在并行），
        wait_and_score 会在需要时等待。
        """
        self._embedding_cache = cache
        self._focuses = focuses

    def _get_doc_id(self, candidate: dict) -> str:
        return candidate.get("id", candidate.get("document_id", candidate.get("doc_id", "")))

    async def submit_and_read(
        self, candidates: List[dict], query: str,
        group_idx: int = 0,
    ) -> List[str]:
        """注册候选文档，BM25 筛选 top-N，触发读取。返回选中的 doc_ids。

        非阻塞：已缓存的 doc 不重复读取；未缓存的启动异步读取。
        调用方随后用 score_and_collect() 等待 + 评分。
        """
        cfg = self.pool_config

        # BM25 文档级排序
        ranked = _bm25_rank_candidates(query, candidates)[:cfg.top_n_docs]

        selected_ids = []
        to_read_block: List[dict] = []
        to_read_mcp: List[dict] = []

        async with self._lock:
            for c in ranked:
                doc_id = self._get_doc_id(c)
                if not doc_id:
                    continue
                selected_ids.append(doc_id)
                # 记录 doc→focus_group 映射
                self._doc_group_indices.setdefault(doc_id, set()).add(group_idx)
                # 缓存 meta
                if doc_id not in self._doc_meta:
                    self._doc_meta[doc_id] = {
                        "title": c.get("title", ""),
                        "url": c.get("url", ""),
                        "doc_type": c.get("doc_type", "DOCX"),
                    }
                # 记录第一个请求该文档的 sub_query（用于 Stage 4 大表 LLM）
                if doc_id not in self._doc_query:
                    self._doc_query[doc_id] = query
                # 已完成或正在读 → 跳过
                if doc_id in self._done or doc_id in self._reading:
                    continue
                # 创建完成 event
                if doc_id not in self._doc_ready_events:
                    self._doc_ready_events[doc_id] = asyncio.Event()
                self._reading.add(doc_id)

            # 路由决策：未缓存文档数 vs mcp_threshold
            uncached = [did for did in selected_ids
                        if did not in self._done and did in self._reading]

        # 在锁外调度读取（避免持锁 IO）
        if uncached:
            # concept 且未缓存文档多 → MCP 并发 + top-3 走 Block API
            if (self.query_type == "concept"
                    and len(uncached) > cfg.mcp_threshold):
                block_ids = set(uncached[:3])  # top-3 走 Block API
                for did in uncached:
                    c = self._doc_meta[did]
                    if did in block_ids:
                        to_read_block.append({"id": did, **c})
                        self._doc_route[did] = "block_api"
                    else:
                        to_read_mcp.append({"id": did, **c})
                        self._doc_route[did] = "mcp"
            else:
                # data / mixed / 少量文档 → 全部 Block API
                for did in uncached:
                    c = self._doc_meta[did]
                    to_read_block.append({"id": did, **c})
                    self._doc_route[did] = "block_api"

            # 启动异步读取（不 await，让调用方通过 event 等待）
            if to_read_block:
                asyncio.create_task(self._read_block_api_batch(to_read_block))
            if to_read_mcp:
                asyncio.create_task(self._read_mcp_batch(to_read_mcp))

        return selected_ids

    async def wait_and_score(
        self, doc_ids: List[str], query: str,
        total_timeout: float = 12.0,
        emb_weight: float = 0.7,
        kw_weight: float = 0.3,
        noise_threshold: float = 0.33,
    ) -> "List[UnifiedChunk]":
        """等待指定文档就绪，混合评分（embedding + BM25），返回过滤后的 UnifiedChunks。

        混合评分公式:
          bm25_norm = BM25(expanded_sub_query, chunk) / 5.0, clamped [0, 1]
          sim_emb = max(cosine_sim(focus_emb, chunk_emb) for each focus)
          hybrid_score = emb_weight × sim_emb + kw_weight × bm25_norm

        噪声过滤: hybrid_score < noise_threshold → 不输出

        无 embedding 时 fallback 到纯 BM25（noise_threshold 自动降为 0.05）。
        """
        from .models import UnifiedChunk
        cfg = self.pool_config

        # ── 准备 BM25 扩展 query ─────────────────────────────────
        if query not in self._expanded_cache:
            from tools.kw_generalization import expand_keywords
            raw_words = set(re.findall(r'[\w\u4e00-\u9fff]{2,}', query.lower()))
            self._expanded_cache[query] = " ".join(expand_keywords(raw_words))
        expanded_query = self._expanded_cache[query]

        # ── 准备 embedding（如果注入了 cache）────────────────────
        cache = self._embedding_cache
        focus_embs = None
        if cache and hasattr(cache, 'focus_embeddings') and cache.focus_embeddings:
            focus_embs = cache.focus_embeddings
        use_emb = focus_embs is not None and len(focus_embs) > 0
        if not use_emb:
            # 无 embedding fallback：纯 BM25，降低噪声阈值
            noise_threshold = 0.05

        all_scored: List[Tuple[float, UnifiedChunk]] = []
        pending = set(doc_ids)
        collected_docs = 0
        n_filtered = 0
        deadline = time.monotonic() + total_timeout

        # ── Compute IDF from all ready docs (progressive update) ─
        idf_cache: Optional[Dict[str, float]] = None

        while pending and time.monotonic() < deadline:
            newly_done = []
            for doc_id in list(pending):
                event = self._doc_ready_events.get(doc_id)
                if event is None or event.is_set():
                    newly_done.append(doc_id)

            # Recompute IDF when new docs arrive (lightweight, <5ms)
            if newly_done and self._doc_chunks:
                idf_cache = _compute_idf(self._doc_chunks)

            for doc_id in newly_done:
                pending.discard(doc_id)
                chunks = self._doc_chunks.get(doc_id, [])
                if not chunks:
                    continue

                meta = self._doc_meta.get(doc_id, {})
                collected_docs += 1

                # ── 批量 embed chunk texts（如果可用）────────────
                chunk_embs_map: Dict[int, List[float]] = {}
                if use_emb:
                    from tools.embedding import batch_embed, _cosine_similarity
                    import os
                    api_key = os.getenv("DASHSCOPE_API_KEY", "")
                    if api_key:
                        try:
                            texts = [c.content[:500] for c in chunks]
                            embs = batch_embed(texts, api_key)
                            for i, emb in enumerate(embs):
                                chunk_embs_map[i] = emb
                        except Exception as e:
                            logger.warning("DocPool: chunk embedding failed: %s", e)

                # ── 对每个 chunk 计算混合分数 ────────────────────
                chunk_scores: List[Tuple[float, float, float, dict, Chunk]] = []  # (hybrid, bm25_norm, sim_emb, per_focus, chunk)
                for ci, chunk in enumerate(chunks):
                    # BM25 侧 (with IDF)
                    bm25_raw = _bm25_score(expanded_query, chunk.content, idf=idf_cache)
                    bm25_norm = min(1.0, max(0.0, bm25_raw / 5.0))

                    # Embedding 侧：per-focus scoring
                    sim_emb = 0.0
                    per_focus_sims = {}
                    if use_emb and ci in chunk_embs_map and focus_embs:
                        c_emb = chunk_embs_map[ci]
                        focus_sims = []
                        for fi, f_emb in enumerate(focus_embs):
                            sim = _cosine_similarity(f_emb, c_emb)
                            focus_sims.append(sim)
                            if fi < len(self._focuses):
                                per_focus_sims[self._focuses[fi]] = round(sim, 4)
                        # Use mean of top-2 focus sims (not max)
                        focus_sims.sort(reverse=True)
                        top_n = min(2, len(focus_sims))
                        sim_emb = sum(focus_sims[:top_n]) / top_n if top_n > 0 else 0.0

                    # 混合评分
                    if use_emb:
                        hybrid = emb_weight * sim_emb + kw_weight * bm25_norm
                    else:
                        hybrid = bm25_norm  # fallback 纯 BM25

                    chunk_scores.append((hybrid, bm25_norm, sim_emb, per_focus_sims, chunk))

                # 噪声过滤 + 排序
                before_filter = len(chunk_scores)
                chunk_scores = [(h, b, se, pf, c) for h, b, se, pf, c in chunk_scores if h >= noise_threshold]
                n_filtered += before_filter - len(chunk_scores)
                chunk_scores.sort(key=lambda x: x[0], reverse=True)

                # 每篇文档取 top-K
                for hybrid, bm25_norm, sim_emb_val, pf_sims, chunk in chunk_scores[:cfg.max_per_doc]:
                    uc = UnifiedChunk(
                        text=chunk.content,
                        source_name=meta.get("title", chunk.doc_title),
                        source_url=meta.get("url", chunk.doc_url),
                        source_tool="feishu",
                        heading_path=chunk.heading_path,
                        score=round(hybrid, 4),
                        metadata={
                            "content_type": chunk.content_type.value if hasattr(chunk.content_type, 'value') else str(chunk.content_type),
                            "doc_id": doc_id,
                            "route": self._doc_route.get(doc_id, "unknown"),
                            "bm25_norm": round(bm25_norm, 4),
                            "sim_emb": round(sim_emb_val, 4),
                            "per_focus_sims": pf_sims,
                        },
                    )
                    all_scored.append((hybrid, uc))

            if not newly_done and pending:
                await asyncio.sleep(0.15)

        if pending:
            logger.warning(
                "DocPool.wait_and_score: total_timeout %.1fs — %d/%d docs still pending, returning %d collected",
                total_timeout, len(pending), len(doc_ids), collected_docs,
            )

        # 全局排序 + 截断
        all_scored.sort(key=lambda x: x[0], reverse=True)
        result = [uc for _, uc in all_scored[:cfg.max_output]]

        logger.info(
            "DocPool.wait_and_score: query=%.30r → %d chunks from %d/%d docs "
            "(filtered=%d noise, emb=%s, %.1fs)",
            query, len(result), collected_docs, len(doc_ids),
            n_filtered, use_emb, total_timeout,
        )
        return result

    # ── score_and_collect: event-driven per-doc scoring ─────────

    def _score_doc_chunks(
        self, chunks: list, query: str, doc_id: str,
        expanded_query: str, idf_cache, use_emb: bool, focus_embs,
        chunk_embs_map: dict, noise_threshold: float,
        bm25_noise: float = 0.05,
    ) -> "List[UnifiedChunk]":
        """Two-stage scoring for one doc's chunks.

        Stage 1: BM25+IDF → bm25_noise filter (keyword pre-screen)
        Stage 2: RSF hybrid = 0.7×emb + 0.3×BM25 → noise_threshold filter
        """
        from .models import UnifiedChunk
        cfg = self.pool_config
        meta = self._doc_meta.get(doc_id, {})

        # Stage 1: BM25 noise pre-filter
        bm25_passed = []
        for ci, chunk in enumerate(chunks):
            bm25_raw = _bm25_score(expanded_query, chunk.content, idf=idf_cache)
            bm25_norm = min(1.0, max(0.0, bm25_raw / 5.0))
            if bm25_norm >= bm25_noise:
                bm25_passed.append((ci, chunk, bm25_norm))

        # Stage 2: RSF hybrid scoring on BM25-passed chunks
        scored = []
        for ci, chunk, bm25_norm in bm25_passed:
            sim_emb = 0.0
            per_focus_sims = {}
            if use_emb and ci in chunk_embs_map and focus_embs:
                from tools.embedding import _cosine_similarity
                c_emb = chunk_embs_map[ci]
                focus_sims = []
                for fi, f_emb in enumerate(focus_embs):
                    sim = _cosine_similarity(f_emb, c_emb)
                    focus_sims.append(sim)
                    if fi < len(self._focuses):
                        per_focus_sims[self._focuses[fi]] = round(sim, 4)
                focus_sims.sort(reverse=True)
                top_n = min(2, len(focus_sims))
                sim_emb = sum(focus_sims[:top_n]) / top_n if top_n > 0 else 0.0

            # Doc: embedding-heavy (0.7 emb + 0.3 BM25)
            hybrid = (0.7 * sim_emb + 0.3 * bm25_norm) if use_emb else bm25_norm

            if hybrid >= noise_threshold:
                scored.append((hybrid, bm25_norm, sim_emb, per_focus_sims, chunk))

        scored.sort(key=lambda x: x[0], reverse=True)

        result = []
        for hybrid, bm25_norm, sim_emb_val, pf_sims, chunk in scored[:cfg.max_per_doc]:
            result.append(UnifiedChunk(
                text=chunk.content,
                source_name=meta.get("title", chunk.doc_title),
                source_url=meta.get("url", chunk.doc_url),
                source_tool="feishu",
                heading_path=chunk.heading_path,
                score=round(hybrid, 4),
                metadata={
                    "content_type": chunk.content_type.value if hasattr(chunk.content_type, 'value') else str(chunk.content_type),
                    "doc_id": doc_id,
                    "route": self._doc_route.get(doc_id, "unknown"),
                    "bm25_norm": round(bm25_norm, 4),
                    "sim_emb": round(sim_emb_val, 4),
                    "per_focus_sims": pf_sims,
                },
            ))
        return result

    async def score_and_collect(
        self, doc_ids: "List[str]", query: str,
        total_timeout: float = 12.0,
        noise_threshold: float = 0.33,
    ) -> "List[UnifiedChunk]":
        """Event-driven per-doc scoring. Replaces wait_and_score.

        Differences from wait_and_score:
        - Event-driven (no 0.15s polling)
        - Per-doc scoring as each doc completes
        - No global sort / max_output truncation (reranker handles that)
        """
        from .models import UnifiedChunk

        # Prepare expanded query
        if query not in self._expanded_cache:
            from tools.kw_generalization import expand_keywords
            raw_words = set(re.findall(r'[\w\u4e00-\u9fff]{2,}', query.lower()))
            self._expanded_cache[query] = " ".join(expand_keywords(raw_words))
        expanded_query = self._expanded_cache[query]

        # Prepare embedding
        cache = self._embedding_cache
        focus_embs = None
        if cache and hasattr(cache, 'focus_embeddings') and cache.focus_embeddings:
            focus_embs = cache.focus_embeddings
        use_emb = focus_embs is not None and len(focus_embs) > 0
        if not use_emb:
            noise_threshold = min(noise_threshold, 0.05)

        result: List[UnifiedChunk] = []
        pending = set(doc_ids)
        collected_docs = 0
        n_filtered = 0
        deadline = time.monotonic() + total_timeout
        idf_cache = None

        while pending and time.monotonic() < deadline:
            # Event-driven: wait for any doc to complete
            wait_coros = []
            for doc_id in pending:
                evt = self._doc_ready_events.get(doc_id)
                if evt and not evt.is_set():
                    wait_coros.append(evt.wait())

            if wait_coros:
                remaining = max(0.1, deadline - time.monotonic())
                try:
                    await asyncio.wait(
                        [asyncio.ensure_future(c) for c in wait_coros],
                        timeout=min(1.0, remaining),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except Exception:
                    pass
            else:
                await asyncio.sleep(0.1)

            # Find newly done docs
            newly_done = []
            for doc_id in list(pending):
                event = self._doc_ready_events.get(doc_id)
                if event is None or event.is_set():
                    newly_done.append(doc_id)

            if newly_done and self._doc_chunks:
                idf_cache = _compute_idf(self._doc_chunks)

            for doc_id in newly_done:
                pending.discard(doc_id)
                chunks = self._doc_chunks.get(doc_id, [])
                if not chunks:
                    continue

                collected_docs += 1

                # Batch embed chunk texts
                chunk_embs_map = {}
                if use_emb:
                    from tools.embedding import batch_embed
                    import os
                    api_key = os.getenv("DASHSCOPE_API_KEY", "")
                    if api_key:
                        try:
                            texts = [c.content[:500] for c in chunks]
                            embs = batch_embed(texts, api_key)
                            for i, emb in enumerate(embs):
                                chunk_embs_map[i] = emb
                        except Exception as e:
                            logger.warning("DocPool: chunk embedding failed: %s", e)

                # Score and collect
                before = len(result)
                doc_result = self._score_doc_chunks(
                    chunks, query, doc_id, expanded_query, idf_cache,
                    use_emb, focus_embs, chunk_embs_map, noise_threshold,
                )
                result.extend(doc_result)
                n_filtered += len(chunks) - len(doc_result)

                logger.debug("DocPool.score_and_collect: doc %s → %d chunks",
                             doc_id[:16], len(doc_result))

        if pending:
            logger.warning(
                "DocPool.score_and_collect: timeout %.1fs — %d/%d docs pending, %d collected",
                total_timeout, len(pending), len(doc_ids), collected_docs,
            )

        logger.info(
            "DocPool.score_and_collect: query=%.30r → %d chunks from %d/%d docs "
            "(filtered=%d, emb=%s)",
            query, len(result), collected_docs, len(doc_ids), n_filtered, use_emb,
        )
        return result

    # ── Block API 批量读取 ───────────────────────────────────────

    async def _read_block_api_batch(self, docs: List[dict]) -> None:
        """Block API 读取 + chunking，逐篇完成后立即通知。"""
        from feishu_read_tool.client import FeishuClient
        from feishu_read_tool.docx_pipeline import run_rag_pipeline

        # 使用 pool 级别的 semaphore（而非每次调用新建），确保跨 sub_query 并发安全
        sem = self._block_api_sem

        async def _read_one(doc: dict):
            doc_id = doc["id"]
            async with sem:
                for attempt in range(3):
                    try:
                        # run_rag_pipeline 是同步的，放线程池
                        # 传真实 sub_query（用于 Stage 4 大表 LLM 提取），跳过 Stage 2+5 评分
                        rag = await asyncio.to_thread(
                            run_rag_pipeline,
                            query=self._doc_query.get(doc_id, ""),
                            candidates=[doc],
                            user_access_token=self.uat,
                            cfg=PipelineConfig(max_candidates=1, skip_scoring=True),
                        )
                        # 转换 ChunkResult → Chunk（保留原始格式）
                        chunks = []
                        for cr in rag.chunks:
                            ct = cr.content_type
                            ct_val = ct.value if hasattr(ct, 'value') else str(ct)
                            chunks.append(Chunk(
                                chunk_id=cr.chunk_id,
                                doc_id=doc_id,
                                doc_title=cr.file_name or doc.get("title", ""),
                                doc_url=cr.file_url or doc.get("url", ""),
                                heading_path=cr.heading_path or "",
                                content_type=ct,
                                content=cr.chunks_content or cr.summary or "",
                                block_ids=[],
                                token_count=cr.token_count,
                                has_table=ct_val in ("table_md", "table_json"),
                            ))
                        self._doc_chunks[doc_id] = chunks
                        self._done.add(doc_id)
                        self._reading.discard(doc_id)
                        evt = self._doc_ready_events.get(doc_id)
                        if evt:
                            evt.set()  # 通知等待的 task
                        logger.info("DocPool: block_api done %s → %d chunks", doc_id[:16], len(chunks))
                        return
                    except Exception as e:
                        if attempt < 2:
                            await asyncio.sleep(0.5 * (attempt + 1))
                            continue
                        logger.warning("DocPool: block_api failed %s after 3 retries: %s", doc_id[:16], e)
                        self._doc_chunks[doc_id] = []
                        self._done.add(doc_id)
                        self._reading.discard(doc_id)
                        evt = self._doc_ready_events.get(doc_id)
                        if evt:
                            evt.set()

        tasks = [_read_one(doc) for doc in docs]
        await asyncio.gather(*tasks, return_exceptions=True)

    # ── MCP 批量读取 ─────────────────────────────────────────────

    async def _read_mcp_batch(self, docs: List[dict]) -> None:
        """MCP 高并发读取 + parse → chunks，逐篇完成后立即通知。"""
        from feishu_read_tool.basic_tool.mcp_fetch_doc import fetch_doc_markdown
        from feishu_read_tool.basic_tool.mcp_fetch_doc import parse_mcp_markdown_to_chunks

        async def _read_one(doc: dict):
            doc_id = doc["id"]
            for attempt in range(3):
                try:
                    result = await asyncio.to_thread(
                        fetch_doc_markdown, doc_id, self.uat, 300000,
                    )
                    if result["status"] != "success":
                        if attempt < 2:
                            await asyncio.sleep(0.3 * (attempt + 1))
                            continue
                        logger.warning("DocPool: mcp failed %s: %s", doc_id[:16], result.get("message"))
                        self._doc_chunks[doc_id] = []
                        break

                    chunks = parse_mcp_markdown_to_chunks(
                        markdown=result["content"],
                        doc_id=doc_id,
                        doc_title=doc.get("title", ""),
                        doc_url=doc.get("url", ""),
                        max_chunk_tokens=800,
                    )
                    self._doc_chunks[doc_id] = chunks
                    logger.info("DocPool: mcp done %s → %d chunks", doc_id[:16], len(chunks))
                    break
                except Exception as e:
                    if attempt < 2:
                        await asyncio.sleep(0.3 * (attempt + 1))
                        continue
                    logger.warning("DocPool: mcp failed %s after 3 retries: %s", doc_id[:16], e)
                    self._doc_chunks[doc_id] = []

            self._done.add(doc_id)
            self._reading.discard(doc_id)
            evt = self._doc_ready_events.get(doc_id)
            if evt:
                evt.set()

        tasks = [_read_one(doc) for doc in docs]
        await asyncio.gather(*tasks, return_exceptions=True)

    # ── 状态查询 ─────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        return {
            "cached": len(self._done),
            "reading": len(self._reading),
            "total_chunks": sum(len(v) for v in self._doc_chunks.values()),
            "routes": dict(self._doc_route),
        }
