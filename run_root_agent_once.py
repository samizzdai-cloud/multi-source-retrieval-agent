"""Run the full root agent once from the command line.

Examples:
    python3 run_root_agent_once.py "你的问题"
    RAG_PRESET=deep_dive RAG_ENABLED_SOURCES=google,feishu,bailian python3 run_root_agent_once.py "你的问题"
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from root_agent_v1 import root_agent


async def _run_once(query: str) -> str:
    runner = InMemoryRunner(agent=root_agent, app_name="context_rag_agent_v1")
    user_id = f"cli_{uuid.uuid4().hex[:8]}"
    session = await runner.session_service.create_session(
        app_name="context_rag_agent_v1",
        user_id=user_id,
    )
    content = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=query)],
    )

    final_text = ""
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=content,
    ):
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if part.text:
                final_text = part.text
                print(part.text, flush=True)

    return final_text


def main() -> None:
    parser = argparse.ArgumentParser(description="Run RetrieveAgent -> AnswerAgent once.")
    parser.add_argument("query", help="User query to send to the root agent.")
    args = parser.parse_args()

    final_text = asyncio.run(_run_once(args.query))
    if not final_text:
        raise SystemExit("Root agent finished without a text answer.")


if __name__ == "__main__":
    main()
