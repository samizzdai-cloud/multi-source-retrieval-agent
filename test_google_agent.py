import asyncio
import logging
from tools.core.models import AgentConfig
from retrieve_agent import build_retrieve_agent_from_config
from google.adk.agents.invocation_context import InvocationContext
from google.genai import types as genai_types

logging.basicConfig(level=logging.INFO)

async def main():
    cfg = AgentConfig(
        preset="lite",
        sources=["google"]
    )
    agent = build_retrieve_agent_from_config(cfg)
    print("Agent built successfully!")

    # mock invocation
    ctx = InvocationContext(
        invocation_id="test",
        branch="main",
        user_content=genai_types.Content(
            role="user",
            parts=[genai_types.Part(text="test Google workspace search")]
        )
    )
    
    async for event in agent._run_async_impl(ctx):
        print(event.actions.state_delta)

if __name__ == "__main__":
    asyncio.run(main())
