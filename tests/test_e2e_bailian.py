"""
端到端诊断测试 — 仅 Bailian 数据源
用法: python tests/test_e2e_bailian.py "nubia 2025 Q4 广告ROAS分析"
"""
import asyncio, sys, time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv()

from tools.query_planner import plan_query
from tools.bailian_retrieve import bailian_retrieve_unified, get_configured_indexes
from tools.reranker import rerank_and_filter, _extract_focus_words
from tools.evaluator import EvaluationLayer, EvalConfig

SEP = "=" * 65

async def run_test(query: str):
    t_total = time.monotonic()

    # ── STEP 0: 配置检查
    print(f"\n{SEP}\nSTEP 0 ▶ 配置检查\n{SEP}")
    indexes = get_configured_indexes()
    print(f"  已配置知识库: {indexes}")
    if not indexes:
        print("  ❌ 未配置 BAILIAN_INDEX_MAP"); return

    # ── STEP 1: Query Planning
    print(f"\n{SEP}\nSTEP 1 ▶ Query Planning (1× LLM)\n{SEP}")
    print(f"  输入 query: {query!r}")
    t0 = time.monotonic()
    plan = plan_query(query, available_sources=["bailian"])
    plan_ms = int((time.monotonic() - t0) * 1000)
    print(f"  耗时: {plan_ms}ms | query_type={plan.query_type} | time_filter={plan.time_filter}")
    print(f"  sub_queries ({len(plan.sub_queries)} 条):")
    for i, sq in enumerate(plan.sub_queries, 1):
        print(f"    [{i}] query  : {sq.query}")
        print(f"         focus  : {sq.focus}")
        print(f"         tags   : {sq.tags}  indexes: {sq.indexes}")

    # ── STEP 2: 并行检索
    print(f"\n{SEP}\nSTEP 2 ▶ 并行检索 (EvaluationLayer)\n{SEP}")
    focuses = [sq.focus for sq in plan.sub_queries if sq.focus]
    focus_words = _extract_focus_words(focuses)
    print(f"  Focus 关键词: {sorted(focus_words)}")

    task_map = {}
    for sq in plan.sub_queries:
        if "bailian" not in sq.targets:
            continue
        kwargs = {}
        if sq.tags:    kwargs["tags"]    = sq.tags
        if sq.indexes: kwargs["indexes"] = sq.indexes
        task = asyncio.create_task(bailian_retrieve_unified(sq.query, **kwargs))
        label = f"bailian[{','.join(sq.indexes) if sq.indexes else 'all'}]/{sq.query[:25]}"
        task_map[task] = label
        print(f"  → 任务: {label!r}  tags={sq.tags}")

    if not task_map:
        print("  ⚠ 无 bailian 任务"); return

    t0 = time.monotonic()
    evaluator = EvaluationLayer(
        EvalConfig(min_good_chunks=3, min_sources=1, hard_timeout_seconds=15.0),
        verbose=True,
    )
    all_chunks, source_stats, _, _ = await evaluator.collect(task_map, focuses=focuses)
    retrieve_ms = int((time.monotonic() - t0) * 1000)
    print(f"\n  检索完成: {retrieve_ms}ms | raw={len(all_chunks)} chunks | {source_stats}")

    # ── STEP 3: Rerank
    print(f"\n{SEP}\nSTEP 3 ▶ Rerank + A1 Focus Boost + A2 Compression\n{SEP}")
    t0 = time.monotonic()
    final_chunks, _ = rerank_and_filter(all_chunks, top_k=8, focuses=focuses or None)
    rerank_ms = int((time.monotonic() - t0) * 1000)
    print(f"  耗时: {rerank_ms}ms | final={len(final_chunks)} chunks")

    # ── STEP 4: 最终 Chunks
    print(f"\n{SEP}\nSTEP 4 ▶ 最终 Chunks (top {len(final_chunks)})\n{SEP}")
    for i, c in enumerate(final_chunks, 1):
        meta = c.metadata
        doc_name   = meta.get("doc_name") or meta.get("title") or c.source_name or "—"
        index_name = meta.get("index_name", "—")
        hier       = (c.heading_path or meta.get("hier_title") or "—")[:80]
        url        = (c.source_url or "—")[:80]
        overlap    = meta.get("focus_overlap", 0)
        compressed = meta.get("compressed", False)
        print(f"\n  ── Chunk [{i}] score={c.score:.4f} tier={c.confidence_tier}")
        print(f"  知识库  : {index_name}")
        print(f"  文档名  : {doc_name}")
        print(f"  章节路径: {hier}")
        print(f"  URL     : {url}")
        print(f"  focus匹配={overlap}词  压缩={compressed}  文本长={len(c.text)}字")
        print(f"  文本内容: {c.text[:300].replace(chr(10),'↵')}{'...' if len(c.text)>300 else ''}")
        if compressed and "original_text" in meta:
            print(f"  原文前150: {meta['original_text'][:150].replace(chr(10),'↵')}...")

    # ── STEP 5: 总结
    total_ms = int((time.monotonic() - t_total) * 1000)
    print(f"\n{SEP}\nSTEP 5 ▶ 总结\n{SEP}")
    print(f"  Query: {query!r}")
    print(f"  Plan={plan_ms}ms | 检索={retrieve_ms}ms | Rerank={rerank_ms}ms | 总={total_ms}ms")
    print(f"  Raw chunks={len(all_chunks)} → Final={len(final_chunks)} | 来源={source_stats}")
    print(f"\n  [API字段完整性]")
    for field, getter in [
        ("doc_name",     lambda c: bool(c.metadata.get("doc_name"))),
        ("source_url",   lambda c: bool(c.source_url)),
        ("heading_path", lambda c: bool(c.heading_path or c.metadata.get("hier_title"))),
        ("text非空",     lambda c: bool(c.text.strip())),
    ]:
        hits = sum(1 for c in final_chunks if getter(c))
        print(f"  {field:16s}: {hits}/{len(final_chunks)}")

if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "nubia 2025 Q4 广告ROAS和营销数据分析"
    asyncio.run(run_test(q))
