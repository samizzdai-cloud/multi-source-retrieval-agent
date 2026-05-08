"""阿里云百炼知识库检索工具 — 多知识库并行检索

支持通过 BAILIAN_INDEX_MAP 配置多个知识库，每次检索并行查询所有已启用的 index。

配置示例 (.env):
  BAILIAN_INDEX_MAP=nubia:hzu9kj1c5x,hypershell:pdws24t6vm
  BAILIAN_WORKSPACE_ID=ws-96qq5jb335bgx0ul
"""

import asyncio
import concurrent.futures
import contextvars
import functools
import logging
import os
from typing import Any, Callable, Dict, List, Optional


async def _exec_to_thread(
    executor: Optional[concurrent.futures.Executor],
    fn: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """模块级 to_thread helper：executor=None 走默认池。保留 contextvars 传播。"""
    if executor is None:
        return await asyncio.to_thread(fn, *args, **kwargs)
    loop = asyncio.get_event_loop()
    ctx = contextvars.copy_context()
    func_call = functools.partial(ctx.run, fn, *args, **kwargs)
    return await loop.run_in_executor(executor, func_call)

from dotenv import load_dotenv

from tools.core.models import UnifiedChunk

load_dotenv()

logger = logging.getLogger(__name__)

BAILIAN_WORKSPACE_ID = os.getenv("BAILIAN_WORKSPACE_ID", "")
ALIBABA_CLOUD_ACCESS_KEY_ID = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
ALIBABA_CLOUD_ACCESS_KEY_SECRET = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")


def get_all_configured_indexes() -> Dict[str, str]:
    """返回当前配置的所有百炼知识库 {index_name: index_id} (从环境变量读取)。

    读取 BAILIAN_INDEX_MAP 环境变量，最后 fallback 到 BAILIAN_INDEX_ID（单库旧版配置）。
    """
    raw = os.getenv("BAILIAN_INDEX_MAP", "").strip()
    if raw:
        result = {}
        for entry in raw.split(","):
            entry = entry.strip()
            if ":" in entry:
                name, index_id = entry.split(":", 1)
                name, index_id = name.strip(), index_id.strip()
                if name and index_id:
                    result[name] = index_id
        if result:
            return result

    fallback = os.getenv("BAILIAN_INDEX_ID", "").strip()
    return {"default": fallback} if fallback else {}


def _build_client():
    """构建百炼 API 客户端（延迟导入）。"""
    from alibabacloud_bailian20231229.client import Client # type: ignore
    from alibabacloud_tea_openapi.models import Config # type: ignore

    config = Config(
        access_key_id=ALIBABA_CLOUD_ACCESS_KEY_ID,
        access_key_secret=ALIBABA_CLOUD_ACCESS_KEY_SECRET,
        endpoint="bailian.cn-beijing.aliyuncs.com",
    )
    return Client(config)


import hashlib as _hashlib
import re as _re


def _dedupe_text_hash(text: str) -> str:
    """对 chunk text 取 MD5，用于并行结果去重（与 reranker 保持相同逻辑）。"""
    normalized = _re.sub(r"\s+", " ", text[:500].strip().lower())
    return _hashlib.md5(normalized.encode()).hexdigest()


def _build_search_filters(tags: List[str]) -> List[Dict]:
    """将 tag 列表构建为百炼 SearchFilters 格式。

    l2 业务分类标签 → 一个子分组（OR 语义，多个匹配任一即可）
    时效标签（YYYY_QN / last_long） → 单独一个子分组（AND 语义，与 l2 分组取交集）

    示例:
      tags=["Ads", "2025_Q4"] →
        [{"tags": ["Ads"]}, {"tags": ["2025_Q4"]}]   # l2 AND time
      tags=["Ads", "DTC"] →
        [{"tags": ["Ads", "DTC"]}]                   # l2 OR（同一子分组）
    """
    time_pattern = _re.compile(r'^(last_long|\d{4}_Q[1-4])$')
    l2_tags = [t for t in tags if not time_pattern.match(t)]
    time_tags = [t for t in tags if time_pattern.match(t)]

    filters = []
    if l2_tags:
        filters.append({"tags": l2_tags})   # OR within l2 group
    if time_tags:
        filters.append({"tags": time_tags}) # AND with l2 group
    return filters


def _retrieve_single_index(
    query: str,
    index_id: str,
    tags: Optional[List[str]] = None,
    dense_similarity_top_k: int = 20,
    sparse_similarity_top_k: int = 20,
    enable_reranking: bool = True,
    rerank_min_score: float = 0.5,
    rerank_top_n: int = 8,
) -> Dict[str, Any]:
    """调用百炼单个知识库检索接口（同步）。

    Args:
        tags: 可选标签过滤（l2分类 + 时效）。空/None 则不过滤，全量检索。
    """
    if not BAILIAN_WORKSPACE_ID:
        return {"status": "error", "message": "未配置 BAILIAN_WORKSPACE_ID"}
    if not ALIBABA_CLOUD_ACCESS_KEY_ID or not ALIBABA_CLOUD_ACCESS_KEY_SECRET:
        return {"status": "error", "message": "未配置阿里云 AccessKey"}

    try:
        from alibabacloud_bailian20231229.models import RetrieveRequest, RetrieveRequestRerank # type: ignore
        client = _build_client()

        request = RetrieveRequest(
            query=query,
            index_id=index_id,
            dense_similarity_top_k=dense_similarity_top_k,
            sparse_similarity_top_k=sparse_similarity_top_k,
            enable_reranking=enable_reranking,
            rerank_min_score=rerank_min_score,
            rerank_top_n=rerank_top_n,
            rerank=[RetrieveRequestRerank(
                model_name="qwen3-rerank-hybrid",
            )],
        )

        # Tag filtering via SearchFilters
        if tags:
            filters = _build_search_filters(tags)
            if filters:
                request.search_filters = filters

        response = client.retrieve(BAILIAN_WORKSPACE_ID, request)
        body = response.body

        # Tag 兜底：以下两种情况去掉 tag 重试
        #   (1) success=False 且 code=Index.NoResult — 原有逻辑
        #   (2) success=True 但 nodes 为空         — 修复之前的漏洞
        # 重试后若仍空，直接返回 []，不再降低分数阈值
        needs_tag_fallback = tags and request.search_filters and (
            (not body.success and body.code == "Index.NoResult")
            or (body.success and not (body.data and body.data.nodes))
        )
        if needs_tag_fallback:
            logger.info(
                "Tag-filtered retrieve returned no results for index=%s, retrying without tags",
                index_id,
            )
            request.search_filters = None
            response = client.retrieve(BAILIAN_WORKSPACE_ID, request)
            body = response.body

        if not body.success:
            return {"status": "error", "message": f"检索失败: code={body.code}, msg={body.message}"}

        nodes = []
        if body.data and body.data.nodes:
            for node in body.data.nodes:
                nodes.append({
                    "text": node.text or "",
                    "score": node.score or 0.0,
                    "metadata": node.metadata or {},
                })
        return {"status": "success", "nodes": nodes}

    except ImportError:
        return {"status": "error", "message": "未安装百炼 SDK: pip install alibabacloud_bailian20231229"}
    except Exception as e:
        logger.warning("_retrieve_single_index failed (index=%s): %s", index_id, e)
        return {"status": "error", "message": f"检索异常: {str(e)}"}


def _node_to_unified(node: dict, index_name: str, index_id: str) -> UnifiedChunk:
    """将百炼 node dict 转换为 UnifiedChunk。

    字段映射（优先级从高到低）:
      source_name : doc_name（文件名）> 文件名[0]（列表字段）> title（章节标题，备用）
      heading_path: hier_title（层级路径）> title（章节标题）
      source_url  : doc_url（OSS 直链）
    """
    meta = node.get("metadata", {})

    # 文件名: doc_name 是标准字段；"文件名" 是列表字段（取第一项）
    file_names = meta.get("文件名", [])
    doc_name = (
        meta.get("doc_name")
        or (file_names[0] if file_names else None)
        or meta.get("title", "")
    )

    # 章节路径: hier_title 包含完整层级路径，如 "报告名\n>章节名\n>子章节>"
    hier = meta.get("hier_title") or meta.get("title", "")

    return UnifiedChunk(
        text=node.get("text", ""),
        source_name=doc_name.strip(),
        source_url=meta.get("doc_url", ""),
        source_tool="bailian",
        heading_path=hier.strip(),
        score=float(node.get("score", 0.0)),
        metadata={**meta, "index_name": index_name, "index_id": index_id},
    )


async def bailian_retrieve_unified(
    query: str,
    index_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    indexes: Optional[List[str]] = None,
    rerank_top_n_override: Optional[int] = None,
    rerank_min_score_override: Optional[float] = None,
    executor: Optional[concurrent.futures.Executor] = None,
) -> List[UnifiedChunk]:
    """百炼多知识库并行检索，返回 UnifiedChunk 列表。

    Args:
        query: 检索文本
        index_id: 指定单个知识库 ID（旧版兼容，优先级低于 indexes）
        tags: tag 过滤列表（l2分类 + 时效），空/None = 不过滤全量检索
        indexes: 指定知识库名列表（["nubia", "hypershell"]），空/None = 查全部
        rerank_top_n_override: 覆盖 rerank_top_n 默认值（12）
        rerank_min_score_override: 覆盖 rerank_min_score 默认值（0.5）

    每个 chunk.metadata["index_name"] 标注来源知识库名。
    每个 chunk.metadata["filtered_by_tags"] 标注使用的 tag 过滤。
    """
    all_configured = get_all_configured_indexes()
    if not all_configured:
        logger.warning("bailian_retrieve_unified: no indexes configured")
        return []

    # 确定要查询的 {name: index_id} 子集
    if index_id:
        # 旧版兼容: 直接指定 index_id
        name = next((n for n, i in all_configured.items() if i == index_id), "specified")
        target_indexes = {name: index_id}
    elif indexes:
        # 按知识库名过滤（planner 传入）
        target_indexes = {n: i for n, i in all_configured.items() if n in indexes}
        if not target_indexes:
            logger.warning("bailian_retrieve_unified: specified indexes %s not found in config, using all", indexes)
            target_indexes = all_configured
    else:
        target_indexes = all_configured

    effective_tags = tags or []
    # 检测是否包含时效 tag（YYYY_QN 或 last_long）
    _time_re = _re.compile(r'^(last_long|\d{4}_Q[1-4])$')
    has_time_tag = any(_time_re.match(t) for t in effective_tags)
    l2_tags = [t for t in effective_tags if not _time_re.match(t)]

    logger.info(
        "bailian_retrieve_unified: query=%.40r, indexes=%s, tags=%s",
        query, list(target_indexes.keys()), effective_tags,
    )

    # rerank_top_n：实验验证 dense/sparse top_k 对百炼无效，只有 top_n 有效
    # 默认 12（[P1.3↑] 扩大每个 index call 的返回量以提升召回）
    _rerank_top = rerank_top_n_override or 12
    _rerank_min = rerank_min_score_override if rerank_min_score_override is not None else 0.5

    async def _fetch_one(name: str, idx_id: str, call_tags: Optional[List[str]]) -> List[UnifiedChunk]:
        r = await _exec_to_thread(
            executor, _retrieve_single_index, query, idx_id, call_tags or None,
            rerank_min_score=_rerank_min, rerank_top_n=_rerank_top,
        )
        if r.get("status") != "success":
            logger.warning("bailian index '%s' (tags=%s) failed: %s", name, call_tags, r.get("message"))
            return []
        chunks = [_node_to_unified(n, name, idx_id) for n in r.get("nodes", [])]
        for c in chunks:
            c.metadata["filtered_by_tags"] = call_tags or []
        return chunks

    # 构建任务列表：
    # - 基础任务：使用完整 tags（包含时效）
    # - 若有时效 tag，额外并行一个 last_long 任务（补充长期文档背景）
    tasks: list = []
    for name, idx_id in target_indexes.items():
        tasks.append(_fetch_one(name, idx_id, effective_tags or None))
        if has_time_tag and "last_long" not in effective_tags:
            # 并行追加 last_long：l2 业务 tag AND last_long（长期文档）
            last_long_tags = l2_tags + ["last_long"] if l2_tags else ["last_long"]
            tasks.append(_fetch_one(name, idx_id, last_long_tags))
            logger.info("bailian index '%s': parallel last_long call added", name)

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 合并结果，text hash 去重（保留 score 高的版本）
    seen_hashes: Dict[str, UnifiedChunk] = {}
    for r in results:
        if isinstance(r, Exception):
            logger.warning("bailian index task raised: %s", r)
            continue
        for c in r:
            h = _dedupe_text_hash(c.text)
            if h not in seen_hashes or c.score > seen_hashes[h].score:
                seen_hashes[h] = c
    all_chunks: List[UnifiedChunk] = list(seen_hashes.values())

    logger.info(
        "bailian_retrieve_unified: %d chunks from %d indexes",
        len(all_chunks), len(target_indexes),
    )
    return all_chunks

