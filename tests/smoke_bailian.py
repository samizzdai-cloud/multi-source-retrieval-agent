"""Bailian-only smoke test：走完 retrieve_agent 全流程。

目的：验证 bailian plan_only → execute_sub_query_full → eval_only 链路端到端通畅
（不测 feishu，不开 eval_layer）。用 preset="lite" 走 STRICT 单 loop。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from retrieve_agent import build_retrieve_agent
from tools.models import RetrieveConfig, SourceConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
# 静音 ADK / genai 无关噪音
logging.getLogger("google_adk").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)

QUERY = "nubia 英国站3月份DTC渠道的GMV和订单量"


async def main() -> int:
    cfg = RetrieveConfig(
        enabled_sources=["bailian"],
        preset="lite",  # STRICT + eval_layer off
    )
    agent = build_retrieve_agent(
        config=cfg,
        source_config=SourceConfig(load_from_env=True),
        name="smoke_bailian",
    )

    runner = InMemoryRunner(agent=agent, app_name="smoke_bailian")
    user_id = "u_smoke"
    session = await runner.session_service.create_session(
        app_name="smoke_bailian", user_id=user_id,
    )
    content = genai_types.Content(
        role="user", parts=[genai_types.Part(text=QUERY)],
    )

    t0 = time.monotonic()
    final_payload = None
    async for event in runner.run_async(
        user_id=user_id, session_id=session.id, new_message=content,
    ):
        if event.actions and event.actions.state_delta:
            raw = event.actions.state_delta.get("retrieval_result")
            if raw:
                final_payload = json.loads(raw)
    elapsed = time.monotonic() - t0

    print("\n" + "=" * 70)
    print(f"[smoke_bailian] query={QUERY!r}  elapsed={elapsed:.2f}s")
    print("=" * 70)

    if not final_payload:
        print("❌ no retrieval_result produced")
        return 1

    focuses = final_payload.get("focuses", [])
    chunks = final_payload.get("chunks", [])
    source_stats = final_payload.get("source_stats", {})
    focus_coverage = final_payload.get("focus_coverage", {})
    chunks_by_focus = final_payload.get("chunks_by_focus", {})
    total_ms = final_payload.get("total_time_ms", 0)

    print(f"focuses ({len(focuses)}):")
    for f in focuses:
        print(f"  - {f.get('focus_id')}: {f.get('text')}")

    print(f"\nsource_stats:       {source_stats}")
    print(f"focus_coverage:     {focus_coverage}")
    print(f"chunks_by_focus:    {chunks_by_focus}")
    print(f"total_time_ms:      {total_ms}")

    print(f"\nchunks ({len(chunks)}):")
    for i, c in enumerate(chunks[:8], 1):
        meta = c.get("metadata") or {}
        cid = meta.get("chunk_id", "")
        score = c.get("score", 0) or 0
        tier = c.get("confidence_tier", "")
        sub_q = (meta.get("sub_query") or "")[:50]
        source_name = (c.get("source_name") or "")[:50]
        print(f"  {i}. {cid}")
        print(f"     score={score:.3f}  tier={tier!r}  doc={source_name!r}")
        print(f"     sub_q={sub_q!r}")

    bailian_count = source_stats.get("bailian", 0)
    ok = len(chunks) > 0 and bailian_count > 0
    print("\n" + ("✅ PASS" if ok else "❌ FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
