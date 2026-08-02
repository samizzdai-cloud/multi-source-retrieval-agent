"""飞书云文档搜索工具 — 三路搜索方案

  MCP endpoint  : 搜索 docx（有 summary/url/timestamps 富元数据）
  Wiki Search   : 搜索知识库（doc/docx/sheet/bitable，按 obj_type 拆分）
  Direct REST   : 搜索 sheet/bitable（云文档空间）

三路并行（ThreadPoolExecutor），按类型合并：
  - 文档类(doc/docx): MCP + Wiki doc → 按 title 去重(MCP优先)
  - 表格类(sheet/bitable): Direct + Wiki table → 直接合并(无重叠)
"""

import json
import os
import requests
import concurrent.futures
import time
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

load_dotenv()

FEISHU_MCP_ENDPOINT = os.getenv("FEISHU_MCP_ENDPOINT", "https://mcp.feishu.cn/mcp")
FEISHU_DIRECT_SEARCH_URL = "https://open.feishu.cn/open-apis/suite/docs-api/search/object"
FEISHU_WIKI_SEARCH_URL = "https://open.feishu.cn/open-apis/wiki/v2/nodes/search"
FEISHU_UAT = os.getenv("FEISHU_UAT")
# 租户域名，用于从 Direct API token 构造 URL（从 MCP 结果自动推断，也可 env 覆盖）
FEISHU_TENANT_DOMAIN = os.getenv("FEISHU_TENANT_DOMAIN", "")

import threading
import logging
logger = logging.getLogger(__name__)

# ── Wiki search rate limiter ─────────────────────────────────────────────
# 飞书 wiki search 限流参数 (实测):
#   burst=9 (连续 9 次后触发限流), safe_concurrent=3, recovery≈30s
# 用 Semaphore 限并发 + 最小间隔限 QPS，避免触发 99991400
_WIKI_SEM = threading.Semaphore(3)     # max 3 concurrent wiki calls
_wiki_last_call = 0.0                  # timestamp of last call
_wiki_time_lock = threading.Lock()     # protects _wiki_last_call
_WIKI_MIN_INTERVAL = 0.35             # ≈2.8 QPS, safely below burst-9 threshold

# Wiki 短期缓存，用于合并相同 query 的并发调用（如 Phase A 和 Phase B 的同时调用）
_wiki_cache = {}
_wiki_cache_lock = threading.Lock()
_WIKI_CACHE_TTL = 2.0  # seconds

# ``search-doc`` is invoked by primary sub-queries and by zero-result variant
# fallbacks.  They must share one process-wide in-flight budget: a fallback is
# ordinary work, not a reason to burst past the primary request limit.  This
# is deliberately a concurrency cap, not a QPS claim; the UAT pressure probe
# established burst capacity but not a tenant-wide sustained-rate contract.
MCP_SEARCH_MAX_CONCURRENCY = max(
    1, int(os.getenv("FEISHU_MCP_SEARCH_MAX_CONCURRENCY", "6"))
)
_MCP_SEARCH_SEM = threading.BoundedSemaphore(MCP_SEARCH_MAX_CONCURRENCY)


def _post_mcp_search(payload: Dict[str, Any], headers: Dict[str, str]):
    """Send one MCP ``search-doc`` request under the shared in-flight cap.

    The semaphore is intentionally held only for the network call.  JSON
    parsing and caller-side normalization are local CPU work and must not keep
    another remote request from using a released slot.
    """
    queued_at = time.perf_counter()
    with _MCP_SEARCH_SEM:
        wait_ms = (time.perf_counter() - queued_at) * 1000.0
        logger.debug(
            "TIMING feishu.mcp_search_slot max_concurrency=%d wait_ms=%.1f",
            MCP_SEARCH_MAX_CONCURRENCY, wait_ms,
        )
        return requests.post(
            FEISHU_MCP_ENDPOINT, headers=headers, json=payload, timeout=15,
        )

# Wiki obj_type → doc_type 映射（仅保留支持的类型）
_WIKI_OBJ_TYPE_MAP = {
    1: "DOC",
    2: "SHEET",
    3: "BITABLE",
    8: "DOCX",
}
_WIKI_DOC_TYPES = {"DOC", "DOCX"}      # 文档类
_WIKI_TABLE_TYPES = {"SHEET", "BITABLE"}  # 表格类


# ── 租户域名推断 ────────────────────────────────────────────────────────────

_inferred_tenant_domain: str = ""


def _extract_tenant_domain(url: str) -> str:
    """从飞书文档 URL 提取租户域名，如 'fastbase.feishu.cn'。"""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.netloc and "feishu.cn" in parsed.netloc:
            return parsed.netloc
    except Exception:
        pass
    return ""


def _build_url_from_token(token: str, docs_type: str, tenant_domain: str) -> str:
    """从 Direct API 的 token + type 构造飞书文档 URL。"""
    if not tenant_domain:
        return ""
    _TYPE_PATH = {
        "docx": "docx", "doc": "docx",
        "sheet": "sheets",
        "bitable": "base",
        "slides": "slides",
        "mindnote": "mindnotes",
        "file": "file",
    }
    path = _TYPE_PATH.get(docs_type.lower(), "docx")
    return f"https://{tenant_domain}/{path}/{token}"


# ── Direct REST API 搜索（非 docx 类型）──────────────────────────────────────

def _search_direct_non_docx(
    query: str,
    count: int = 10,
    token: str = "",
    tenant_domain: str = "",
    owner_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """调用 /suite/docs-api/search/object 搜索 sheet/bitable/slides/file 类型。

    返回标准化 item 列表（与 MCP items 格式对齐），失败时返回 []。
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    body = {
        "search_key": query or "",
        "count": min(count, 50),
        "offset": 0,
        "docs_types": ["sheet", "bitable"],
    }
    if owner_ids:
        body["owner_ids"] = owner_ids
    try:
        resp = requests.post(FEISHU_DIRECT_SEARCH_URL, headers=headers, json=body, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            return []
        entities = data.get("data", {}).get("docs_entities", [])
        normalized = []
        for e in entities:
            docs_token = e.get("docs_token", "")
            docs_type = e.get("docs_type", "")
            url = _build_url_from_token(docs_token, docs_type, tenant_domain)
            normalized.append({
                "id": docs_token,
                "title": e.get("title", ""),
                "doc_type": docs_type.upper(),
                "url": url,
                "summary": "",          # Direct API 不提供 summary
                "owner_name": "",
                "_source": "direct",
            })
        return normalized
    except Exception:
        return []


# ── 混合搜索入口（对外公开，全类型搜索）──────

def search_feishu_hybrid(
    query: str,
    count: int = 10,
    uat: str = "",
    time_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """混合搜索：MCP（docx 富元数据）+ Direct（sheet/bitable/slides），并行执行。

    Args:
        query:       搜索关键词
        count:       每路最多返回条数（各自最多 count 条，合并后去重）
        uat:         用户 UAT，优先参数值，fallback 到 env
        time_filter: 时间过滤（仅对 MCP 生效，格式 "last_N_days"）

    Returns:
        {"status": "success", "total_fetched": int, "items": [...]}
    """
    token = uat or FEISHU_UAT
    if not token:
        return {"status": "error", "message": "未配置飞书 User Access Token (FEISHU_UAT)"}

    # ── MCP 路（docx）
    def _mcp_call():
        filters: Dict[str, Any] = {}
        if time_filter:
            filters["open_time_relative"] = time_filter
        page: Dict[str, Any] = {"size": count}
        arguments: Dict[str, Any] = {"query": query, "page": page}
        if filters:
            arguments["filters"] = filters
        payload = {
            "jsonrpc": "2.0", "method": "tools/call", "id": 1,
            "params": {"name": "search-doc", "arguments": arguments},
        }
        headers = {
            "Content-Type": "application/json",
            "X-Lark-MCP-UAT": token,
            "X-Lark-MCP-Allowed-Tools": "search-doc",
        }
        try:
            resp = _post_mcp_search(payload, headers)
            resp.raise_for_status()
            content = resp.json().get("result", {}).get("content", [])
            raw = content[0].get("text", "") if content else ""
            inner = json.loads(raw) if raw else {}
            if inner.get("code") != 0:
                return []
            items = inner.get("data", {}).get("items", [])
            # 标注来源，统一字段
            for item in items:
                item.setdefault("_source", "mcp")
                item.setdefault("summary", item.get("summary", ""))
            return items
        except Exception:
            return []

    # ── 并行执行两路
    global _inferred_tenant_domain
    tenant_domain = FEISHU_TENANT_DOMAIN or _inferred_tenant_domain

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        fut_mcp = ex.submit(_mcp_call)
        fut_direct = ex.submit(_search_direct_non_docx, query, count, token, tenant_domain)
        mcp_items = fut_mcp.result()
        direct_items = fut_direct.result()

    # 从 MCP 结果推断租户域名（一次性缓存）
    if not _inferred_tenant_domain and mcp_items:
        domain = _extract_tenant_domain(mcp_items[0].get("url", ""))
        if domain:
            _inferred_tenant_domain = domain
            # 补全 direct_items 的 URL（如果 tenant_domain 之前未知）
            if not tenant_domain:
                for item in direct_items:
                    if not item.get("url"):
                        item["url"] = _build_url_from_token(
                            item.get("id", ""), item.get("doc_type", ""), _inferred_tenant_domain
                        )

    # 合并去重（按 id/token）
    seen_ids = set()
    merged = []
    for item in mcp_items + direct_items:
        item_id = item.get("id", "") or item.get("docs_token", "")
        if item_id and item_id not in seen_ids:
            seen_ids.add(item_id)
            merged.append(item)

    return {
        "status": "success",
        "has_more": False,
        "page_token": None,
        "total_fetched": len(merged),
        "items": merged,
        "_debug": {
            "mcp_count": len(mcp_items),
            "direct_count": len(direct_items),
        },
    }


# ── Wiki 搜索 ──────────────────────────────────────────────────────────────

def _search_wiki(
    query: str,
    page_size: int = 20,
    token: str = "",
) -> List[Dict[str, Any]]:
    """搜索知识库（所有知识空间），返回标准化 item 列表。

    Wiki 搜索返回 obj_type 整数，需映射为 doc_type 字符串。
    仅保留支持的类型（DOC/DOCX/SHEET/BITABLE），过滤 Mindnote/Slides 等。

    速率控制: Semaphore(3) 限并发 + 最小间隔 0.35s 限 QPS。
    限流 (code=99991400) 时做 1 次 2s 重试。
    """
    import time as _time
    global _wiki_last_call

    # 尝试从缓存获取（缓存时长2秒）
    cache_key = f"{query}::{token}"
    with _wiki_cache_lock:
        if cache_key in _wiki_cache:
            cache_time, cache_data = _wiki_cache[cache_key]
            if _time.time() - cache_time < _WIKI_CACHE_TTL:
                return list(cache_data)  # 返回副本以防外部修改
            else:
                del _wiki_cache[cache_key]

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    with _WIKI_SEM:  # max 3 concurrent wiki calls
        # Enforce minimum interval between calls
        with _wiki_time_lock:
            now = _time.time()
            wait = _WIKI_MIN_INTERVAL - (now - _wiki_last_call)
            if wait > 0:
                _time.sleep(wait)
            _wiki_last_call = _time.time()

        for attempt in range(2):  # at most 1 retry
            try:
                resp = requests.post(
                    FEISHU_WIKI_SEARCH_URL,
                    headers=headers,
                    params={"page_size": min(page_size, 50)},
                    json={"query": query},
                    timeout=15,
                )
                # Feishu rate limit: HTTP 400 + code 99991400
                if resp.status_code == 400:
                    try:
                        err_body = resp.json()
                    except Exception:
                        err_body = {}
                    if err_body.get("code") == 99991400:
                        if attempt == 0:
                            _time.sleep(2)
                            continue
                        return []
                    resp.raise_for_status()
                resp.raise_for_status()
                data = resp.json()
                if data.get("code") != 0:
                    logger.warning("_search_wiki error: code=%s msg=%s", data.get("code"), data.get("msg"))
                    return []
                items = data.get("data", {}).get("items", [])
                normalized = []
                for item in items:
                    obj_type = item.get("obj_type", 0)
                    doc_type = _WIKI_OBJ_TYPE_MAP.get(obj_type)
                    if not doc_type:
                        continue
                    normalized.append({
                        "id": item.get("obj_token", ""),
                        "title": item.get("title", ""),
                        "doc_type": doc_type,
                        "url": item.get("url", ""),
                        "summary": "",
                        "node_id": item.get("node_id", ""),
                        "space_id": item.get("space_id", ""),
                        "_source": "wiki",
                    })
                
                # 存入缓存
                with _wiki_cache_lock:
                    _wiki_cache[cache_key] = (_time.time(), list(normalized))
                    
                return normalized
            except Exception as e:
                logger.warning("_search_wiki exception: %s", e)
                return []
    return []


# ── 按类型拆分的搜索接口（供 feishu_retrieve 分阶段调用）──────────────────

def search_feishu_mcp_pure(
    query: str,
    count: int = 10,
    uat: str = "",
    time_filter: Optional[str] = None,
    owner_ids: Optional[List[str]] = None,
    sort_rule: Optional[str] = None,
) -> Dict[str, Any]:
    """纯 MCP 搜索接口（用于 personal_recent 通道极速拉取）。"""
    token = uat or FEISHU_UAT
    if not token:
        return {"status": "error", "message": "未配置 FEISHU_UAT"}

    filters: Dict[str, Any] = {}
    if time_filter:
        filters["open_time_relative"] = time_filter
    if owner_ids:
        filters["owners"] = owner_ids
    if sort_rule:
        filters["sort_rule"] = sort_rule

    page: Dict[str, Any] = {"size": count}
    arguments: Dict[str, Any] = {"query": query or "", "page": page}
    if filters:
        arguments["filters"] = filters
        
    payload = {
        "jsonrpc": "2.0", "method": "tools/call", "id": 1,
        "params": {"name": "search-doc", "arguments": arguments},
    }
    mcp_headers = {
        "Content-Type": "application/json",
        "X-Lark-MCP-UAT": token,
        "X-Lark-MCP-Allowed-Tools": "search-doc",
    }
    try:
        resp = _post_mcp_search(payload, mcp_headers)
        resp.raise_for_status()
        content = resp.json().get("result", {}).get("content", [])
        raw = content[0].get("text", "") if content else ""
        inner = json.loads(raw) if raw else {}
        if inner.get("code") != 0:
            return {"status": "success", "items": []}
        items = inner.get("data", {}).get("items", [])
        for item in items:
            item.setdefault("_source", "mcp")
            item.setdefault("summary", item.get("summary", ""))
        return {"status": "success", "items": items}
    except Exception as e:
        logger.warning("_mcp_call pure failed: %s", e)
        return {"status": "success", "items": []}


def search_feishu_docs(
    query: str,
    count: int = 20,
    uat: str = "",
    time_filter: Optional[str] = None,
    owner_ids: Optional[List[str]] = None,
    skip_mcp: bool = False,
) -> Dict[str, Any]:
    """文档搜索：MCP + Wiki doc 类，按 title 去重（MCP 优先）。

    skip_mcp=True 时只查 Wiki(用于 dual-path Path B,避免与 Path A 的 MCP 重复)。

    Returns:
        {"status": "success", "items": [...], "_debug": {...}}
    """
    token = uat or FEISHU_UAT
    if not token:
        return {"status": "error", "message": "未配置 FEISHU_UAT"}

    if skip_mcp:
        # Path B 专用: 只查 Wiki,避免和 Path A 的 MCP 重复调用
        wiki_all = _search_wiki(query, count, token)
        wiki_doc_items = [w for w in wiki_all if w.get("doc_type") in _WIKI_DOC_TYPES]
        return {
            "status": "success",
            "total_fetched": len(wiki_doc_items),
            "items": wiki_doc_items,
            "_debug": {"wiki_doc_count": len(wiki_doc_items), "mcp_skipped": True},
        }

    # 并行: MCP ∥ Wiki
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        fut_mcp = ex.submit(search_feishu_mcp_pure, query, count, token, time_filter, owner_ids)
        fut_wiki = ex.submit(_search_wiki, query, count, token)
        mcp_res = fut_mcp.result()
        mcp_items = mcp_res.get("items", []) if mcp_res else []
        wiki_all = fut_wiki.result()

    # Wiki 中只取文档类 (DOC/DOCX)
    wiki_doc_items = [w for w in wiki_all if w.get("doc_type") in _WIKI_DOC_TYPES]

    # 推断租户域名
    global _inferred_tenant_domain
    if not _inferred_tenant_domain and mcp_items:
        domain = _extract_tenant_domain(mcp_items[0].get("url", ""))
        if domain:
            _inferred_tenant_domain = domain

    # 按 wiki_node_id 去重：MCP 优先（有 summary）
    # MCP 的 URL 可能包含 /wiki/node_id，如果和 Wiki 结果的 node_id 相同，则说明是同一份知识库文档
    import re
    mcp_node_ids = set()
    for item in mcp_items:
        url = item.get("url", "")
        match = re.search(r'/wiki/([a-zA-Z0-9]+)', url)
        if match:
            mcp_node_ids.add(match.group(1))

    # 如果 Wiki 结果的 node_id 在 MCP 中已有，说明已经被 MCP 召回（且带有 summary），Wiki 这边就剔除
    deduped_wiki = [w for w in wiki_doc_items if w.get("node_id", "") not in mcp_node_ids]

    merged = mcp_items + deduped_wiki
    return {
        "status": "success",
        "total_fetched": len(merged),
        "items": merged,
        "_debug": {
            "mcp_count": len(mcp_items),
            "wiki_doc_count": len(wiki_doc_items),
            "wiki_doc_deduped": len(deduped_wiki),
        },
    }


def search_feishu_tables(
    query: str,
    count: int = 20,
    uat: str = "",
    owner_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """表格搜索：Direct API (sheet+bitable) + Wiki table 类。

    Returns:
        {"status": "success", "items": [...], "_debug": {...}}
    """
    token = uat or FEISHU_UAT
    if not token:
        return {"status": "error", "message": "未配置 FEISHU_UAT"}

    global _inferred_tenant_domain
    tenant_domain = FEISHU_TENANT_DOMAIN or _inferred_tenant_domain

    # 并行: Direct ∥ Wiki
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        fut_direct = ex.submit(_search_direct_non_docx, query, count, token, tenant_domain, owner_ids)
        fut_wiki = ex.submit(_search_wiki, query, count, token)
        direct_items = fut_direct.result()
        wiki_all = fut_wiki.result()

    # Wiki 中只取表格类 (SHEET/BITABLE)
    wiki_table_items = [w for w in wiki_all if w.get("doc_type") in _WIKI_TABLE_TYPES]

    # 补全 direct URL（如果之前未推断域名，这里可能已有）
    if _inferred_tenant_domain and not tenant_domain:
        for item in direct_items:
            if not item.get("url"):
                item["url"] = _build_url_from_token(
                    item.get("id", ""), item.get("doc_type", ""), _inferred_tenant_domain
                )

    merged = direct_items + wiki_table_items
    return {
        "status": "success",
        "total_fetched": len(merged),
        "items": merged,
        "_debug": {
            "direct_count": len(direct_items),
            "wiki_table_count": len(wiki_table_items),
        },
    }
