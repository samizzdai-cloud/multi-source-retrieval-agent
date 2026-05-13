"""专攻 Bitable1 召回探测: 用所有合理的 query 形态直接调三个搜索 API,
看哪种 query 能找回 Haivivi内容运营计划 (S4swbS3G...)。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from feishu_read_tool.search import (
    _search_direct_non_docx, _search_wiki, search_feishu_mcp_pure,
)

UAT = os.getenv("FEISHU_UAT", "")
TENANT = "fastbase.feishu.cn"
TARGET = "S4swbS3G"            # Bitable1 doc_id
TARGET_TITLE = "Haivivi内容运营计划"


def probe(query: str, page_size: int = 30) -> dict:
    out = {"query": query}
    # MCP (docx only — 不应找到 bitable, 但 check)
    try:
        resp = search_feishu_mcp_pure(query=query, count=page_size, uat=UAT)
        items = resp.get("items", []) if isinstance(resp, dict) else []
        out["mcp"] = (len(items), next((i+1 for i,it in enumerate(items)
                       if TARGET in (it.get("url") or "")
                       or TARGET_TITLE in (it.get("title") or "")), None))
    except Exception as e:
        out["mcp"] = ("ERR", str(e)[:30])
    time.sleep(0.4)

    # Wiki
    try:
        items = _search_wiki(query=query, page_size=page_size, token=UAT)
        out["wiki"] = (len(items), next((i+1 for i,it in enumerate(items)
                       if TARGET in (it.get("url") or "")
                       or TARGET_TITLE in (it.get("title") or "")), None))
    except Exception as e:
        out["wiki"] = ("ERR", str(e)[:30])
    time.sleep(0.4)

    # Direct (sheet/bitable)
    try:
        items = _search_direct_non_docx(query=query, count=page_size, token=UAT, tenant_domain=TENANT)
        out["direct"] = (len(items), next((i+1 for i,it in enumerate(items)
                          if TARGET in (it.get("url") or "")
                          or TARGET_TITLE in (it.get("title") or "")), None))
        out["direct_titles"] = [it.get("title","")[:50] for it in items[:10]]
    except Exception as e:
        out["direct"] = ("ERR", str(e)[:30])
    return out


QUERIES = [
    # 基础尝试
    "Haivivi",
    "Haivivi 内容运营",
    "Haivivi 内容运营计划",
    "Haivivi内容运营计划",                # 无空格
    "Haivivi 运营",
    "haivivi",                          # 小写
    # 加 type 词
    "Haivivi 多维表格",
    "Haivivi 项目",
    "Haivivi 项目 进度",
    # 内部 item 词
    "Haivivi UGC",
    "Haivivi UGC 活动",
    "Haivivi EDM",
    "Haivivi 卜伟哲",                     # 负责人
    # 单词
    "Haivivi 计划",
    "内容运营计划",                       # 无品牌
    # 英文变体
    "Haivivi content",
    "Haivivi content operation",
    "Haivivi operation",
]


def main():
    print(f"\n{'='*100}")
    print(f"Bitable1 召回探测  target_url~'{TARGET}'  target_title~'{TARGET_TITLE}'")
    print(f"{'='*100}\n")
    print(f"{'query':45}  {'MCP':>15}  {'Wiki':>15}  {'Direct':>15}")
    print(f"{'-'*45}  {'-'*15}  {'-'*15}  {'-'*15}")
    for q in QUERIES:
        r = probe(q)
        def fmt(t):
            n, rk = t if isinstance(t, tuple) else (t, None)
            if isinstance(n, str): return f"ERR"
            return f"{'#'+str(rk) if rk else '—'}/{n}"
        print(f"{q:45}  {fmt(r['mcp']):>15}  {fmt(r['wiki']):>15}  {fmt(r['direct']):>15}")
        time.sleep(0.5)

    # 单独看 brand_only "Haivivi" Direct 的 top-10 标题
    print(f"\n--- Direct top-10 for 'Haivivi' (看为什么目标不在前 30) ---")
    r = probe("Haivivi", page_size=30)
    for i, t in enumerate(r.get("direct_titles", []), 1):
        marker = "✓" if TARGET_TITLE in t else " "
        print(f"  {marker} #{i:2d}  {t}")


if __name__ == "__main__":
    main()
