"""
飞书搜索 API 双路基准测试
对比:
  A) MCP endpoint  (当前生产方案)
  B) Direct REST   /suite/docs-api/search/object

指标:
  - 单请求延迟 (mean / p50 / p95)
  - 5 query 并发延迟
  - 结果质量 (返回条数, 标题样本, docs_type 分布)
  - 错误率

运行:
  cd /Users/javendai/Documents/workplace/Context_RAG_Agent
  python -m tests.bench_search_api
"""

import asyncio
import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv

load_dotenv()

UAT = os.getenv("FEISHU_UAT", "")
MCP_ENDPOINT = os.getenv("FEISHU_MCP_ENDPOINT", "https://mcp.feishu.cn/mcp")
DIRECT_URL = "https://open.feishu.cn/open-apis/suite/docs-api/search/object"

BENCH_QUERIES = [
    "nubia 店铺表现",
    "hypershell 流量",
    "广告投放 ROAS",
    "英国 UK sales",
    "渠道分析 DTC",
]

REPEAT = 3  # 每个 query 重复次数（单请求延迟测量）
CONCURRENCY = 5  # 并发数


# ─────────────────────────────────────────────────────────────────────────────
# 两种搜索实现
# ─────────────────────────────────────────────────────────────────────────────

def search_mcp(query: str, count: int = 10) -> Dict[str, Any]:
    """MCP JSON-RPC 搜索（当前生产实现）"""
    headers = {
        "Content-Type": "application/json",
        "X-Lark-MCP-UAT": UAT,
        "X-Lark-MCP-Allowed-Tools": "search-doc",
    }
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "id": 1,
        "params": {
            "name": "search-doc",
            "arguments": {
                "query": query,
                "page": {"size": count},
            },
        },
    }
    t0 = time.perf_counter()
    try:
        resp = requests.post(MCP_ENDPOINT, headers=headers, json=payload, timeout=15)
        elapsed = time.perf_counter() - t0
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            return {"ok": False, "error": str(data["error"]), "elapsed": elapsed, "items": []}

        content = data.get("result", {}).get("content", [])
        raw = content[0].get("text", "") if content else ""
        inner = json.loads(raw) if raw else {}

        if inner.get("code") != 0:
            return {"ok": False, "error": inner.get("msg", "?"), "elapsed": elapsed, "items": []}

        items = inner.get("data", {}).get("items", [])
        return {
            "ok": True,
            "elapsed": elapsed,
            "count": len(items),
            "items": [{"title": i.get("title", ""), "type": i.get("doc_type", i.get("type", ""))} for i in items],
            "has_more": inner.get("data", {}).get("has_more", False),
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "elapsed": time.perf_counter() - t0, "items": []}


def search_direct(query: str, count: int = 10) -> Dict[str, Any]:
    """Direct REST API 搜索（/suite/docs-api/search/object）"""
    headers = {
        "Authorization": f"Bearer {UAT}",
        "Content-Type": "application/json; charset=utf-8",
    }
    body = {
        "search_key": query,
        "count": min(count, 50),
        "offset": 0,
    }
    t0 = time.perf_counter()
    try:
        resp = requests.post(DIRECT_URL, headers=headers, json=body, timeout=15)
        elapsed = time.perf_counter() - t0
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            return {"ok": False, "error": data.get("msg", "?"), "elapsed": elapsed, "items": []}

        entities = data.get("data", {}).get("docs_entities", [])
        return {
            "ok": True,
            "elapsed": elapsed,
            "count": len(entities),
            "total": data.get("data", {}).get("total", 0),
            "items": [{"title": e.get("title", ""), "type": e.get("docs_type", "")} for e in entities],
            "has_more": data.get("data", {}).get("has_more", False),
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "elapsed": time.perf_counter() - t0, "items": []}


# ─────────────────────────────────────────────────────────────────────────────
# 基准测试逻辑
# ─────────────────────────────────────────────────────────────────────────────

def bench_single(label: str, fn, queries: List[str], repeat: int):
    """单请求延迟测试：每个 query 重复 repeat 次，串行。"""
    print(f"\n{'='*60}")
    print(f"[{label}] 单请求延迟测试 ({len(queries)} queries × {repeat} 次)")
    print(f"{'='*60}")

    all_latencies = []
    errors = 0

    for q in queries:
        latencies = []
        for r in range(repeat):
            result = fn(q)
            if result["ok"]:
                latencies.append(result["elapsed"])
                if r == 0:
                    types = {}
                    for item in result.get("items", []):
                        t = item.get("type", "?")
                        types[t] = types.get(t, 0) + 1
                    print(
                        f"  q={q!r:<25} "
                        f"n={result['count']:>2}  "
                        f"t={result['elapsed']*1000:>6.0f}ms  "
                        f"types={types}"
                    )
            else:
                errors += 1
                print(f"  q={q!r:<25} ERROR: {result['error']}")
        all_latencies.extend(latencies)

    if all_latencies:
        all_latencies.sort()
        n = len(all_latencies)
        mean = statistics.mean(all_latencies)
        p50 = all_latencies[n // 2]
        p95 = all_latencies[int(n * 0.95)]
        print(f"\n  ── 延迟统计 ({n} 次成功) ──")
        print(f"  mean={mean*1000:.0f}ms  p50={p50*1000:.0f}ms  p95={p95*1000:.0f}ms  errors={errors}")
    else:
        print(f"  全部失败 ({errors} errors)")

    return all_latencies, errors


def bench_concurrent(label: str, fn, queries: List[str]):
    """并发测试：所有 query 同时发出，测量总体完成时间。"""
    print(f"\n{'='*60}")
    print(f"[{label}] 并发测试 ({len(queries)} queries 同时发出)")
    print(f"{'='*60}")

    results_by_q = {}
    t_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=len(queries)) as ex:
        futures = {ex.submit(fn, q): q for q in queries}
        for fut in as_completed(futures):
            q = futures[fut]
            results_by_q[q] = fut.result()

    wall_time = time.perf_counter() - t_start

    latencies = []
    for q in queries:
        r = results_by_q[q]
        status = f"n={r['count']:>2}  t={r['elapsed']*1000:>6.0f}ms" if r["ok"] else f"ERROR: {r['error']}"
        print(f"  q={q!r:<25} {status}")
        if r["ok"]:
            latencies.append(r["elapsed"])

    print(f"\n  ── 并发墙钟时间: {wall_time*1000:.0f}ms  (串行估算: {sum(latencies)*1000:.0f}ms)")
    if latencies:
        print(f"  并发加速比: {sum(latencies)/wall_time:.1f}x  max_single={max(latencies)*1000:.0f}ms")

    return wall_time, latencies


def compare_quality(queries: List[str]):
    """对同一 query 比较两种 API 的返回结果交集和差集。"""
    print(f"\n{'='*60}")
    print("[质量对比] MCP vs Direct — 结果重叠分析")
    print(f"{'='*60}")

    for q in queries[:3]:  # 只比较前3个
        r_mcp = search_mcp(q, count=10)
        r_dir = search_direct(q, count=10)

        if not r_mcp["ok"] or not r_dir["ok"]:
            print(f"  q={q!r}: 有接口错误，跳过对比")
            continue

        titles_mcp = {i["title"] for i in r_mcp["items"]}
        titles_dir = {i["title"] for i in r_dir["items"]}
        common = titles_mcp & titles_dir
        only_mcp = titles_mcp - titles_dir
        only_dir = titles_dir - titles_mcp

        print(f"\n  query: {q!r}")
        print(f"    MCP {len(titles_mcp)} 条 | Direct {len(titles_dir)} 条")
        print(f"    共同结果: {len(common)} 条")
        print(f"    仅 MCP:   {sorted(only_mcp)[:3]}")
        print(f"    仅 Direct:{sorted(only_dir)[:3]}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if not UAT:
        print("ERROR: FEISHU_UAT 未配置，请检查 .env")
        return

    print(f"UAT: {UAT[:20]}...")
    print(f"MCP endpoint: {MCP_ENDPOINT}")
    print(f"Direct URL:   {DIRECT_URL}")

    # ── 单请求延迟
    mcp_latencies, mcp_errors = bench_single("MCP", search_mcp, BENCH_QUERIES, REPEAT)
    dir_latencies, dir_errors = bench_single("Direct", search_direct, BENCH_QUERIES, REPEAT)

    # ── 并发
    mcp_wall, _ = bench_concurrent("MCP", search_mcp, BENCH_QUERIES)
    dir_wall, _ = bench_concurrent("Direct", search_direct, BENCH_QUERIES)

    # ── 质量对比
    compare_quality(BENCH_QUERIES)

    # ── 汇总
    print(f"\n{'='*60}")
    print("综合对比汇总")
    print(f"{'='*60}")

    def fmt_stats(lats, errors, label):
        if lats:
            lats = sorted(lats)
            n = len(lats)
            return (
                f"  {label:<10} mean={statistics.mean(lats)*1000:>6.0f}ms  "
                f"p50={lats[n//2]*1000:>6.0f}ms  "
                f"p95={lats[int(n*0.95)]*1000:>6.0f}ms  "
                f"errors={errors}"
            )
        return f"  {label:<10} 全部失败"

    print(fmt_stats(mcp_latencies, mcp_errors, "MCP"))
    print(fmt_stats(dir_latencies, dir_errors, "Direct"))
    print(f"  {'并发(MCP)':<10} wall={mcp_wall*1000:.0f}ms")
    print(f"  {'并发(Direct)':<10} wall={dir_wall*1000:.0f}ms")

    # 建议
    print(f"\n{'='*60}")
    print("结论建议")
    print(f"{'='*60}")
    if mcp_latencies and dir_latencies:
        mcp_mean = statistics.mean(mcp_latencies)
        dir_mean = statistics.mean(dir_latencies)
        faster = "Direct" if dir_mean < mcp_mean else "MCP"
        ratio = max(mcp_mean, dir_mean) / min(mcp_mean, dir_mean)
        print(f"  速度: {faster} 更快 ({ratio:.1f}x)")
        if ratio < 1.3:
            print("  差异 < 30%，速度基本持平")

    if mcp_errors == 0 and dir_errors == 0:
        print("  稳定性: 两者均无错误")
    elif mcp_errors > dir_errors:
        print(f"  稳定性: Direct 更稳定 (MCP errors={mcp_errors})")
    elif dir_errors > mcp_errors:
        print(f"  稳定性: MCP 更稳定 (Direct errors={dir_errors})")


if __name__ == "__main__":
    main()
