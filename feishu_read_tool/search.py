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
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

load_dotenv()

FEISHU_MCP_ENDPOINT = os.getenv("FEISHU_MCP_ENDPOINT", "https://open.feishu.cn/mcp")
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

# Wiki obj_type → doc_type 映射（仅保留支持的类型）
_WIKI_OBJ_TYPE_MAP = {
    1: "DOC",
    2: "SHEET",
    3: "BITABLE",
    8: "DOCX",
}
_WIKI_DOC_TYPES = {"DOC", "DOCX"}      # 文档类
_WIKI_TABLE_TYPES = {"SHEET", "BITABLE"}  # 表格类


def search_feishu_file(
    query: str,
    page_size: int = 20,
    max_pages: int = 1,
    page_token: Optional[str] = None,
    owners: Optional[List[str]] = None,
    sort_rule: Optional[str] = None,
    create_time: Optional[str] = None,
    create_time_relative: Optional[str] = None,
    open_time: Optional[str] = None,
    open_time_relative: Optional[str] = None,
    uat: str = "",
) -> Dict[str, Any]:
    """
    搜索当前用户可见的飞书云文档（doc / docx）。

    ── INPUT SCHEMA ────────────────────────────────────────────────────────────
    必填：
      query (str): 搜索关键词。可为空字符串，空搜时按最近浏览时间返回。

    分页（选填）：
      page_size (int): 每页条数，默认 20，最大 20。
      max_pages (int): 最多自动翻页次数，默认 1（只取第一页）。
      page_token (str): 起始翻页游标，通常不传（从第一页开始）。

    过滤（选填）：
      owners (list[str]): 文档创建者 open_id 列表。
      sort_rule (str): 排序方式（"OPEN_TIME"/"EDIT_TIME"/"EDIT_TIME_ASC"/"CREATE_TIME"）
      create_time (str): 创建时间绝对范围，格式 "[YYYY-MM-DD, YYYY-MM-DD]"
      create_time_relative (str): 创建时间相对范围，格式 "last_N_days"
      open_time (str): 最近打开时间绝对范围
      open_time_relative (str): 最近打开时间相对范围

    ── OUTPUT SCHEMA ───────────────────────────────────────────────────────────
    成功时返回：
    {
        "status": "success",
        "has_more": bool,
        "page_token": str,
        "total_fetched": int,
        "items": [{"id": str, "title": str, "doc_type": str, "url": str, ...}]
    }
    失败时返回：{"status": "error", "message": str}
    ────────────────────────────────────────────────────────────────────────────
    """
    token = uat or FEISHU_UAT
    if not token:
        return {"status": "error", "message": "未配置飞书 User Access Token (FEISHU_UAT)"}

    headers = {
        "Content-Type": "application/json",
        "X-Lark-MCP-UAT": token,
        "X-Lark-MCP-Allowed-Tools": "search-doc",
    }

    filters: Dict[str, Any] = {}
    if owners:
        filters["owners"] = owners
    if sort_rule:
        filters["sort_rule"] = sort_rule
    if create_time:
        filters["create_time"] = create_time
    if create_time_relative:
        filters["create_time_relative"] = create_time_relative
    if open_time:
        filters["open_time"] = open_time
    if open_time_relative:
        filters["open_time_relative"] = open_time_relative

    all_items: list = []
    current_token = page_token
    has_more = False
    last_token = None

    for _ in range(max_pages):
        page: Dict[str, Any] = {"size": page_size}
        if current_token is not None:
            page["page_token"] = current_token

        arguments: Dict[str, Any] = {"query": query, "page": page}
        if filters:
            arguments["filters"] = filters

        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {"name": "search-doc", "arguments": arguments},
        }

        try:
            response = requests.post(FEISHU_MCP_ENDPOINT, headers=headers, json=payload, timeout=10)
            response.raise_for_status()
            result_data = response.json()

            if "error" in result_data:
                return {"status": "error", "message": result_data["error"]}

            content = result_data.get("result", {}).get("content", [])
            if not content:
                return {"status": "error", "message": "响应体中 content 为空"}

            raw_text = content[0].get("text", "") if content else ""
            if not raw_text:
                return {"status": "error", "message": "MCP响应content[0].text为空"}
            try:
                inner = json.loads(raw_text)
            except json.JSONDecodeError as e:
                return {"status": "error", "message": f"MCP响应JSON解析失败: {e} | raw={raw_text[:200]}"}
            if inner.get("code") != 0:
                return {"status": "error", "message": inner.get("msg", "未知错误")}

            data = inner.get("data", {})
            all_items.extend(data.get("items", []))
            has_more = data.get("has_more", False)
            last_token = data.get("page_token")

            if not has_more:
                break
            current_token = last_token

        except requests.exceptions.RequestException as e:
            return {"status": "error", "message": f"请求飞书 MCP 服务失败: {str(e)}"}

    return {
        "status": "success",
        "has_more": has_more,
        "page_token": last_token,
        "total_fetched": len(all_items),
        "items": all_items,
    }

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
) -> List[Dict[str, Any]]:
    """调用 /suite/docs-api/search/object 搜索 sheet/bitable/slides/file 类型。

    返回标准化 item 列表（与 MCP items 格式对齐），失败时返回 []。
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    body = {
        "search_key": query,
        "count": min(count, 50),
        "offset": 0,
        "docs_types": ["sheet", "bitable"],
    }
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


# ── 混合搜索入口（对外公开，替换原 search_feishu_file 在需要全类型时使用）──────

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
            resp = requests.post(FEISHU_MCP_ENDPOINT, headers=headers, json=payload, timeout=15)
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
                return normalized
            except Exception as e:
                logger.warning("_search_wiki exception: %s", e)
                return []
    return []


# ── 按类型拆分的搜索接口（供 feishu_retrieve 分阶段调用）──────────────────

def search_feishu_docs(
    query: str,
    count: int = 20,
    uat: str = "",
    time_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """文档搜索：MCP + Wiki doc 类，按 title 去重（MCP 优先）。

    Returns:
        {"status": "success", "items": [...], "_debug": {...}}
    """
    token = uat or FEISHU_UAT
    if not token:
        return {"status": "error", "message": "未配置 FEISHU_UAT"}

    # MCP 搜索 (复用 search_feishu_hybrid 中的 _mcp_call 逻辑)
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
        mcp_headers = {
            "Content-Type": "application/json",
            "X-Lark-MCP-UAT": token,
            "X-Lark-MCP-Allowed-Tools": "search-doc",
        }
        try:
            resp = requests.post(FEISHU_MCP_ENDPOINT, headers=mcp_headers, json=payload, timeout=15)
            resp.raise_for_status()
            content = resp.json().get("result", {}).get("content", [])
            raw = content[0].get("text", "") if content else ""
            inner = json.loads(raw) if raw else {}
            if inner.get("code") != 0:
                return []
            items = inner.get("data", {}).get("items", [])
            for item in items:
                item.setdefault("_source", "mcp")
                item.setdefault("summary", item.get("summary", ""))
            return items
        except Exception:
            return []

    # 并行: MCP ∥ Wiki
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        fut_mcp = ex.submit(_mcp_call)
        fut_wiki = ex.submit(_search_wiki, query, count, token)
        mcp_items = fut_mcp.result()
        wiki_all = fut_wiki.result()

    # Wiki 中只取文档类 (DOC/DOCX)
    wiki_doc_items = [w for w in wiki_all if w.get("doc_type") in _WIKI_DOC_TYPES]

    # 推断租户域名
    global _inferred_tenant_domain
    if not _inferred_tenant_domain and mcp_items:
        domain = _extract_tenant_domain(mcp_items[0].get("url", ""))
        if domain:
            _inferred_tenant_domain = domain

    # 按 title 去重：MCP 优先（有 summary）
    mcp_titles = {item.get("title", "") for item in mcp_items if item.get("title")}
    deduped_wiki = [w for w in wiki_doc_items if w.get("title", "") not in mcp_titles]

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
        fut_direct = ex.submit(_search_direct_non_docx, query, count, token, tenant_domain)
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