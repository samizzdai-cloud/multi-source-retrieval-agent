"""ADK Web workflow with independent retrieval and answer budgets."""

import asyncio
import json
import logging
import threading
import time
from contextlib import aclosing

from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from google.genai import types
from pydantic import PrivateAttr

from tools.core.retrieval_progress import progress_sink, retrieval_deadline
from feishu_read_tool.oauth_tokens import (
    FeishuOAuthError, authorize_via_loopback, ensure_valid_project_access_token, load_token_state,
)
from retrieve_agent import _build_feishu_source_agent


logger = logging.getLogger(__name__)


class WebRetrievalWorkflow(BaseAgent):
    retrieve_timeout_s: float = 200.0
    answer_timeout_s: float = 120.0
    manage_feishu_oauth: bool = False
    _retrieve_lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)
    _authorization_task: asyncio.Task | None = PrivateAttr(default=None)
    _authorization_ready: asyncio.Future | None = PrivateAttr(default=None)

    async def _authorization_url(self):
        if self._authorization_task is None or self._authorization_task.done():
            config = load_token_state()
            loop = asyncio.get_running_loop()
            ready = loop.create_future()
            self._authorization_ready = ready

            def publish_url(url):
                if not ready.done():
                    ready.set_result(url)

            async def listen():
                try:
                    await asyncio.to_thread(
                        authorize_via_loopback, redirect_uri=config.redirect_uri, scopes=config.scopes,
                        open_browser=False, on_authorization_ready=lambda url: loop.call_soon_threadsafe(publish_url, url),
                    )
                except (FeishuOAuthError, OSError) as exc:
                    logger.warning("Feishu Web OAuth ended: %s", type(exc).__name__)
                finally:
                    publish_url("")

            self._authorization_task = asyncio.create_task(listen())
        return await asyncio.shield(self._authorization_ready)

    async def _prepare_feishu(self, retrieve):
        current = retrieve.sources.get("feishu")
        if current is None:
            return ""
        try:
            config = await asyncio.to_thread(load_token_state)
            now = time.time()
            if config.access_expires_at and config.access_expires_at <= now and (
                not config.refresh_token or (config.refresh_expires_at and config.refresh_expires_at <= now)
            ):
                token = ""
            else:
                token = await asyncio.to_thread(ensure_valid_project_access_token)
        except FeishuOAuthError as exc:
            if "network error" in str(exc):
                return "飞书凭证刷新失败，请稍后重试。"
            token = ""
        if not token:
            url = await self._authorization_url()
            if url:
                return f"[打开飞书授权页]({url})。授权完成后，在当前 Web 会话重新发送查询。"
            return "飞书授权暂不可用，请检查本地 OAuth 回调和权限配置。"
        if token != current._uat:
            if await current.drain_inflight(timeout_s=0):
                return "飞书上一轮读取仍在收尾，请稍后重试。"
            replacement = _build_feishu_source_agent(
                current.cfg, retrieve.source_config, preset=retrieve.config.preset, uat=token,
            )
            retrieve.sources["feishu"] = replacement
            await current.aclose()
        return ""

    def _message(self, ctx, author, text, state_delta=None):
        return Event(
            invocation_id=ctx.invocation_id,
            branch=ctx.branch,
            author=author,
            content=types.Content(role="model", parts=[types.Part(text=text)]),
            actions=EventActions(state_delta=state_delta or {}),
        )

    async def _run_async_impl(self, ctx):
        retrieve, answer = self.sub_agents
        for agent, budget in ((retrieve, self.retrieve_timeout_s), (answer, self.answer_timeout_s)):
            is_retrieve = agent is retrieve
            loop = asyncio.get_running_loop()
            loop_thread = threading.get_ident()
            deadline = loop.time() + budget
            queue = asyncio.Queue()
            active = threading.Event()
            active.set()

            def publish(message, author=agent.name, queue=queue, active=active):
                if not active.is_set():
                    return

                def enqueue():
                    if active.is_set():
                        queue.put_nowait(self._message(ctx, author, message))
                if threading.get_ident() == loop_thread:
                    enqueue()
                else:
                    loop.call_soon_threadsafe(enqueue)

            async def forward():
                async with aclosing(agent.run_async(ctx)) as events:
                    async for event in events:
                        queue.put_nowait(event)
                        if ctx.should_pause_invocation(event):
                            return False
                return True

            async def run_stage():
                sink_token = progress_sink.set(publish)
                deadline_token = retrieval_deadline.set(deadline if is_retrieve else None)
                try:
                    async with asyncio.timeout_at(deadline):
                        if is_retrieve:
                            async with self._retrieve_lock:
                                if self.manage_feishu_oauth:
                                    oauth_message = await self._prepare_feishu(retrieve)
                                    if oauth_message:
                                        queue.put_nowait(self._message(ctx, retrieve.name, oauth_message, {
                                            "retrieval_result": json.dumps({"query": "", "chunks": []}),
                                            "retrieval_trace": json.dumps({"events": [{"event": "feishu_oauth_unavailable"}]}),
                                            "web_stage_timeout": None,
                                        }))
                                        return False
                                return await forward()
                        return await forward()
                finally:
                    progress_sink.reset(sink_token)
                    retrieval_deadline.reset(deadline_token)
                    queue.put_nowait(None)

            task = asyncio.create_task(run_stage())
            try:
                while (event := await queue.get()) is not None:
                    yield event
                if not await task:
                    return
            except TimeoutError:
                stage = "RetrieveAgent" if is_retrieve else "AnswerAgent"
                state = {"web_stage_timeout": stage}
                if is_retrieve:
                    parts = (ctx.user_content.parts or []) if ctx.user_content else []
                    query = "\n".join(part.text for part in parts if part.text).strip()
                    state["retrieval_result"] = json.dumps({"query": query, "chunks": []}, ensure_ascii=False)
                    state["retrieval_trace"] = json.dumps({"events": [{"event": "stage_timeout", "stage": stage}]})
                yield self._message(ctx, agent.name, f"{stage} 已超时（{budget:g} 秒）。", state)
                return
            finally:
                active.clear()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

            if is_retrieve:
                yield self._message(ctx, self.name, "Transfer to AnswerAgent")
