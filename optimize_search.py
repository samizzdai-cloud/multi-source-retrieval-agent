"""optimize_search.py — Feishu 搜索 Query 策略对比实验（Doc 搜索）

对比 5 种 query 策略 × 3 条搜索路径的检索效果。
目标：找到 "什么 query 适合什么路径" 的最优组合。

运行：python optimize_search.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

# ── 环境加载 ──────────────────────────────────────────────────────
load_dotenv(Path(__file__).parent / ".env", override=False)
load_dotenv(Path.home() / ".env", override=False)

UAT = os.getenv("FEISHU_UAT", "")
MCP_URL = os.getenv("FEISHU_MCP_ENDPOINT", "https://mcp.feishu.cn/mcp")
DIRECT_URL = "https://open.feishu.cn/open-apis/suite/docs-api/search/object"
WIKI_URL = "https://open.feishu.cn/open-apis/wiki/v2/nodes/search"

if not UAT:
    print("❌ FEISHU_UAT 未设置")
    sys.exit(1)

# ── User Profile ───────────────────────────────────────────────────
COUNT = 5  # 每次请求固定返回 5 条


# ══════════════════════════════════════════════════════════════════
# Search Paths
# ══════════════════════════════════════════════════════════════════

def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def search_mcp(query: str) -> dict:
    """MCP search-doc"""
    payload = {
        "jsonrpc": "2.0", "method": "tools/call", "id": 1,
        "params": {"name": "search-doc", "arguments": {"query": query, "page": {"size": COUNT}}},
    }
    headers = {"Content-Type": "application/json", "X-Lark-MCP-UAT": UAT,
               "X-Lark-MCP-Allowed-Tools": "search-doc"}
    t0 = time.perf_counter()
    try:
        r = requests.post(MCP_URL, headers=headers, json=payload, timeout=20)
        ms = _elapsed_ms(t0)
        content = r.json().get("result", {}).get("content", [])
        raw = content[0].get("text", "") if content else ""
        inner = json.loads(raw) if raw else {}
        items = inner.get("data", {}).get("items", []) if inner.get("code") == 0 else []
        ids = [(it.get("id") or it.get("document_id", ""), it.get("title", "")[:50]) for it in items[:COUNT]]
        return {"ok": True, "n": len(items), "ms": ms, "ids": ids, "path": "mcp"}
    except Exception as e:
        return {"ok": False, "n": 0, "ms": _elapsed_ms(t0), "ids": [], "path": "mcp", "error": str(e)[:100]}


def search_direct_doc(query: str) -> dict:
    """Direct API — doc/docx 搜索"""
    headers = {"Authorization": f"Bearer {UAT}", "Content-Type": "application/json; charset=utf-8"}
    body = {"search_key": query, "count": COUNT, "offset": 0, "docs_types": ["doc", "docx"]}
    t0 = time.perf_counter()
    try:
        r = requests.post(DIRECT_URL, headers=headers, json=body, timeout=20)
        ms = _elapsed_ms(t0)
        d = r.json()
        items = d.get("data", {}).get("docs_entities", []) if d.get("code") == 0 else []
        ids = [(it.get("docs_token", ""), it.get("title", "")[:50]) for it in items[:COUNT]]
        return {"ok": True, "n": len(items), "ms": ms, "ids": ids, "path": "direct"}
    except Exception as e:
        return {"ok": False, "n": 0, "ms": _elapsed_ms(t0), "ids": [], "path": "direct", "error": str(e)[:100]}


def search_wiki(query: str) -> dict:
    """Wiki 搜索"""
    headers = {"Authorization": f"Bearer {UAT}", "Content-Type": "application/json; charset=utf-8"}
    t0 = time.perf_counter()
    try:
        r = requests.post(WIKI_URL, headers=headers,
                          params={"page_size": COUNT}, json={"query": query}, timeout=20)
        ms = _elapsed_ms(t0)
        d = r.json()
        items = d.get("data", {}).get("items", []) if d.get("code") == 0 else []
        ids = [(it.get("obj_token", ""), it.get("title", "")[:50]) for it in items[:COUNT]]
        return {"ok": True, "n": len(items), "ms": ms, "ids": ids, "path": "wiki"}
    except Exception as e:
        return {"ok": False, "n": 0, "ms": _elapsed_ms(t0), "ids": [], "path": "wiki", "error": str(e)[:100]}


# ══════════════════════════════════════════════════════════════════
# Query Strategies
# ══════════════════════════════════════════════════════════════════

def _get_brand_alias(brand: str) -> str:
    """取品牌首选别名（用于 query 中替换）"""
    return brand


def _get_domain_terms(domain: str, lang: str = "zh") -> List[str]:
    """取领域术语"""
    return []


def _get_doc_terms(doc_key: str) -> dict:
    return {}


def strategy_a_raw(focus_text: str) -> str:
    """A. Raw Focus — 直接用 focus 原文"""
    return focus_text.strip()


def strategy_b_planner(focus_text: str, brand: str = "", domain: str = "") -> str:
    """B. Planner-style — 品牌 + 动作词, 2-3 词, 5-15 字"""
    alias = _get_brand_alias(brand) if brand else ""
    terms = _get_domain_terms(domain) if domain else []

    # 构建: brand + action_word
    parts = [alias] if alias else []
    # 从 focus 中提取关键词（取前 2 个有意义的词）
    import re
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", focus_text)
    meaningful = [t for t in tokens if t.lower() not in
                  {"的", "了", "是", "在", "和", "与", "及", "或", "如何", "什么", "哪些", "为什么", "分析", "情况", "表现", "数据", "报告"}]
    parts.extend(meaningful[:2] if len(parts) > 0 else meaningful[:3])
    query = " ".join(parts[:3]).strip()
    return query if 5 <= len(query) <= 15 else (query[:15] if len(query) > 15 else query)


def strategy_c_cooccurrence(focus_text: str, brand: str = "", domains: List[str] = None) -> str:
    """C. Co-occurrence — 从 focus 抽 2-3 维度锚点词 + 实体组合"""
    alias = _get_brand_alias(brand) if brand else ""
    domains = domains or []

    # 收集各维度术语
    all_terms = [alias] if alias else []
    seen = set(all_terms)
    for d in domains:
        for t in _get_domain_terms(d):
            if t.lower() not in seen:
                all_terms.append(t)
                seen.add(t.lower())
                if len(all_terms) >= 4:
                    break
        if len(all_terms) >= 4:
            break

    # 不足时从 focus 中补充
    if len(all_terms) < 3:
        import re
        tokens = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", focus_text)
        for t in tokens:
            if t.lower() not in seen and len(t) >= 2:
                all_terms.append(t)
                seen.add(t.lower())
                if len(all_terms) >= 3:
                    break

    return " ".join(all_terms[:4]).strip()


def strategy_d_profile(focus_text: str, brand: str = "", domains: List[str] = None,
                       doc_key: str = "") -> str:
    """D. Profile-augmented — 策略 C + user_profile 中的文档术语替换"""
    query = strategy_c_cooccurrence(focus_text, brand, domains)

    # 尝试用 profile 中的 unique_terms 替换或补充
    if doc_key:
        doc_terms = _get_doc_terms(doc_key)
        unique = doc_terms.get("unique_terms", [])
        title_signals = doc_terms.get("title_signals", [])

        # 添加 title signals（如果有匹配）
        for ts in title_signals:
            if ts.lower() not in query.lower():
                query = f"{query} {ts}".strip()
                break

    return query


def strategy_e_hybrid(focus_text: str, brand: str = "", domains: List[str] = None,
                      doc_key: str = "") -> str:
    """E. Optimized Hybrid — 策略 D + 文档标题信号 + section heading"""
    query = strategy_d_profile(focus_text, brand, domains, doc_key)

    if doc_key:
        doc_terms = _get_doc_terms(doc_key)
        # 追加 section heading 中与 focus 最相关的
        sections = doc_terms.get("section_headings", [])
        import re
        focus_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", focus_text.lower()))
        for sec in sections:
            sec_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", sec.lower()))
            if focus_tokens & sec_tokens:
                # 取 section 的第一个有意义词加入 query
                first_word = re.findall(r"[\w\u4e00-\u9fff]+", sec)
                if first_word and first_word[0].lower() not in query.lower():
                    query = f"{query} {first_word[0]}".strip()
                    break

    return query


# ══════════════════════════════════════════════════════════════════
# Test Cases
# ══════════════════════════════════════════════════════════════════

@dataclass
class TestCase:
    id: str
    focus: str
    target_doc_token: str
    target_doc_name: str
    query_type: str           # structured | concept
    brand: str = ""
    domains: List[str] = field(default_factory=list)
    doc_key: str = ""


TEST_CASES: List[TestCase] = [
    # ── Doc1: Nubia Weekly Review ─────────────────────────────
    TestCase(
        id="D1", focus="Nubia Z80 Ultra 新品上市时间线和关键节点",
        target_doc_token="R9NZdImoLog6wtxi8OVcBhvUnIQ",
        target_doc_name="Nubia Weekly Review",
        query_type="structured", brand="nubia",
        domains=["launch"], doc_key="nubia_weekly",
    ),
    TestCase(
        id="D2", focus="Nubia 墨西哥市场转化率下降的原因分析",
        target_doc_token="R9NZdImoLog6wtxi8OVcBhvUnIQ",
        target_doc_name="Nubia Weekly Review",
        query_type="concept", brand="nubia",
        domains=["traffic", "market_analysis"], doc_key="nubia_weekly",
    ),
    TestCase(
        id="D3", focus="Nubia Meta Ads 各国预算分配和 ROAS 表现",
        target_doc_token="R9NZdImoLog6wtxi8OVcBhvUnIQ",
        target_doc_name="Nubia Weekly Review",
        query_type="structured", brand="nubia",
        domains=["advertising", "sales"], doc_key="nubia_weekly",
    ),
    TestCase(
        id="D4", focus="Nubia KOL 合作进度和各机型发布率",
        target_doc_token="R9NZdImoLog6wtxi8OVcBhvUnIQ",
        target_doc_name="Nubia Weekly Review",
        query_type="structured", brand="nubia",
        domains=["kol_pr"], doc_key="nubia_weekly",
    ),
    TestCase(
        id="D5", focus="Nubia Google Ads 投放效果和创意表现",
        target_doc_token="R9NZdImoLog6wtxi8OVcBhvUnIQ",
        target_doc_name="Nubia Weekly Review",
        query_type="structured", brand="nubia",
        domains=["advertising"], doc_key="nubia_weekly",
    ),
    # ── Doc2: REDMAGIC BI Report ──────────────────────────────
    TestCase(
        id="D6", focus="REDMAGIC 11S Pro 欧洲库存不足和销售下降",
        target_doc_token="ZbIVdQkDtoDeGBxADjCcTTIonpd",
        target_doc_name="REDMAGIC BI Report",
        query_type="structured", brand="redmagic",
        domains=["inventory", "sales"], doc_key="redmagic_bi",
    ),
    TestCase(
        id="D7", focus="REDMAGIC 流量归因中 Unassigned 渠道异常增长问题",
        target_doc_token="ZbIVdQkDtoDeGBxADjCcTTIonpd",
        target_doc_name="REDMAGIC BI Report",
        query_type="concept", brand="redmagic",
        domains=["traffic", "market_analysis"], doc_key="redmagic_bi",
    ),
    TestCase(
        id="D8", focus="REDMAGIC Affiliate 渠道收入和活跃推广者数据",
        target_doc_token="ZbIVdQkDtoDeGBxADjCcTTIonpd",
        target_doc_name="REDMAGIC BI Report",
        query_type="structured", brand="redmagic",
        domains=["affiliate"], doc_key="redmagic_bi",
    ),
    TestCase(
        id="D9", focus="REDMAGIC Facebook 墨西哥极低 CPM 和高 CTR 表现",
        target_doc_token="ZbIVdQkDtoDeGBxADjCcTTIonpd",
        target_doc_name="REDMAGIC BI Report",
        query_type="structured", brand="redmagic",
        domains=["advertising", "traffic"], doc_key="redmagic_bi",
    ),
    TestCase(
        id="D10", focus="REDMAGIC 核心结论与本周建议行动",
        target_doc_token="ZbIVdQkDtoDeGBxADjCcTTIonpd",
        target_doc_name="REDMAGIC BI Report",
        query_type="concept", brand="redmagic",
        domains=["conclusion"], doc_key="redmagic_bi",
    ),
]


# ══════════════════════════════════════════════════════════════════
# Experiment Runner
# ══════════════════════════════════════════════════════════════════

STRATEGIES = {
    "A_raw": strategy_a_raw,
    "B_planner": strategy_b_planner,
    "C_cooccur": strategy_c_cooccurrence,
    "D_profile": strategy_d_profile,
    "E_hybrid": strategy_e_hybrid,
}

SEARCH_PATHS = {
    "mcp": search_mcp,
    "direct": search_direct_doc,
    "wiki": search_wiki,
}


def _call_strategy(sfn, args: dict) -> str:
    """调用策略函数，自动过滤不匹配的参数"""
    import inspect
    sig = inspect.signature(sfn)
    valid = {k: v for k, v in args.items() if k in sig.parameters}
    return sfn(**valid)


def check_hit(result: dict, target_token: str) -> Tuple[bool, int]:
    """检查目标 doc 是否在结果中，返回 (hit, rank)"""
    for rank, (tid, _title) in enumerate(result["ids"], start=1):
        if tid == target_token:
            return True, rank
    return False, 0


def run_experiment() -> Dict[str, Any]:
    """运行全矩阵实验"""
    print("=" * 72)
    print("Feishu Search Query Strategy Experiment")
    print(f"Test cases: {len(TEST_CASES)}  |  Strategies: {len(STRATEGIES)}  |  Paths: {len(SEARCH_PATHS)}")
    print(f"Total runs: {len(TEST_CASES) * len(STRATEGIES) * len(SEARCH_PATHS)}")
    print(f"COUNT per request: {COUNT}")
    print("=" * 72)

    results: Dict[str, list] = {}  # test_id → list of run records
    all_runs = []

    for tc in TEST_CASES:
        tc_results = []
        args = {
            "focus_text": tc.focus,
            "brand": tc.brand,
            "domains": tc.domains,
            "doc_key": tc.doc_key,
        }

        for sname, sfn in STRATEGIES.items():
            query = _call_strategy(sfn, args)

            for pname, pfn in SEARCH_PATHS.items():
                result = pfn(query)
                hit, rank = check_hit(result, tc.target_doc_token)

                run = {
                    "test_id": tc.id,
                    "focus": tc.focus[:60],
                    "strategy": sname,
                    "query": query,
                    "path": pname,
                    "hit": hit,
                    "rank": rank,
                    "n_results": result["n"],
                    "ms": result["ms"],
                    "ok": result["ok"],
                    "target_doc": tc.target_doc_name,
                    "query_type": tc.query_type,
                }
                tc_results.append(run)
                all_runs.append(run)

                icon = "✅" if hit else "❌"
                rank_str = f" rank={rank}" if hit else ""
                print(f"  {icon} {tc.id:4s} {sname:12s} {pname:7s} | n={result['n']} {result['ms']:4d}ms{rank_str} | {query[:55]}")

        results[tc.id] = tc_results

    return {
        "meta": {
            "timestamp": datetime.now().isoformat(),
            "count_per_request": COUNT,
            "n_cases": len(TEST_CASES),
            "n_strategies": len(STRATEGIES),
            "n_paths": len(SEARCH_PATHS),
            "total_runs": len(all_runs),
        },
        "runs": all_runs,
        "summary": _compute_summary(all_runs),
    }


def _compute_summary(runs: List[dict]) -> dict:
    """计算汇总指标"""
    # 按 strategy 汇总
    by_strategy: Dict[str, dict] = {}
    for r in runs:
        s = r["strategy"]
        if s not in by_strategy:
            by_strategy[s] = {"total": 0, "hits": 0, "ranks": [], "ms_list": []}
        by_strategy[s]["total"] += 1
        by_strategy[s]["ms_list"].append(r["ms"])
        if r["hit"]:
            by_strategy[s]["hits"] += 1
            by_strategy[s]["ranks"].append(r["rank"])

    for s, stats in by_strategy.items():
        stats["hit_rate"] = stats["hits"] / stats["total"] if stats["total"] else 0
        stats["mrr"] = sum(1.0 / r for r in stats["ranks"]) / stats["total"] if stats["total"] else 0
        stats["avg_ms"] = sum(stats["ms_list"]) // len(stats["ms_list"]) if stats["ms_list"] else 0

    # 按 path 汇总
    by_path: Dict[str, dict] = {}
    for r in runs:
        p = r["path"]
        if p not in by_path:
            by_path[p] = {"total": 0, "hits": 0, "ranks": [], "ms_list": []}
        by_path[p]["total"] += 1
        by_path[p]["ms_list"].append(r["ms"])
        if r["hit"]:
            by_path[p]["hits"] += 1
            by_path[p]["ranks"].append(r["rank"])

    for p, stats in by_path.items():
        stats["hit_rate"] = stats["hits"] / stats["total"] if stats["total"] else 0
        stats["mrr"] = sum(1.0 / r for r in stats["ranks"]) / stats["total"] if stats["total"] else 0
        stats["avg_ms"] = sum(stats["ms_list"]) // len(stats["ms_list"]) if stats["ms_list"] else 0

    # 按 strategy+path 组合汇总
    by_combo: Dict[str, dict] = {}
    for r in runs:
        key = f"{r['strategy']}+{r['path']}"
        if key not in by_combo:
            by_combo[key] = {"total": 0, "hits": 0, "ranks": []}
        by_combo[key]["total"] += 1
        if r["hit"]:
            by_combo[key]["hits"] += 1
            by_combo[key]["ranks"].append(r["rank"])

    for key, stats in by_combo.items():
        stats["hit_rate"] = stats["hits"] / stats["total"] if stats["total"] else 0

    # 按 test_id 汇总（每个 focus 的总命中率）
    by_test: Dict[str, dict] = {}
    for r in runs:
        tid = r["test_id"]
        if tid not in by_test:
            by_test[tid] = {"total": 0, "hits": 0, "focus": r["focus"]}
        by_test[tid]["total"] += 1
        if r["hit"]:
            by_test[tid]["hits"] += 1

    for tid, stats in by_test.items():
        stats["hit_rate"] = stats["hits"] / stats["total"] if stats["total"] else 0

    return {
        "by_strategy": by_strategy,
        "by_path": by_path,
        "by_combo": {k: v for k, v in sorted(by_combo.items(), key=lambda x: -x[1]["hit_rate"])},
        "by_test": dict(sorted(by_test.items(), key=lambda x: -x[1]["hit_rate"])),
    }


def print_report(summary: dict) -> None:
    """打印实验报告"""
    print("\n" + "=" * 72)
    print("  EXPERIMENT REPORT")
    print("=" * 72)

    print("\n── 按 Strategy 汇总 ──")
    print(f"  {'Strategy':12s} {'Hit%':>6s} {'MRR':>6s} {'Hits':>5s} {'Total':>5s} {'AvgMs':>6s}")
    print("  " + "-" * 48)
    for s, st in sorted(summary["by_strategy"].items(), key=lambda x: -x[1]["hit_rate"]):
        print(f"  {s:12s} {st['hit_rate']:5.0%}  {st['mrr']:5.3f}  {st['hits']:4d}  {st['total']:4d}  {st['avg_ms']:5d}ms")

    print("\n── 按 Search Path 汇总 ──")
    print(f"  {'Path':10s} {'Hit%':>6s} {'MRR':>6s} {'Hits':>5s} {'Total':>5s} {'AvgMs':>6s}")
    print("  " + "-" * 44)
    for p, st in sorted(summary["by_path"].items(), key=lambda x: -x[1]["hit_rate"]):
        print(f"  {p:10s} {st['hit_rate']:5.0%}  {st['mrr']:5.3f}  {st['hits']:4d}  {st['total']:4d}  {st['avg_ms']:5d}ms")

    print("\n── 最佳 Strategy+Path 组合 (Top 10) ──")
    combos = list(summary["by_combo"].items())[:10]
    for key, st in combos:
        print(f"  {key:25s} {st['hit_rate']:5.0%}  {st['hits']:2d}/{st['total']:2d}")

    print("\n── 按 Focus 汇总 (易→难) ──")
    for tid, st in summary["by_test"].items():
        bar = "█" * int(st["hit_rate"] * 20) + "░" * (20 - int(st["hit_rate"] * 20))
        print(f"  {tid:4s} [{bar}] {st['hit_rate']:4.0%}  {st['hits']:2d}/{st['total']:2d}  {st['focus'][:55]}")

    # 失败分析
    print("\n── 零命中 Focus (需优化) ──")
    zero_hit = [(tid, st) for tid, st in summary["by_test"].items() if st["hit_rate"] == 0]
    if zero_hit:
        for tid, st in zero_hit:
            print(f"  {tid}: {st['focus'][:60]}")
    else:
        print("  (无) 所有 focus 至少有一条策略+路径命中 ✅")


# ══════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════

def main():
    data = run_experiment()
    print_report(data["summary"])

    # 保存结果
    out_dir = Path(__file__).parent / "optimize_results"
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"run_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n完整结果已保存: {out_path}")


if __name__ == "__main__":
    main()
