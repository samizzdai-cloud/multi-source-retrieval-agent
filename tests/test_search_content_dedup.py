"""验证 MCP ∩ Wiki 同名文档是否内容相同

对每个 query，找出 MCP 和 Wiki 搜索结果中 title 重叠的文档对，
然后用 Block API 分别拉取两个 token 的前 N 个 block 文本，对比相似度。

结论：如果同名文档的 block 文本高度相似 → 确认是同一文档的不同视图 → 需要去重
"""

import json
import os
import sys
import requests
from collections import defaultdict
from typing import Dict, Any, List, Tuple
from dotenv import load_dotenv

load_dotenv()

FEISHU_MCP_ENDPOINT = os.getenv("FEISHU_MCP_ENDPOINT", "https://open.feishu.cn/mcp")
FEISHU_WIKI_SEARCH_URL = "https://open.feishu.cn/open-apis/wiki/v2/nodes/search"
FEISHU_WIKI_GET_NODE_URL = "https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node"
FEISHU_UAT = os.getenv("FEISHU_UAT", "")

WIKI_OBJ_TYPE_MAP = {1: "DOC", 2: "SHEET", 3: "BITABLE", 4: "MINDNOTE", 7: "WIKI", 8: "DOCX", 11: "SLIDES"}


# ── Search helpers (from test_search_overlap.py) ──

def search_mcp(query: str, count: int = 20) -> List[Dict]:
    headers = {
        "Content-Type": "application/json",
        "X-Lark-MCP-UAT": FEISHU_UAT,
        "X-Lark-MCP-Allowed-Tools": "search-doc",
    }
    payload = {
        "jsonrpc": "2.0", "method": "tools/call", "id": 1,
        "params": {"name": "search-doc", "arguments": {"query": query, "page": {"size": count}}},
    }
    try:
        resp = requests.post(FEISHU_MCP_ENDPOINT, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        content = resp.json().get("result", {}).get("content", [])
        raw = content[0].get("text", "") if content else ""
        inner = json.loads(raw) if raw else {}
        if inner.get("code") != 0:
            return []
        return [{"id": item.get("id", ""), "title": item.get("title", ""), "url": item.get("url", ""),
                 "doc_type": "DOCX", "_source": "mcp"} for item in inner.get("data", {}).get("items", [])]
    except Exception as e:
        print(f"  [MCP] Error: {e}")
        return []


def search_wiki(query: str, page_size: int = 20) -> List[Dict]:
    headers = {"Authorization": f"Bearer {FEISHU_UAT}", "Content-Type": "application/json; charset=utf-8"}
    try:
        resp = requests.post(FEISHU_WIKI_SEARCH_URL, headers=headers,
                             params={"page_size": page_size}, json={"query": query}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            return []
        results = []
        for item in data.get("data", {}).get("items", []):
            obj_type = item.get("obj_type", 0)
            results.append({
                "id": item.get("obj_token", ""),
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "doc_type": WIKI_OBJ_TYPE_MAP.get(obj_type, f"UNKNOWN_{obj_type}"),
                "node_id": item.get("node_id", ""),
                "obj_type_raw": obj_type,
                "_source": "wiki",
            })
        return results
    except Exception as e:
        print(f"  [Wiki] Error: {e}")
        return []


# ── Wiki node → real obj_token resolution ──

def resolve_wiki_node(node_id: str) -> Dict:
    """Call wiki/v2/spaces/get_node to resolve node_id → real obj_token."""
    headers = {"Authorization": f"Bearer {FEISHU_UAT}"}
    try:
        resp = requests.get(FEISHU_WIKI_GET_NODE_URL, headers=headers,
                            params={"token": node_id}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            return {"error": data.get("msg", "unknown")}
        node = data.get("data", {}).get("node", {})
        return {
            "obj_token": node.get("obj_token", ""),
            "obj_type": node.get("obj_type", ""),
            "title": node.get("title", ""),
            "node_token": node.get("node_token", ""),
        }
    except Exception as e:
        return {"error": str(e)}


# ── Block API content fetching ──

def fetch_doc_blocks_text(doc_id: str, max_blocks: int = 20) -> str:
    """Fetch first N blocks of a docx document and return concatenated text."""
    url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_id}/blocks"
    headers = {"Authorization": f"Bearer {FEISHU_UAT}"}
    params = {"page_size": max_blocks, "document_revision_id": -1}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            return f"[ERROR: code={data.get('code')} msg={data.get('msg', '')}]"
        blocks = data.get("data", {}).get("items", [])
        texts = []
        for block in blocks:
            # Extract text from various rich_text attributes
            for attr in ["text", "heading1", "heading2", "heading3", "heading4",
                         "heading5", "heading6", "bullet", "ordered", "quote", "todo"]:
                obj = block.get(attr)
                if obj and isinstance(obj, dict):
                    elements = obj.get("elements", [])
                    for el in elements:
                        tr = el.get("text_run")
                        if tr and isinstance(tr, dict):
                            t = tr.get("content", "").strip()
                            if t:
                                texts.append(t)
        return "\n".join(texts)
    except Exception as e:
        return f"[ERROR: {e}]"


# ── Main comparison ──

def find_title_overlaps(mcp_items: List[Dict], wiki_items: List[Dict]) -> List[Tuple[Dict, Dict]]:
    """Find pairs with matching titles (MCP item, Wiki item)."""
    # Only consider wiki DOCX items (obj_type=8 or 1) for fair comparison
    wiki_by_title = {}
    for item in wiki_items:
        if item["doc_type"] in ("DOCX", "DOC"):
            wiki_by_title.setdefault(item["title"], []).append(item)

    pairs = []
    for mcp_item in mcp_items:
        title = mcp_item["title"]
        if title in wiki_by_title:
            for wiki_item in wiki_by_title[title]:
                pairs.append((mcp_item, wiki_item))
    return pairs


def compare_content(mcp_item: Dict, wiki_item: Dict) -> Dict:
    """Fetch and compare content of two docs with same title."""
    mcp_id = mcp_item["id"]
    wiki_obj_token = wiki_item["id"]
    wiki_node_id = wiki_item.get("node_id", "")

    # Step 1: Resolve wiki node to get real obj_token
    resolved_token = wiki_obj_token
    if wiki_node_id:
        node_info = resolve_wiki_node(wiki_node_id)
        if node_info.get("obj_token"):
            resolved_token = node_info["obj_token"]

    # Step 2: Check if resolved tokens are actually the same
    tokens_match = (mcp_id == resolved_token)

    # Step 3: Fetch block content for both
    mcp_text = fetch_doc_blocks_text(mcp_id)
    wiki_text = fetch_doc_blocks_text(resolved_token)

    # Step 4: Simple similarity check
    mcp_lines = set(mcp_text.strip().split("\n")) - {""}
    wiki_lines = set(wiki_text.strip().split("\n")) - {""}

    if not mcp_lines and not wiki_lines:
        similarity = 1.0
    elif not mcp_lines or not wiki_lines:
        similarity = 0.0
    else:
        intersection = mcp_lines & wiki_lines
        union = mcp_lines | wiki_lines
        similarity = len(intersection) / len(union) if union else 0.0

    return {
        "title": mcp_item["title"],
        "mcp_id": mcp_id,
        "wiki_obj_token": wiki_obj_token,
        "wiki_resolved_token": resolved_token,
        "tokens_match": tokens_match,
        "mcp_text_len": len(mcp_text),
        "wiki_text_len": len(wiki_text),
        "mcp_lines": len(mcp_lines),
        "wiki_lines": len(wiki_lines),
        "common_lines": len(mcp_lines & wiki_lines),
        "jaccard_similarity": round(similarity, 3),
        "mcp_text_preview": mcp_text[:200],
        "wiki_text_preview": wiki_text[:200],
    }


def run_query(query: str):
    print(f"\n{'='*70}")
    print(f"  Query: \"{query}\"")
    print(f"{'='*70}")

    mcp_items = search_mcp(query)
    wiki_items = search_wiki(query)
    print(f"  MCP: {len(mcp_items)} results, Wiki: {len(wiki_items)} results")

    pairs = find_title_overlaps(mcp_items, wiki_items)
    print(f"  Title overlaps (DOCX only): {len(pairs)} pairs\n")

    if not pairs:
        print("  No overlapping titles found.")
        return []

    results = []
    for i, (mcp_item, wiki_item) in enumerate(pairs[:5]):  # max 5 pairs per query
        print(f"  ── Pair {i+1}: \"{mcp_item['title']}\" ──")
        result = compare_content(mcp_item, wiki_item)
        results.append(result)

        print(f"    MCP token:           {result['mcp_id']}")
        print(f"    Wiki obj_token:      {result['wiki_obj_token']}")
        print(f"    Wiki resolved token: {result['wiki_resolved_token']}")
        print(f"    Tokens match:        {'✅ YES' if result['tokens_match'] else '❌ NO'}")
        print(f"    MCP text lines:      {result['mcp_lines']}")
        print(f"    Wiki text lines:     {result['wiki_lines']}")
        print(f"    Common lines:        {result['common_lines']}")
        print(f"    Jaccard similarity:  {result['jaccard_similarity']}")
        if result['tokens_match']:
            print(f"    → SAME DOCUMENT (identical token after wiki resolution)")
        elif result['jaccard_similarity'] > 0.8:
            print(f"    → LIKELY SAME CONTENT (high similarity)")
        elif result['jaccard_similarity'] > 0.5:
            print(f"    → PARTIAL OVERLAP")
        else:
            print(f"    → DIFFERENT CONTENT despite same title")
        print()

    return results


def main():
    if not FEISHU_UAT:
        print("ERROR: FEISHU_UAT not set")
        sys.exit(1)

    queries = ["数据", "周报", "项目"]
    if len(sys.argv) > 1:
        queries = sys.argv[1:]

    all_results = []
    for q in queries:
        results = run_query(q)
        all_results.extend(results)

    # Summary
    print(f"\n{'='*70}")
    print(f"  SUMMARY: {len(all_results)} pairs compared")
    print(f"{'='*70}")
    token_match_count = sum(1 for r in all_results if r["tokens_match"])
    high_sim_count = sum(1 for r in all_results if r["jaccard_similarity"] > 0.8)
    partial_count = sum(1 for r in all_results if 0.5 < r["jaccard_similarity"] <= 0.8)
    diff_count = sum(1 for r in all_results if r["jaccard_similarity"] <= 0.5)

    print(f"  Token match (same doc):    {token_match_count}")
    print(f"  High similarity (>0.8):    {high_sim_count}")
    print(f"  Partial overlap (0.5-0.8): {partial_count}")
    print(f"  Different content (≤0.5):  {diff_count}")

    if token_match_count + high_sim_count > len(all_results) * 0.7:
        print(f"\n  CONCLUSION: MCP ∩ Wiki overlapping titles are mostly SAME content → DEDUP recommended")
    else:
        print(f"\n  CONCLUSION: MCP ∩ Wiki overlapping titles have DIFFERENT content → keep both")


if __name__ == "__main__":
    main()
