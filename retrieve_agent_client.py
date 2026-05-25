"""Programmatic retrieve-agent entrypoint driven by explicit config objects.

This module lets an external parent program:
1. Build a RetrieveAgent from an explicit AgentConfig
2. Invoke it with a RetrieveRequest
3. Receive structured output: chunks + top-level metadata + progress events
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace
from typing import List

from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from retrieve_agent import build_retrieve_agent_from_config
from tools.core.models import (
    AgentConfig,
    RetrieveInvocationResult,
    RetrieveRequest,
    SourceConfig,
)


def config(**kwargs) -> AgentConfig:
    """Thin helper for external callers that prefer config(...) syntax."""
    return AgentConfig(**kwargs)


def request(**kwargs) -> RetrieveRequest:
    """Thin helper for external callers that prefer request(...) syntax."""
    return RetrieveRequest(**kwargs)


def _merge_source_config(base: SourceConfig, override: SourceConfig) -> SourceConfig:
    merged = SourceConfig(
        feishu_uat=override.feishu_uat or base.feishu_uat,
        feishu_open_id=override.feishu_open_id or base.feishu_open_id,
        bailian_allowed_indexes=override.bailian_allowed_indexes or base.bailian_allowed_indexes,
        bailian_workspace_id=override.bailian_workspace_id or base.bailian_workspace_id,
        bailian_access_key_id=override.bailian_access_key_id or base.bailian_access_key_id,
        bailian_access_key_secret=override.bailian_access_key_secret or base.bailian_access_key_secret,
        bailian_index_map=override.bailian_index_map or base.bailian_index_map,
        bailian_index_id=override.bailian_index_id or base.bailian_index_id,
        dashscope_api_key=override.dashscope_api_key or base.dashscope_api_key,
        google_api_key=override.google_api_key or base.google_api_key,
        gemini_fallback_model=override.gemini_fallback_model or base.gemini_fallback_model,
        google_credentials_path=override.google_credentials_path or base.google_credentials_path,
        google_token_path=override.google_token_path or base.google_token_path,
        load_from_env=override.load_from_env if override.load_from_env != base.load_from_env else base.load_from_env,
    )
    return merged


def _merge_agent_config(base: AgentConfig, request: RetrieveRequest) -> AgentConfig:
    merged = replace(base)
    if request.preset_override:
        merged.preset = request.preset_override
    if request.enabled_sources_override is not None:
        merged.sources = list(request.enabled_sources_override)
    if request.auth_override is not None:
        merged_source = _merge_source_config(base.to_source_config(), request.auth_override)
        merged = AgentConfig(
            preset=merged.preset,
            sources=list(merged.sources),
            feishu_uat=merged_source.feishu_uat,
            feishu_open_id=merged_source.feishu_open_id,
            bailian_allowed_indexes=merged_source.bailian_allowed_indexes,
            bailian_workspace_id=merged_source.bailian_workspace_id,
            bailian_access_key_id=merged_source.bailian_access_key_id,
            bailian_access_key_secret=merged_source.bailian_access_key_secret,
            bailian_index_map=merged_source.bailian_index_map,
            bailian_index_id=merged_source.bailian_index_id,
            google_api_key=merged_source.google_api_key,
            gemini_fallback_model=merged_source.gemini_fallback_model,
            dashscope_api_key=merged_source.dashscope_api_key,
            planner_model=base.planner_model,
            eval_model=base.eval_model,
            google_credentials_path=merged_source.google_credentials_path,
            google_token_path=merged_source.google_token_path,
            load_from_env=merged_source.load_from_env,
        )
    return merged


def build_retrieve_agent_with_config(agent_config: AgentConfig, name: str = "retrieve_v1"):
    """Build RetrieveAgent from explicit config, without relying on local .env by default."""
    return build_retrieve_agent_from_config(agent_config, name=name)


async def invoke_retrieve_agent(
    agent_config: AgentConfig,
    request: RetrieveRequest,
    *,
    app_name: str = "retrieve_agent_client",
    agent_name: str = "retrieve_v1",
) -> RetrieveInvocationResult:
    """Run retrieve_agent once and return structured output."""
    effective_config = _merge_agent_config(agent_config, request)
    agent = build_retrieve_agent_with_config(effective_config, name=f"{agent_name}_{uuid.uuid4().hex[:6]}")

    runner = InMemoryRunner(agent=agent, app_name=app_name)
    user_id = f"cfg_{uuid.uuid4().hex[:8]}"
    session = await runner.session_service.create_session(app_name=app_name, user_id=user_id)
    content = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=request.user_query)],
    )

    raw_payload = {}
    progress_events: List[str] = []
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=content,
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    progress_events.append(part.text)
        if event.actions and event.actions.state_delta:
            raw = event.actions.state_delta.get("retrieval_result")
            if raw:
                raw_payload = json.loads(raw)

    chunks = list(raw_payload.get("chunks") or [])
    metadata = {k: v for k, v in raw_payload.items() if k != "chunks"}
    return RetrieveInvocationResult(
        query=request.user_query,
        raw_payload=raw_payload,
        chunks=chunks,
        metadata=metadata,
        progress_events=progress_events,
    )


def invoke_retrieve_agent_sync(
    agent_config: AgentConfig,
    request: RetrieveRequest,
    *,
    app_name: str = "retrieve_agent_client",
    agent_name: str = "retrieve_v1",
) -> RetrieveInvocationResult:
    """Synchronous wrapper around invoke_retrieve_agent."""
    return asyncio.run(
        invoke_retrieve_agent(
            agent_config,
            request,
            app_name=app_name,
            agent_name=agent_name,
        )
    )
