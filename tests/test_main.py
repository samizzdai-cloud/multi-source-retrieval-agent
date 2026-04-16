import json
from feishu_read_tool.search import search_feishu_file
from tools.feishu_fetch import fetch_feishu_doc

# ==========================================
# 可定制化测试接口
# ==========================================
QUERY = "hypershell 2025销售情况"  # 搜索关键词
PAGE_SIZE = 1                       # 每页条数（最大 20）
MAX_PAGES = 3                       # 最多请求页数

if __name__ == "__main__":
    # ── Step 1: 搜索文档 ──────────────────────────────
    print(f"搜索关键词 : {QUERY}")
    print(f"每页条数   : {PAGE_SIZE}")
    print(f"最多页数   : {MAX_PAGES}")
    print("-" * 50)

    search_result = search_feishu_file(query=QUERY, page_size=PAGE_SIZE, max_pages=MAX_PAGES)
    print("【搜索结果】")
    print(json.dumps(search_result, indent=4, ensure_ascii=False))

    # ── Step 2: 取排名第 1 的文档 URL 调用 fetch_doc ──
    items = search_result.get("items", [])
    if not items:
        print("\n未找到任何文档，跳过 fetch_doc 测试。")
    else:
        top_url = items[0].get("url")
        top_title = items[0].get("title", "（无标题）")
        print(f"\n【fetch_doc 测试】排名第 1 的文档: {top_title}")
        print(f"URL: {top_url}")
        print("-" * 50)

        fetch_result = fetch_feishu_doc(doc_id=top_url)
        print("【fetch_doc 原始返回】")
        print(json.dumps(fetch_result, indent=4, ensure_ascii=False))
