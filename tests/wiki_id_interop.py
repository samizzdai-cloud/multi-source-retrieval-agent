"""
MCP / Direct API / Wiki 搜索结果 ID 互通性探索

目的：
- 检查 MCP search-doc 与 Wiki search(doc/docx) 是否能通过 token 映射到同一文档
- 检查 Direct API(sheet/bitable) 与 Wiki 表格项是否存在可用的 token 映射
- 给出可操作的去重/合并策略建议（title/url/token 维度）

运行：
  python tests/wiki_id_interop.py

要求：环境变量 FEISHU_UAT、可选 FEISHU_MCP_ENDPOINT、FEISHU_TENANT_DOMAIN
"""

import os
import re
import json
import time
from typing import Dict, Any, List, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

FEISHU_UAT = os.getenv("FEISHU_UAT", "")
FEISHU_MCP_ENDPOINT = os.getenv("FEISHU_MCP_ENDPOINT", "https://open.feishu.cn/mcp")
FEISHU_WIKI_SEARCH_URL = "https://open.feishu.cn/open-apis/wiki/v2/nodes/search"
FEISHU_DIRECT_SEARCH_URL = "https://open.feishu.cn/open-apis/suite/docs-api/search/object"

TEST_QUERIES = [
    "NUBIA Z70",
    "REDMAGIC",
    "Hypershell",
    "Nothing Phone",
    "竞品分析",
    "销量 数据",
]


def mcp_search_doc(query: str, count: int = 20) -> List[Dict[str, Any]]:
    headers = {
        "Content-Type": "application/json",
        "X-Lark-MCP-UAT": FEISHU_UAT,
        "X-Lark-MCP-Allowed-Tools": "search-doc",
    }
    page = {"size": count}
    payload = {
        "jsonrpc": "2.0", "method": "tools/call", "id": 1,
        "params": {"name": "search-doc", "arguments": {"query": query, "page": page}},
    }
    try:
        resp = requests.post(FEISHU_MCP_ENDPOINT, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        content = resp.json().get("result", {}).get("content", [])
        raw = content[0].get("text", "") if content else ""
        inner = json.loads(raw) if raw else {}
        items = inner.get("data", {}).get("items", [])
        for it in items:
            it.setdefault("_source", "mcp")
        return items
    except Exception as e:
        print("mcp_search_doc error:", e)
        return []


def wiki_search(query: str, page_size: int = 20) -> List[Dict[str, Any]]:
    headers = {
        "Authorization": f"Bearer {FEISHU_UAT}",
        "Content-Type": "application/json; charset=utf-8",
    }
    try:
        resp = requests.post(
            FEISHU_WIKI_SEARCH_URL, headers=headers,
            params={"page_size": min(page_size, 50)}, json={"query": query}, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            return []
        items = data.get("data", {}).get("items", [])
        out = []
        for it in items:
            obj_type = it.get("obj_type", 0)
            # 仅保留 DOC/DOCX/SHEET/BITABLE 四类
            if obj_type not in (1, 2, 3, 8):
                continue
            out.append({
                "title": it.get("title", ""),
                "url": it.get("url", ""),
                "obj_type": obj_type,
                "obj_token": it.get("obj_token", ""),
                "node_id": it.get("node_id", ""),
                "space_id": it.get("space_id", ""),
                "_source": "wiki",
            })
        return out
    except Exception as e:
        print("wiki_search error:", e)
        return []


def direct_search_tables(query: str, count: int = 20) -> List[Dict[str, Any]]:
    headers = {
        "Authorization": f"Bearer {FEISHU_UAT}",
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
        out = []
        for e in entities:
            out.append({
                "title": e.get("title", ""),
                "docs_type": e.get("docs_type", ""),
                "docs_token": e.get("docs_token", ""),
                "_source": "direct",
            })
        return out
    except Exception as e:
        print("direct_search_tables error:", e)
        return []


_DOCX_PATH = re.compile(r"/docx/([A-Za-z0-9]+)")


def extract_docx_token(url: str) -> str:
    m = _DOCX_PATH.search(url or "")
    return m.group(1) if m else ""


def analyze_overlap_docs(query: str) -> None:
    print(f"\n[DOCS] Query: {query}")
    mcp = mcp_search_doc(query)
    wiki = wiki_search(query)
    wiki_docs = [w for w in wiki if w.get("obj_type") in (1, 8)]  # DOC/DOCX

    # 以标题对齐
    by_title_mcp: Dict[str, List[Dict[str, Any]]] = {}
    for it in mcp:
        by_title_mcp.setdefault(it.get("title", ""), []).append(it)
    by_title_wiki: Dict[str, List[Dict[str, Any]]] = {}
    for it in wiki_docs:
        by_title_wiki.setdefault(it.get("title", ""), []).append(it)

    titles_overlap = sorted(set(by_title_mcp) & set(by_title_wiki))
    print(f"MCP items: {len(mcp)}, Wiki(doc) items: {len(wiki_docs)}, Title overlap: {len(titles_overlap)}")

    for t in titles_overlap[:5]:
        m = by_title_mcp[t][0]
        w = by_title_wiki[t][0]
        m_token = extract_docx_token(m.get("url", ""))
        print(
            f" - '{t[:28]}'\n"
            f"   MCP.url={m.get('url','')}, docx_token={m_token}\n"
            f"   WIKI.url={w.get('url','')}, obj_token={w.get('obj_token')}, node_id={w.get('node_id')}"
        )


def analyze_overlap_tables(query: str) -> None:
    print(f"\n[TABLES] Query: {query}")
    direct = direct_search_tables(query)
    wiki = wiki_search(query)
    wiki_tables = [w for w in wiki if w.get("obj_type") in (2, 3)]  # SHEET/BITABLE

    by_title_direct: Dict[str, List[Dict[str, Any]]] = {}
    for it in direct:
        by_title_direct.setdefault(it.get("title", ""), []).append(it)
    by_title_wiki: Dict[str, List[Dict[str, Any]]] = {}
    for it in wiki_tables:
        by_title_wiki.setdefault(it.get("title", ""), []).append(it)

    titles_overlap = sorted(set(by_title_direct) & set(by_title_wiki))
    print(f"Direct items: {len(direct)}, Wiki(table) items: {len(wiki_tables)}, Title overlap: {len(titles_overlap)}")
    for t in titles_overlap[:5]:
        d = by_title_direct[t][0]
        w = by_title_wiki[t][0]
        print(
            f" - '{t[:28]}'\n"
            f"   DIRECT.docs_type={d.get('docs_type')} token={d.get('docs_token')}\n"
            f"   WIKI.obj_type={w.get('obj_type')} obj_token={w.get('obj_token')} node_id={w.get('node_id')}"
        )


def main():
    if not FEISHU_UAT:
        print("ERROR: FEISHU_UAT not set")
        return

    for q in TEST_QUERIES:
        analyze_overlap_docs(q)
        time.sleep(0.5)
        analyze_overlap_tables(q)
        # 轻微间隔，避免过度撞 wiki 限流
        time.sleep(1.0)


if __name__ == "__main__":
    main()
