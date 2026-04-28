#!/usr/bin/env python3
"""
从 eval_queries.json 选 4 个 query，详细对比 3 种 preset 的行为差异
"""
import asyncio
import json
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

from google.adk.runners import InMemoryRunner
from google.genai import types
from retrieve_agent import build_retrieve_agent
from tools.core.models import RetrieveConfig, SourceAgentConfig, SourceConfig

# 从 eval_queries.json 选 4 个有代表性的 query
QUERIES = [
    {"id": 1,  "query": "nubia 英国站3月份DTC渠道的GMV和订单量",          "type": "data",    "difficulty": "easy"},
    {"id": 3,  "query": "nubia Z70 Ultra 的产品定价策略",                 "type": "concept", "difficulty": "easy"},
    {"id": 6,  "query": "nubia Amazon渠道12月份的运营月报和问题总结",     "type": "mixed",   "difficulty": "medium"},
    {"id": 55, "query": "按渠道分类对比nubia和hypershell的总体销售数据",  "type": "data",    "difficulty": "hard"},
]

PRESETS = ["lite", "balanced", "deep_dive"]


def _extract_metrics(events: list) -> dict:
    """从事件日志中提取详细指标"""
    result = {
        "status": "NO_RESULT",
        "chunks": 0,
        "source_stats": {},
        # lite 特有
        "lite_search_raw": 0,
        "lite_doc_route": "",
        "lite_batch_total_ms": 0,
        # balanced/deep_dive 特有
        "doc_route": "",
        "doc_candidates": 0,
        "table_candidates": 0,
        "batch_prepare_ms": 0,
        "batch_total_ms": 0,
        "doc_wait_ms": 0,
        "table_wait_ms": 0,
        "chunk_select_input": 0,
        "chunk_select_ms": 0,
        "eval_only_ms": 0,
        "loops": 0,
        "table_roi": {},
        # 通用
        "dispatch_ms": 0,
        "render_ms": 0,
    }
    for ev in events:
        delta = ev.actions.state_delta if ev.actions else {}
        result_text = delta.get("retrieval_result", "")
        if result_text:
            try:
                payload = json.loads(result_text)
                result["chunks"] = len(payload.get("chunks", []))
                result["source_stats"] = payload.get("source_stats", {})
                result["status"] = "PASS"
            except:
                pass
        
        if not (ev.content and ev.content.parts):
            continue
        for part in ev.content.parts:
            text = part.text if hasattr(part, 'text') else str(part)
            if not text:
                continue
            
            # lite 搜索
            if "TIMING feishu.lite.search_docs" in text and "raw=" in text:
                try:
                    result["lite_search_raw"] += int(text.split("raw=")[1].split()[0])
                except:
                    pass
            # lite doc route
            if "[feishu][lite] doc route_hint" in text:
                result["lite_doc_route"] = text.strip()
            # lite batch_total
            if "TIMING feishu.lite.batch_total" in text:
                try:
                    result["lite_batch_total_ms"] = float(text.split("ms=")[-1].split()[0])
                except:
                    pass
            
            # balanced/deep_dive doc route
            if "[feishu] doc route_hint" in text and "[lite]" not in text:
                result["doc_route"] = text.strip()
            # batch_prepare
            if "TIMING feishu.batch_prepare_search" in text:
                try:
                    result["batch_prepare_ms"] = float(text.split("ms=")[-1].split()[0])
                except:
                    pass
            # batch_total
            if "TIMING feishu.batch_total" in text and "lite" not in text:
                try:
                    result["batch_total_ms"] = float(text.split("ms=")[-1].split()[0])
                except:
                    pass
            # doc_wait
            if "execute_one_doc_wait" in text:
                try:
                    result["doc_wait_ms"] = float(text.split("ms=")[-1].split()[0])
                except:
                    pass
            # table_wait
            if "execute_one_table_wait" in text:
                try:
                    result["table_wait_ms"] = float(text.split("ms=")[-1].split()[0])
                except:
                    pass
            # chunk_select
            if "chunk_select mode=rerank" in text:
                try:
                    result["chunk_select_input"] = int(text.split("input=")[1].split()[0])
                    result["chunk_select_ms"] = float(text.split("ms=")[-1].split()[0])
                except:
                    pass
            # eval_only
            if "source.eval_only" in text:
                try:
                    result["eval_only_ms"] = float(text.split("ms=")[-1].split()[0])
                except:
                    pass
            # loops
            if "done: loops=" in text:
                try:
                    result["loops"] = int(text.split("loops=")[1].split(",")[0])
                except:
                    pass
            # table roi
            if "[feishu] table roi summary" in text:
                try:
                    parts = text.split("total=")[1].split()
                    result["table_roi"]["total"] = int(parts[0])
                    result["table_roi"]["submitted"] = int(text.split("submitted=")[1].split()[0])
                    result["table_roi"]["final_selected"] = int(text.split("final_selected=")[1].split()[0])
                except:
                    pass
            
            # retrieve dispatch
            if "TIMING retrieve.dispatch" in text:
                try:
                    result["dispatch_ms"] = float(text.split("ms=")[-1].split()[0])
                except:
                    pass
            if "TIMING retrieve.render" in text:
                try:
                    result["render_ms"] = float(text.split("ms=")[-1].split()[0])
                except:
                    pass
    
    return result


async def test_preset(query_info: dict, preset: str):
    app_name = f"eval_{preset}_q{query_info['id']}"
    cfg = RetrieveConfig(
        preset=preset,
        enabled_sources=["feishu"],
        source_configs={"feishu": SourceAgentConfig(source_timeout_s=60.0)},
    )
    sc = SourceConfig(load_from_env=True)
    agent = build_retrieve_agent(config=cfg, source_config=sc, name=f"retrieve_{preset}")
    runner = InMemoryRunner(agent=agent, app_name=app_name)

    msg = types.Content(role="user", parts=[types.Part(text=query_info["query"])])
    session = await runner.session_service.create_session(app_name=app_name, user_id="u_test")

    t0 = time.perf_counter()
    events = []
    async for ev in runner.run_async(user_id="u_test", session_id=session.id, new_message=msg):
        events.append(ev)
    wall_ms = (time.perf_counter() - t0) * 1000

    metrics = _extract_metrics(events)
    metrics["wall_ms"] = int(wall_ms)
    return metrics


async def main():
    results = []
    for q in QUERIES:
        for preset in PRESETS:
            print(f"\n{'='*80}")
            print(f"Testing: id={q['id']} preset={preset} type={q['type']} difficulty={q['difficulty']}")
            print(f"Query: {q['query']}")
            print(f"{'='*80}")
            try:
                m = await test_preset(q, preset)
                results.append({"query_id": q["id"], "preset": preset, **m})
                print(f"  ✅ status={m['status']} chunks={m['chunks']} wall={m['wall_ms']}ms")
                if preset == "lite":
                    print(f"     lite_search_raw={m['lite_search_raw']} route={m['lite_doc_route']} batch_total={m['lite_batch_total_ms']}ms")
                else:
                    print(f"     route={m['doc_route']}")
                    print(f"     batch_prepare={m['batch_prepare_ms']}ms batch_total={m['batch_total_ms']}ms")
                    print(f"     doc_wait={m['doc_wait_ms']}ms table_wait={m['table_wait_ms']}ms")
                    print(f"     chunk_select(input={m['chunk_select_input']} ms={m['chunk_select_ms']})")
                    print(f"     eval_only={m['eval_only_ms']}ms loops={m['loops']}")
                    if m["table_roi"]:
                        print(f"     table_roi={m['table_roi']}")
            except Exception as e:
                print(f"  ❌ ERROR: {e}")
                results.append({"query_id": q["id"], "preset": preset, "status": "ERROR", "error": str(e)})

    # 汇总表格
    print(f"\n\n{'='*110}")
    print(f"{'QID':<5} {'Type':<8} {'Preset':<10} {'Wall(ms)':<10} {'Chunks':<8} {'Loops':<6} {'Search':<10} {'DocWait':<10} {'TableWait':<10} {'Eval(ms)':<10}")
    print(f"{'='*110}")
    for r in results:
        if r.get("status") == "ERROR":
            print(f"{r['query_id']:<5} {'?':<8} {r['preset']:<10} ERROR: {r.get('error','')}")
            continue
        q_info = next(q for q in QUERIES if q["id"] == r["query_id"])
        search_info = r.get("lite_search_raw", 0) if r["preset"] == "lite" else r.get("chunk_select_input", 0)
        print(f"{r['query_id']:<5} {q_info['type']:<8} {r['preset']:<10} "
              f"{r['wall_ms']:<10} {r['chunks']:<8} {r.get('loops',0):<6} "
              f"{search_info:<10} {r.get('doc_wait_ms',0):<10.0f} {r.get('table_wait_ms',0):<10.0f} {r.get('eval_only_ms',0):<10.0f}")
    print(f"{'='*110}")


if __name__ == "__main__":
    asyncio.run(main())
