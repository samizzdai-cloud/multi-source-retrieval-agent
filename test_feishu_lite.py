"""测试 feishu_retrieve 在 lite mode 下能否跑通。"""
import asyncio
import logging
import os
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

from dotenv import load_dotenv
load_dotenv(override=True)

UAT = os.environ.get("FEISHU_UAT", "").strip()
if not UAT:
    print("ERROR: FEISHU_UAT not set")
    sys.exit(1)

# ── Test 1: 直接测试 feishu_retrieve dispatcher ────────────────────────────
async def test_dispatcher():
    from tools.feishu_retrieve import feishu_retrieve
    from tools.feishu_doc_pool import FeishuDocPool
    from tools.table_pool import TablePool

    doc_pool = FeishuDocPool(uat=UAT)
    table_pool = TablePool(uat=UAT)

    query = "nubia 销售策略"
    print(f"\n=== Test 1: feishu_retrieve dispatcher ===")
    print(f"Query: {query}")
    t0 = time.perf_counter()
    chunks = await feishu_retrieve(
        query=query,
        uat=UAT,
        query_type="structured",
        doc_pool=doc_pool,
        table_pool=table_pool,
        focus_id="focus_0",
        focus_text=query,
        sub_query_id="sq_0",
    )
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"Result: {len(chunks)} chunks, {elapsed:.1f}ms")
    for i, c in enumerate(chunks[:5]):
        print(f"  [{i}] {c.source_name[:40]:<40} | score={c.score:.3f} | {c.text[:80]}...")
    return len(chunks)

# ── Test 2: 测试 FeishuSourceAgent 完整链路（lite mode）─────────────────────
async def test_source_agent():
    from agents.feishu_source_agent import FeishuSourceAgent
    from tools.feishu_doc_pool import FeishuDocPool
    from tools.table_pool import TablePool
    from tools.models import SourceAgentConfig
    from agents.source_agent_base import Focus

    cfg = SourceAgentConfig(source_top_k=5, min_loops=1, max_loops=1, source_timeout_s=45.0)
    doc_pool = FeishuDocPool(uat=UAT)
    table_pool = TablePool(uat=UAT)

    agent = FeishuSourceAgent(
        cfg=cfg,
        uat=UAT,
        doc_pool=doc_pool,
        table_pool=table_pool,
        preset="lite",
    )

    query = "nubia 销售策略"
    focuses = [Focus(focus_id="focus_0", text=query, targets=["feishu"])]

    print(f"\n=== Test 2: FeishuSourceAgent (lite mode) ===")
    print(f"Query: {query}")
    t0 = time.perf_counter()
    result = await agent.run(focuses)
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"Result: loops={result.loops_used}, top_chunks={len(result.top_chunks)}, elapsed={result.elapsed_s:.1f}s")
    for i, ch in enumerate(result.top_chunks[:5]):
        print(f"  [{i}] tier={ch.tier} score={ch.score:.3f} | {ch.content[:80]}...")
    return len(result.top_chunks)

# ── Test 3: 测试 RetrieveAgent 完整链路（lite mode, 只开 feishu）───────────
async def test_retrieve_agent():
    from retrieve_agent import build_retrieve_agent
    from tools.models import RetrieveConfig, SourceAgentConfig, SourceConfig
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    cfg = RetrieveConfig(
        preset="lite",
        enabled_sources=["feishu"],
        source_configs={
            "feishu": SourceAgentConfig(source_timeout_s=45.0),
        },
    )
    sc = SourceConfig(load_from_env=True)
    agent = build_retrieve_agent(config=cfg, source_config=sc, name="retrieve_test")
    runner = InMemoryRunner(agent=agent, app_name="test_feishu_lite")

    query = "nubia 销售策略"
    msg = types.Content(role="user", parts=[types.Part(text=query)])

    print(f"\n=== Test 3: RetrieveAgent (lite mode, feishu only) ===")
    print(f"Query: {query}")
    t0 = time.perf_counter()

    session = await runner.session_service.create_session(app_name="test_feishu_lite", user_id="u_test")
    events = []
    async for ev in runner.run_async(user_id="u_test", session_id=session.id, new_message=msg):
        events.append(ev)

    elapsed = (time.perf_counter() - t0) * 1000
    import json
    for e in events:
        delta = e.actions.state_delta if e.actions else {}
        result = delta.get("retrieval_result", "")
        if result:
            try:
                payload = json.loads(result)
                chunks = payload.get("chunks", [])
                print(f"Result: {len(chunks)} chunks, {elapsed:.1f}ms")
                print(f"source_stats: {payload.get('source_stats')}")
                for i, c in enumerate(chunks[:5]):
                    print(f"  [{i}] {c.get('source_name','')[:40]:<40} | tier={c.get('confidence_tier')} | {c.get('text','')[:60]}...")
                return len(chunks)
            except Exception as e2:
                print(f"Parse error: {e2}")
                print(f"Raw result: {result[:500]}")
                return 0

    print("No events with retrieval_result")
    return 0

async def main():
    results = {}
    try:
        results["dispatcher"] = await test_dispatcher()
    except Exception as e:
        logging.exception("Test 1 failed")
        results["dispatcher"] = -1

    try:
        results["source_agent"] = await test_source_agent()
    except Exception as e:
        logging.exception("Test 2 failed")
        results["source_agent"] = -1

    try:
        results["retrieve_agent"] = await test_retrieve_agent()
    except Exception as e:
        logging.exception("Test 3 failed")
        results["retrieve_agent"] = -1

    print(f"\n=== Summary ===")
    for k, v in results.items():
        status = "PASS" if v > 0 else ("FAIL" if v == -1 else "EMPTY")
        print(f"  {k}: {status} ({v} chunks)")

if __name__ == "__main__":
    asyncio.run(main())
