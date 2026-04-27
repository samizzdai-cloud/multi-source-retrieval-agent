"""测试 feishu 在三种 preset (lite / balanced / deep_dive) 下均能跑通。"""
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

from google.adk.runners import InMemoryRunner
from google.genai import types
from retrieve_agent import build_retrieve_agent
from tools.models import RetrieveConfig, SourceAgentConfig, SourceConfig

QUERY = "nubia 销售策略"


async def test_preset(preset: str):
    cfg = RetrieveConfig(
        preset=preset,
        enabled_sources=["feishu"],
        source_configs={
            "feishu": SourceAgentConfig(source_timeout_s=60.0),
        },
    )
    sc = SourceConfig(load_from_env=True)
    agent = build_retrieve_agent(config=cfg, source_config=sc, name=f"retrieve_{preset}")
    runner = InMemoryRunner(agent=agent, app_name=f"test_{preset}")

    msg = types.Content(role="user", parts=[types.Part(text=QUERY)])
    session = await runner.session_service.create_session(
        app_name=f"test_{preset}", user_id="u_test"
    )

    t0 = time.perf_counter()
    events = []
    async for ev in runner.run_async(
        user_id="u_test", session_id=session.id, new_message=msg
    ):
        events.append(ev)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    import json
    for e in events:
        delta = e.actions.state_delta if e.actions else {}
        result = delta.get("retrieval_result", "")
        if result:
            try:
                payload = json.loads(result)
                chunks = payload.get("chunks", [])
                stats = payload.get("source_stats", {})
                focuses = payload.get("focuses", [])
                return {
                    "preset": preset,
                    "status": "PASS",
                    "chunks": len(chunks),
                    "focuses": len(focuses),
                    "source_stats": stats,
                    "elapsed_ms": int(elapsed_ms),
                }
            except Exception as e2:
                return {
                    "preset": preset,
                    "status": "PARSE_ERROR",
                    "error": str(e2),
                    "elapsed_ms": int(elapsed_ms),
                }

    return {
        "preset": preset,
        "status": "NO_RESULT",
        "elapsed_ms": int(elapsed_ms),
    }


async def main():
    presets = ["lite", "balanced", "deep_dive"]
    results = []
    for preset in presets:
        print(f"\n{'='*60}")
        print(f"Testing preset={preset!r}")
        print(f"{'='*60}")
        try:
            result = await test_preset(preset)
            results.append(result)
        except Exception as e:
            logging.exception("Preset %s failed", preset)
            results.append(
                {
                    "preset": preset,
                    "status": "EXCEPTION",
                    "error": str(e),
                    "elapsed_ms": 0,
                }
            )

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for r in results:
        status = r["status"]
        ok = "✅" if status == "PASS" else "❌"
        chunks = r.get("chunks", "N/A")
        elapsed = r.get("elapsed_ms", 0)
        print(
            f"  {ok} {r['preset']:<12} | {status:<8} | chunks={chunks:<4} | {elapsed}ms"
        )
        if status == "PASS":
            print(f"       source_stats={r.get('source_stats')}")
        elif status != "PASS":
            print(f"       error={r.get('error', 'N/A')}")

    all_pass = all(r["status"] == "PASS" for r in results)
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    asyncio.run(main())
