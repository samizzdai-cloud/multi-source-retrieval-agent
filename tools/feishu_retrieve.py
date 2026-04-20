"""飞书多格式检索编排器 (Dispatcher)

职责：
  1. Phase A — 文档搜索（始终执行）：MCP + Wiki doc → doc_pool
  2. Phase B — 表格搜索（条件启动）：Direct + Wiki table → sheet/bitable readers
  3. 合并各 reader 输出的 UnifiedChunk[] 返回给 RetrievalAgent

搜索路由：
  - 文档类(doc/docx): MCP + Wiki doc → 按 title 去重 → doc_pool
  - 表格类(sheet/bitable): Direct API + Wiki table → TablePool → readers
  - 表格搜索仅在 query_type = "data" | "mixed" 时启动

设计原则：
  - feishu_retrieve 只做路由和合并，不做具体的读取和评分
  - 每种 doc_type 的读取逻辑封装在独立 reader 中（高内聚、低耦合）
  - 新增 doc_type 支持时，只需注册新 reader，不改此文件核心逻辑
"""

import asyncio
import logging
import os
from collections import defaultdict
from typing import Dict, List, Optional

from dotenv import load_dotenv

from feishu_read_tool.search import search_feishu_docs, search_feishu_tables, search_feishu_hybrid
from feishu_read_tool.pipeline_models import PipelineConfig, ChunkContentType
from .models import UnifiedChunk

load_dotenv()
logger = logging.getLogger(__name__)

FEISHU_UAT = os.getenv("FEISHU_UAT", "")


# ── 预检策略：快速判断搜索结果是否值得深度读取 ──────────────────────────────────

# ── 零返回重试策略：简化 query + 品牌名大小写泛化 ────────────────────────────

# 已知品牌名（用于大小写泛化）— 与 source_query_config.BRAND_ROUTING 保持同步
_KNOWN_BRANDS = {
    "nubia": ["nubia", "Nubia", "NUBIA"],
    "redmagic": ["REDMAGIC", "Redmagic", "redmagic", "RedMagic"],
    "hypershell": ["Hypershell", "hypershell", "HYPERSHELL", "HyperShell"],
    "nothing": ["Nothing", "nothing", "NOTHING"],
}


def _try_brand_case_variant(query: str) -> Optional[str]:
    """尝试品牌名大小写变体，用于零返回时重试。

    检测 query 中的品牌名，返回不同大小写变体。
    例如：'NUBIA 运营' → 'nubia 运营' 或 'Nubia 运营'

    Returns:
        大小写变体的 query，或 None 表示无法生成变体
    """
    query_lower = query.lower()

    for brand_key, variants in _KNOWN_BRANDS.items():
        if brand_key in query_lower:
            # 找到当前使用的变体
            current_variant = None
            for v in variants:
                if v in query:
                    current_variant = v
                    break

            if current_variant:
                # 尝试其他变体（优先小写，次优首字母大写）
                for v in variants:
                    if v != current_variant:
                        new_query = query.replace(current_variant, v)
                        logger.info(
                            "_try_brand_case_variant: '%s' → '%s' (brand case)",
                            query, new_query
                        )
                        return new_query

    return None


def _get_brand_query_variants(query: str) -> List[str]:
    """生成品牌名大小写变体列表，用于并行搜索。
    
    返回包含原 query 和所有品牌变体的列表（去重）。
    如果 query 中没有已知品牌名，返回 [query]。
    """
    variants = [query]
    query_lower = query.lower()
    
    for brand_key, brand_variants in _KNOWN_BRANDS.items():
        if brand_key in query_lower:
            # 找到当前使用的变体
            current_variant = None
            for v in brand_variants:
                if v in query:
                    current_variant = v
                    break
            if current_variant:
                # 生成所有其他变体
                for v in brand_variants:
                    if v != current_variant:
                        new_query = query.replace(current_variant, v)
                        if new_query not in variants:
                            variants.append(new_query)
            break  # 只处理第一个匹配的品牌
    
    return variants


def _simplify_feishu_query(query: str) -> Optional[str]:
    """简化飞书搜索 query，用于零返回时重试。

    [P2.7] 策略优先级：
    1. 如果 query 有 3+ 词，保留品牌名 + 第 2 关键词 + 文档类型提示
    2. 如果包含中文国家名，去掉国家名，加文档类型词
    3. 否则返回 None（不重试）
    """
    words = query.strip().split()
    if len(words) < 2:
        return None

    # [P2.7] 检测品牌名（保留为锚点）
    brand_word = None
    for w in words:
        if w.lower() in {"nubia", "hypershell", "redmagic", "nothing"}:
            brand_word = w
            break

    # [P3-A] 策略1：3+ 词 → 保留品牌 + 最泛化的动作词
    _BROAD_WORDS = {"销售", "运营", "策略", "营销", "推广", "市场", "合作", "进度", "表现", "计划"}
    if len(words) >= 3:
        if brand_word:
            # 找第一个泛化动作词
            action = next((w for w in words if w in _BROAD_WORDS and w != brand_word), None)
            if action:
                simplified = f"{brand_word} {action}"
            else:
                other = [w for w in words if w != brand_word]
                simplified = f"{brand_word} {other[0]}"
        else:
            simplified = " ".join(words[:2])
        logger.info("_simplify_feishu_query: '%s' → '%s' (brand+action)", query, simplified)
        return simplified

    # 策略2：去掉中文国家名
    country_words = {"美国", "日本", "中国", "韩国", "德国", "英国", "法国", "澳大利亚", "加拿大"}
    filtered = [w for w in words if w not in country_words]
    if len(filtered) < len(words) and filtered:
        simplified = " ".join(filtered)
        logger.info("_simplify_feishu_query: '%s' → '%s' (remove country)", query, simplified)
        return simplified

    # [P3-A] 策略3：品牌名单独搜索（最后兜底）
    if brand_word and len(words) == 2:
        logger.info("_simplify_feishu_query: '%s' → '%s' (brand only)", query, brand_word)
        return brand_word

    return None


def _preflight_check(
    query: str,
    candidates: List[Dict],
    query_type: str,
) -> bool:
    """快速预检：判断搜索结果是否值得深度读取。
    
    策略：
    - 如果 query_type != "data"，始终返回 True（概念类不做预检）
    - 对于数据类查询，检查 title + summary 中是否包含查询关键词
    - 如果完全不匹配，提前返回 False，避免浪费时间在 doc_pool 上
    
    Returns:
        True = 值得继续读取
        False = 跳过，提前返回空结果
    """
    # 概念类查询不做预检
    if query_type == "concept":
        return True
    
    # 无候选文档
    if not candidates:
        return False
    
    # 提取查询关键词（简单分词）
    query_words = set(w for w in query.replace("　", " ").split() if len(w) >= 2)
    if not query_words:
        return True
    
    # 检查 title + summary 是否包含任一关键词
    for cand in candidates:
        title = cand.get("title", "") or ""
        summary = cand.get("summary", "") or ""
        combined = title + summary
        
        for word in query_words:
            if word in combined:
                logger.debug("preflight_check: keyword '%s' found in '%s'", word, title[:30])
                return True
    
    logger.info(
        "preflight_check SKIP: query=%.30r, query_type=%s, candidates=%d (no keyword match)",
        query, query_type, len(candidates),
    )
    return False


# ── doc_type 归类 ─────────────────────────────────────────────────────────────
_DOCX_TYPES = {"DOCX", "DOC", "docx", "doc"}
_SHEET_TYPES = {"SHEET", "sheet"}
_BITABLE_TYPES = {"BITABLE", "bitable"}


# ── ChunkResult → UnifiedChunk 转换 ──────────────────────────────────────────

def _chunk_result_to_unified(cr, source_tool: str = "feishu") -> UnifiedChunk:
    """将 docx_pipeline.ChunkResult 转换为 UnifiedChunk。"""
    content_type = cr.content_type
    if isinstance(content_type, ChunkContentType):
        content_type = content_type.value

    return UnifiedChunk(
        text=cr.chunks_content or cr.summary or "",
        source_name=cr.file_name or "",
        source_url=cr.file_url or "",
        source_tool=source_tool,
        heading_path=cr.heading_path or "",
        score=min(1.0, max(0.0, cr.relevance_score)),
        metadata={
            "content_type": content_type,
            "summary": cr.summary or "",
        },
    )


# ── Reader 实现 ────────────────────────────────────────────────────────────────

def _log_read_fail(source: str, candidates: List[dict], err: BaseException) -> None:
    """统一的 reader 失败结构化日志（S2.1 观测埋点）。

    格式：feishu.read_fail source=<reader> n=<候选数> tokens=[id1,id2,...]
          kind=<异常类型> msg=<消息截断 160 字符>
    """
    tokens = [c.get("id", "")[:12] for c in candidates[:5]]
    if len(candidates) > 5:
        tokens.append(f"...+{len(candidates)-5}")
    msg = str(err)
    if len(msg) > 160:
        msg = msg[:160] + "..."
    logger.warning(
        "feishu.read_fail source=%s n=%d tokens=%s kind=%s msg=%s",
        source, len(candidates), tokens, type(err).__name__, msg,
    )


async def _read_docx(
    query: str,
    candidates: List[dict],
    token: str,
    max_candidates: int,
    query_type: str,
) -> List[UnifiedChunk]:
    """DOCX/DOC reader — 调用 docx_pipeline 完整链路。"""
    from feishu_read_tool.docx_pipeline import run_rag_pipeline
    cfg = PipelineConfig(max_candidates=max_candidates, query_type=query_type)
    try:
        rag_result = run_rag_pipeline(
            query=query,
            candidates=candidates,
            user_access_token=token,
            cfg=cfg,
        )
        chunks = [_chunk_result_to_unified(cr) for cr in rag_result.chunks]
        logger.info("_read_docx: %d chunks from %d docs", len(chunks), rag_result.total_docs_read)
        return chunks
    except Exception as e:
        _log_read_fail("_read_docx", candidates, e)
        return []


async def _read_sheet(
    query: str,
    candidates: List[dict],
    token: str,
    query_type: str,
) -> List[UnifiedChunk]:
    """SHEET reader — 调用 sheet_reader 完整链路。"""
    from feishu_read_tool.sheet_reader import read_sheets
    try:
        loop = asyncio.get_event_loop()
        chunks = await loop.run_in_executor(None, read_sheets, query, candidates, token)
        logger.info("_read_sheet: %d chunks from %d sheets", len(chunks), len(candidates))
        return chunks
    except Exception as e:
        _log_read_fail("_read_sheet", candidates, e)
        return []


async def _read_bitable(
    query: str,
    candidates: List[dict],
    token: str,
    query_type: str,
) -> List[UnifiedChunk]:
    """BITABLE reader — 调用 bitable_reader 完整链路。"""
    from feishu_read_tool.bitable_reader import read_bitables
    try:
        loop = asyncio.get_event_loop()
        chunks = await loop.run_in_executor(None, read_bitables, query, candidates, token)
        logger.info("_read_bitable: %d chunks from %d bitables", len(chunks), len(candidates))
        return chunks
    except Exception as e:
        _log_read_fail("_read_bitable", candidates, e)
        return []


# ── 主入口：Dispatcher ────────────────────────────────────────────────────────

async def feishu_retrieve(
    query: str,
    time_filter: Optional[str] = None,
    max_candidates: int = 10,
    page_size: int = 20,
    uat: str = "",
    query_type: str = "mixed",
    doc_pool: "Optional[object]" = None,
    table_pool: "Optional[object]" = None,
    doc_pool_timeout: Optional[float] = None,
) -> List[UnifiedChunk]:
    """飞书多格式检索编排器。

    两阶段搜索：
      Phase A — 文档搜索（始终执行）：MCP + Wiki doc → doc_pool
      Phase B — 表格搜索（条件启动）：Direct + Wiki table → sheet/bitable readers
                仅在 query_type = "data" | "mixed" 时启动

    Args:
        query:         单个子查询（已由 query_planner 拆解）
        time_filter:   时间过滤，如 "last_30_days"
        max_candidates: docx reader 保留最多文档数
        page_size:     搜索每路返回条数
        uat:           用户访问令牌（优先参数值，fallback env）
        query_type:    "data" | "concept" | "mixed"
        doc_pool:      FeishuDocPool 实例（可选，跨 task 共享 docx 缓存）
        table_pool:    TablePool 实例（可选，跨 task 共享 sheet/bitable 缓存）
        doc_pool_timeout: DocPool.wait_and_score 的 total_timeout（秒）。
                          None 时沿用 DocPool 默认值（12.0s）。由 SourceAgent
                          从 SourceAgentConfig.source_timeout_s 派生传入。

    Returns:
        List[UnifiedChunk]，空列表表示无结果（不抛异常）
    """
    token = uat or FEISHU_UAT
    if not token:
        logger.warning("feishu_retrieve: FEISHU_UAT not configured")
        return []

    enable_tables = query_type in ("data", "mixed")

    # ═══════════════════════════════════════════════════════════════════════════
    # Phase A: 文档搜索（MCP + Wiki doc）— 始终执行
    # ═══════════════════════════════════════════════════════════════════════════
    doc_chunks: List[UnifiedChunk] = []

    try:
        # ── 并行搜索：原 query + 品牌大小写变体 ──────────────────────────────
        query_variants = _get_brand_query_variants(query)
        
        async def _search_one(q: str) -> Dict:
            """在线程池中执行同步搜索"""
            return await asyncio.to_thread(
                search_feishu_docs, query=q, count=page_size, uat=token, time_filter=time_filter
            )
        
        if len(query_variants) > 1:
            # 有品牌变体，并行搜索
            logger.info(
                "feishu_retrieve: parallel search with %d variants: %s",
                len(query_variants), query_variants
            )
            results = await asyncio.gather(*[_search_one(q) for q in query_variants])
            
            # 合并去重（按 doc token）
            seen_tokens = set()
            doc_candidates = []
            for r in results:
                for item in r.get("items", []):
                    doc_token = item.get("doc_token") or item.get("url", "")
                    if doc_token and doc_token not in seen_tokens:
                        seen_tokens.add(doc_token)
                        doc_candidates.append(item)
            
            logger.info(
                "feishu_retrieve: parallel search merged %d unique candidates",
                len(doc_candidates)
            )
        else:
            # 无品牌变体，单次搜索
            doc_result = search_feishu_docs(
                query=query, count=page_size, uat=token, time_filter=time_filter,
            )
            doc_candidates = doc_result.get("items", [])
            logger.info(
                "feishu_retrieve: doc search returned %d candidates",
                len(doc_candidates)
            )
        
        # ── 零返回时重试：简化 query ──────────────────────────────────────────
        if not doc_candidates:
            simplified = _simplify_feishu_query(query)
            if simplified:
                logger.info("feishu_retrieve: ZERO results, retry with simplified '%s'", simplified)
                retry_result = search_feishu_docs(
                    query=simplified, count=page_size, uat=token, time_filter=time_filter,
                )
                doc_candidates = retry_result.get("items", [])
                logger.info("feishu_retrieve: RETRY returned %d candidates", len(doc_candidates))

    except Exception as e:
        logger.warning("feishu_retrieve doc search failed: %s", e)
        doc_candidates = []

    # ═══════════════════════════════════════════════════════════════════════════
    # [P2.7] DocPool + Table 全并行：doc_pool.wait_and_score ∥ table search+read
    # 原来 table read 在 doc_pool.wait 之后串行 → 现在完全重叠
    # ═══════════════════════════════════════════════════════════════════════════

    # 启动 table search+read 的完整后台任务
    table_read_task = None
    if enable_tables:
        async def _do_table_search_and_read() -> List[UnifiedChunk]:
            """Table search + read 完整流程（后台执行，与 DocPool 并行）"""
            try:
                result = await asyncio.to_thread(
                    search_feishu_tables, query=query, count=page_size, uat=token
                )
                table_candidates = result.get("items", [])
                logger.info(
                    "feishu_retrieve: table search returned %d candidates (direct=%s wiki_table=%s)",
                    len(table_candidates),
                    result.get("_debug", {}).get("direct_count", "?"),
                    result.get("_debug", {}).get("wiki_table_count", "?"),
                )
                if not table_candidates:
                    return []

                sheet_cands = [c for c in table_candidates if c.get("doc_type", "").upper() in {"SHEET"}]
                bitable_cands = [c for c in table_candidates if c.get("doc_type", "").upper() in {"BITABLE"}]

                if table_pool is not None:
                    # submit_tables 已经只返回本次新登记的 candidate，
                    # 无需再用 needs_read() 过滤（历史遗留的重复过滤会把
                    # 本次刚加入 _reading 的 token 排除，导致没人读）。
                    selected = await table_pool.submit_tables(table_candidates, query)
                    sheet_cands = [c for c in selected if c.get("doc_type", "").upper() == "SHEET"]
                    bitable_cands = [c for c in selected if c.get("doc_type", "").upper() == "BITABLE"]

                tasks = []
                if sheet_cands:
                    tasks.append(("sheet", _read_sheet(query, sheet_cands, token, query_type)))
                if bitable_cands:
                    tasks.append(("bitable", _read_bitable(query, bitable_cands, token, query_type)))

                chunks: List[UnifiedChunk] = []
                if tasks:
                    results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)
                    for (label, _), r in zip(tasks, results):
                        if isinstance(r, Exception):
                            logger.warning("feishu_retrieve %s reader raised: %s", label, r)
                            continue
                        chunks.extend(r)
                        if table_pool is not None:
                            cands = sheet_cands if label == "sheet" else bitable_cands
                            for c in cands:
                                tok = c.get("id", "")
                                matching = [ch for ch in r if ch.source_name == c.get("title", "")]
                                await table_pool.store_result(tok, matching or [])

                if table_pool is not None:
                    # 对本 sub_query 没亲自读但已被 pool 管理（其他 sub_query
                    # 已读完 or 正在读）的 token，等/取缓存。
                    self_read_toks = {cc.get("id", "") for cc in sheet_cands + bitable_cands}
                    for c in table_candidates:
                        tok = c.get("id", "")
                        if (tok and tok not in self_read_toks
                                and table_pool.has_result_or_pending(tok)):
                            cached = await table_pool.get_cached(tok)
                            chunks.extend(cached)

                return chunks
            except Exception as e:
                logger.warning("feishu_retrieve table pipeline failed: %s", e)
                return []

        table_read_task = asyncio.create_task(_do_table_search_and_read())

    # DocPool 读取（与 table_read_task 并行）
    if doc_candidates:
        if not _preflight_check(query, doc_candidates, query_type):
            logger.info("feishu_retrieve: preflight_check SKIP, returning early")
        elif doc_pool is not None:
            try:
                selected_ids = await doc_pool.submit_and_read(doc_candidates, query)
                # doc_pool_timeout 从 SourceAgent 透传；None 时走 DocPool 默认
                ws_kwargs = {}
                if doc_pool_timeout is not None:
                    ws_kwargs["total_timeout"] = doc_pool_timeout
                doc_chunks = await doc_pool.wait_and_score(
                    selected_ids, query, **ws_kwargs,
                )
                logger.info(
                    "feishu_retrieve [pool]: query=%.30r → %d doc chunks (timeout=%s)",
                    query, len(doc_chunks),
                    f"{doc_pool_timeout:.1f}s" if doc_pool_timeout else "default",
                )
            except Exception as e:
                logger.warning("feishu_retrieve doc_pool failed: %s", e)
        else:
            doc_chunks = await _read_docx(query, doc_candidates, token, max_candidates, query_type)

    # 收割 table 结果（大概率已完成，因为与 DocPool 并行）
    table_chunks: List[UnifiedChunk] = []
    if table_read_task is not None:
        table_chunks = await table_read_task

    all_chunks = doc_chunks + table_chunks
    logger.info(
        "feishu_retrieve: total %d chunks (doc=%d table=%d) query_type=%s",
        len(all_chunks), len(doc_chunks), len(table_chunks), query_type,
    )
    return all_chunks
