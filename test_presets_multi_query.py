#!/usr/bin/env python3
"""
多 Query 下不同 preset 的耗时对比测试
"""
import asyncio
import logging
import os
import sys
import time
import json

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
from tools.models import RetrieveConfig, SourceAgentConfig, SourceConfig

QUERIES = [
    "飞书多维表格怎么使用",
    "公司入职流程是什么",
    "Q2 销售数据汇总",
    "项目进度跟踪方法",
    "如何配置飞书审批流程",
]

PRESETS = ["lite", "balanced", "deep_dive"]


def _extract_timings(events: list) -> dict:
    """从事件日志中提取关键耗时指标"""
    result = {
        "total_ms": 0,
        "doc_route": "",
        "doc_wait_ms": 0,
        "chunk_select_input": 0,
        "chunk_select_ms": 0,
        "status": "NO_RESULT",
        "chunks": 0,
    }
    for ev in events:
        # 从 state_delta 获取结果
        delta = ev.actions.state_delta if ev.actions else {}
        result_text = delta.get("retrieval_result", "")
        if result_text:
            try:
                payload = json.loads(result_text)
                result["chunks"] = len(payload.get("chunks", []))
                result["status"] = "PASS"
            except:
                pass
        # 从日志内容提取 TIMING
        if ev.content and ev.content.parts:
            for part in ev.content.parts:
                text = part.text if hasattr(part, 'text') else str(part)
                if "TIMING feishu.lite.batch_total" in text or "TIMING feishu.batch_total" in text:
                    if "ms=" in text:
                        try:
                            result["total_ms"] = float(text.split("ms=")[-1].split()[0])
                        except:
                            pass
                if "doc route_hint" in text:
                    result["doc_route"] = text.strip()
                if "doc_wait" in text and "ms=" in text:
                    try:
                        result["doc_wait_ms"] = float(text.split("ms=")[-1].split()[0])
                    except:
                        pass
                if "chunk_select" in text and "input" in text:
                    parts = text.split()
                    for i, p in enumerate(parts):
                        if p == "input=":
                            try:
                                result["chunk_select_input"] = int(parts[i+1])
                            except:
                                pass
                        if p == "ms=":
                            try:
                                result["chunk_select_ms"] = float(parts[i+1])
                            except:
                                pass
    return result


async def test_preset(query: str, preset: str):
    app_name = f"test_{preset}_{hash(query) & 0xFFFF}"
    cfg = RetrieveConfig(
        preset=preset,
        enabled_sources=["feishu"],
        source_configs={"feishu": SourceAgentConfig(source_timeout_s=60.0)},
    )
    sc = SourceConfig(load_from_env=True)
    agent = build_retrieve_agent(config=cfg, source_config=sc, name=f"retrieve_{preset}")
    runner = InMemoryRunner(agent=agent, app_name=app_name)

    msg = types.Content(role="user", parts=[types.Part(text=query)])
    session = await runner.session_service.create_session(app_name=app_name, user_id="u_test")

    t0 = time.perf_counter()
    events = []
    async for ev in runner.run_async(user_id="u_test", session_id=session.id, new_message=msg):
        events.append(ev)
    wall_ms = (time.perf_counter() - t0) * 1000

    timings = _extract_timings(events)
    timings["wall_ms"] = int(wall_ms)
    return timings


async def main():
    print("=" * 110)
    print(f"{'Query':<26} {'Preset':<10} {'Wall(ms)':<10} {'Batch(ms)':<10} {'DocWait(ms)':<12} {'Chunks':<8} {'Route':<25}")
    print("=" * 110)

    for query in QUERIES:
        for preset in PRESETS:
            try:
                result = await test_preset(query, preset)

                route = result.get("doc_route", "")
                if route:
                    if "block=" in route:
                        route = route.split("block=")[1].split()[0] + "b+" + route.split("mcp=")[1].split()[0] + "m"
                    elif "mcp=" in route:
                        route = route.split("mcp=")[1].split()[0] + "m"
                    else:
                        route = "?"

                print(
                    f"{query:<26} {preset:<10} "
                    f"{result['wall_ms']:<10} {result.get('total_ms', 0):<10.0f} "
                    f"{result.get('doc_wait_ms', 0):<12.0f} {result['chunks']:<8} {route:<25}"
                )
            except Exception as e:
                print(f"{query:<26} {preset:<10} ERROR: {e}")
        print("-" * 110)


if __name__ == "__main__":
    asyncio.run(main())
