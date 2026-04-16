"""DashScope Embedding — text-embedding-v4 batch 调用 + cosine similarity

使用 DashScope Python SDK，单次 batch ≤10 条（API 上限），
focuses 和 chunks 使用 asyncio 并行 embed，最大化吞吐量。
"""

import asyncio
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from typing import List

logger = logging.getLogger(__name__)

_MODEL = "text-embedding-v4"
_MAX_BATCH = 10      # DashScope text-embedding-v4 单次硬上限
_EMBED_DIM = 512     # 默认向量维度（1024 精度更高但更慢）

# 线程池：embedding API 是 IO 密集型，用线程并行多个 batch
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="embed")


_EMBED_RETRYABLE = ("429", "503", "throttl", "limit", "quota", "too many")


def _call_embedding_api(
    texts: List[str],
    api_key: str,
    dimension: int = _EMBED_DIM,
    max_retries: int = 3,
    retry_delay: float = 1.0,
) -> List[List[float]]:
    """调用 DashScope TextEmbedding SDK（同步），自动重试 429/503/限流错误。"""
    import time as _time
    from dashscope import TextEmbedding

    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = TextEmbedding.call(
                api_key=api_key,
                model=_MODEL,
                input=texts,
                dimension=dimension,
                text_type="document",
            )
            if resp.status_code != HTTPStatus.OK:
                msg = getattr(resp, 'message', '')
                retryable = any(k in str(resp.status_code) or k.lower() in msg.lower()
                                for k in _EMBED_RETRYABLE)
                if retryable and attempt < max_retries - 1:
                    wait = retry_delay * (2 ** attempt)
                    logger.warning(
                        "embedding API attempt %d/%d failed (code=%s): %s — retrying in %.1fs",
                        attempt + 1, max_retries, resp.status_code, msg[:60], wait,
                    )
                    _time.sleep(wait)
                    continue
                raise ValueError(
                    f"DashScope embedding error: code={resp.status_code} "
                    f"msg={msg} request_id={getattr(resp, 'request_id', '')}"
                )
            embeddings = resp.output["embeddings"]
            embeddings.sort(key=lambda e: e["text_index"])
            return [e["embedding"] for e in embeddings]
        except ValueError:
            raise
        except Exception as e:
            last_exc = e
            err_str = str(e)
            retryable = any(k.lower() in err_str.lower() for k in _EMBED_RETRYABLE)
            if retryable and attempt < max_retries - 1:
                wait = retry_delay * (2 ** attempt)
                logger.warning(
                    "embedding API attempt %d/%d exception: %s — retrying in %.1fs",
                    attempt + 1, max_retries, err_str[:60], wait,
                )
                _time.sleep(wait)
            else:
                break

    raise RuntimeError(f"Embedding API failed after {max_retries} attempts: {last_exc}") from last_exc


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """计算两个向量的余弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _split_batches(texts: List[str]) -> List[List[str]]:
    """将文本列表切分为 ≤_MAX_BATCH 的批次，并截断超长文本。"""
    batches = []
    for i in range(0, len(texts), _MAX_BATCH):
        batch = [t[:1500] for t in texts[i:i + _MAX_BATCH]]
        batches.append(batch)
    return batches


async def _batch_embed_parallel(
    texts: List[str],
    api_key: str,
    dimension: int = _EMBED_DIM,
) -> List[List[float]]:
    """异步并行 embed：将文本切分为多个 batch，所有 batch 同时提交到线程池。

    batch=10, 3 批次并行 ≈ 1 次 API 调用延迟（~2s），而非串行的 3×2s=6s。
    """
    batches = _split_batches(texts)
    if not batches:
        return []

    loop = asyncio.get_event_loop()

    async def _embed_one(batch: List[str]) -> List[List[float]]:
        return await loop.run_in_executor(
            _EXECUTOR, _call_embedding_api, batch, api_key, dimension
        )

    results = await asyncio.gather(*[_embed_one(b) for b in batches])

    all_embs: List[List[float]] = []
    for r in results:
        all_embs.extend(r)
    return all_embs


def batch_embed(
    texts: List[str],
    api_key: str,
    dimension: int = _EMBED_DIM,
) -> List[List[float]]:
    """同步接口：串行分批（用于非 async 上下文）。"""
    all_embeddings: List[List[float]] = []
    for batch in _split_batches(texts):
        all_embeddings.extend(_call_embedding_api(batch, api_key, dimension=dimension))
    return all_embeddings


async def compute_focus_chunk_similarities(
    focuses: List[str],
    chunk_texts: List[str],
    api_key: str,
    dimension: int = _EMBED_DIM,
) -> List[List[float]]:
    """并行计算 focuses × chunks 的相似度矩阵（保留供直接调用）。"""
    logger.info(
        "embedding: %d focuses + %d chunks (parallel batches)",
        len(focuses), len(chunk_texts),
    )
    focus_embs, chunk_embs = await asyncio.gather(
        _batch_embed_parallel(focuses, api_key, dimension),
        _batch_embed_parallel(chunk_texts, api_key, dimension),
    )
    sim_matrix = []
    for f_emb in focus_embs:
        row = [_cosine_similarity(f_emb, c_emb) for c_emb in chunk_embs]
        sim_matrix.append(row)
    return sim_matrix


# ── EmbeddingCache: 渐进式 embedding 缓存 ────────────────────────

import hashlib
import re
from typing import Dict, Optional, Set

def _text_hash_for_cache(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text[:500].strip().lower())
    return hashlib.md5(normalized.encode()).hexdigest()


class EmbeddingCache:
    """贯穿 eval → reranker 的渐进式 embedding 缓存。

    生命周期: 1 次 query 请求。
    用法:
      cache = EmbeddingCache(api_key, enabled=True)
      await cache.precompute_focuses(focuses)         # 与检索并行
      cache.submit_chunks(new_chunks)                 # eval 层每次 task 完成时非阻塞调用
      focus_hits = cache.get_focus_hits(all_chunks)   # eval 层早停判断
      await cache.wait_pending()                      # rerank 前确保全部完成
      sim_matrix = cache.get_sim_matrix(focuses, chunks)  # reranker 消费
    """

    def __init__(self, api_key: str = "", enabled: bool = True, sim_threshold: float = 0.4):
        self.api_key = api_key
        self.enabled = enabled and bool(api_key)
        self.sim_threshold = sim_threshold  # focus 命中判断阈值

        self.focus_texts: List[str] = []
        self.focus_embeddings: Optional[List[List[float]]] = None
        self.chunk_embeddings: Dict[str, List[float]] = {}  # text_hash → embedding
        self._pending_tasks: Set[asyncio.Task] = set()
        self._failed = False  # API 失败后停止所有后续调用

    async def precompute_focuses(self, focuses: List[str]) -> None:
        """Phase 1: embed all focuses. 与检索并行启动，通常 <200ms。"""
        if not self.enabled or not focuses:
            return
        self.focus_texts = focuses
        try:
            self.focus_embeddings = await _batch_embed_parallel(focuses, self.api_key)
            logger.info("EmbeddingCache: focus embed done (%d focuses)", len(focuses))
        except Exception as e:
            logger.warning("EmbeddingCache: focus embed failed, disabling: %s", e)
            self._failed = True
            self.enabled = False

    def submit_chunks(self, chunks: "List") -> None:
        """Phase 2: 非阻塞提交 chunk embedding 到后台线程池。"""
        if not self.enabled or self._failed or not chunks:
            return
        # 只 embed 尚未缓存的 chunks
        new_texts = []
        new_hashes = []
        for c in chunks:
            h = _text_hash_for_cache(c.text)
            if h not in self.chunk_embeddings:
                new_texts.append(c.text[:500])
                new_hashes.append(h)
        if not new_texts:
            return

        async def _do_embed():
            try:
                embs = await _batch_embed_parallel(new_texts, self.api_key)
                for h, emb in zip(new_hashes, embs):
                    self.chunk_embeddings[h] = emb
            except Exception as e:
                logger.warning("EmbeddingCache: chunk embed failed: %s", e)
                self._failed = True

        task = asyncio.create_task(_do_embed())
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        logger.debug("EmbeddingCache: submitted %d chunks for embed", len(new_texts))

    def get_focus_hits(
        self,
        all_chunks: "List",
        min_score: float = 0.3,
        focus_keywords_fallback: Optional[Dict[str, set]] = None,
    ) -> Dict[str, int]:
        """Eval 层用：基于已有的 embedding 计算 per-focus 命中数。
        未 embed 完成的 chunk 用关键词 fallback。"""
        hits: Dict[str, int] = {f: 0 for f in self.focus_texts}
        if not self.focus_texts:
            return hits

        for chunk in all_chunks:
            if chunk.score < min_score:
                continue
            h = _text_hash_for_cache(chunk.text)
            chunk_emb = self.chunk_embeddings.get(h)

            for fi, focus in enumerate(self.focus_texts):
                if self.focus_embeddings and chunk_emb:
                    sim = _cosine_similarity(self.focus_embeddings[fi], chunk_emb)
                    if sim >= self.sim_threshold:
                        hits[focus] = hits.get(focus, 0) + 1
                elif focus_keywords_fallback and focus in focus_keywords_fallback:
                    chunk_words = set(re.findall(r'[\w\u4e00-\u9fff]+', chunk.text[:500].lower()))
                    if chunk_words & focus_keywords_fallback[focus]:
                        hits[focus] = hits.get(focus, 0) + 1
        return hits

    async def wait_pending(self) -> None:
        """Rerank 前调用：等待所有后台 chunk embedding 完成。"""
        if self._pending_tasks:
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)
            self._pending_tasks.clear()
        logger.info(
            "EmbeddingCache: all pending done, cache has %d chunk embeddings",
            len(self.chunk_embeddings),
        )

    def get_sim_matrix(
        self,
        focuses: List[str],
        chunks: "List",
    ) -> Optional[List[List[float]]]:
        """Reranker 用：从缓存组装 N×M 相似度矩阵。缺失的用 0.0 填充。"""
        if not self.enabled or not self.focus_embeddings:
            return None
        sim_matrix = []
        for fi, f_emb in enumerate(self.focus_embeddings):
            row = []
            for c in chunks:
                h = _text_hash_for_cache(c.text)
                c_emb = self.chunk_embeddings.get(h)
                if c_emb:
                    row.append(_cosine_similarity(f_emb, c_emb))
                else:
                    row.append(0.0)
            sim_matrix.append(row)
        return sim_matrix
