"""Offline UI fixture for ADK timeline, handoff and deadline checks."""

import asyncio
import json
import sys
from pathlib import Path

from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from google.genai import types

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from web_retrieval_workflow import WebRetrievalWorkflow
from tools.core.final_assembler import render_handoff_summary
from agents.source_agent_base import Focus, SubQuery, _report_focus_status


class FixtureStage(BaseAgent):
    retrieval: bool = False

    async def _run_async_impl(self, ctx):
        query = ctx.user_content.parts[0].text.strip()

        def message(text, state=None):
            return Event(
                invocation_id=ctx.invocation_id, author=self.name,
                content=types.Content(role="model", parts=[types.Part(text=text)]),
                actions=EventActions(state_delta=state or {}),
            )

        if not self.retrieval:
            if query == "回答超时":
                await asyncio.sleep(5)
            payload = json.loads(ctx.session.state["retrieval_result"])
            yield message("合成证据显示目标为 42。[①] 来源：测试文档" if payload["chunks"] else "没有足够证据。")
            return

        yield message("允许检索源：飞书（最多 3 轮） · balanced · 合成调试")
        if query == "检索超时":
            await asyncio.sleep(5)
        yield message('已成功加载业务理解profile: "Hypershell"')
        yield message("检索规划\n\n- focus_1 · 查目标值 · feishu")
        max_rounds = 3 if query == "三轮" else 1
        focuses = [Focus("focus_1", "查目标值")]
        for round_number in range(1, max_rounds + 1):
            yield message(f"feishu · 开始第 {round_number} 轮检索")
            await asyncio.sleep(0.2)
            if query == "无结果":
                yield message(f"feishu · 第 {round_number} 轮：检索到 0 份候选文档，筛选后读取 0 份。")
                _report_focus_status("feishu", round_number, focuses, {"focus_1": "insufficient"})
                break
            yield message(f"feishu · 第 {round_number} 轮：检索到 1 份候选文档，筛选后读取 1 份。")
            yield message("feishu · 检查证据是否充分，并筛选证据链…（source_plan_eval）")
            more = round_number < max_rounds
            _report_focus_status(
                "feishu", round_number, focuses, {"focus_1": "gap" if more else "covered"},
                [SubQuery("合成目标数据", "focus_1", query_type="structured")] if more else None,
            )
        yield message("整理最终证据链…")
        chunks = [] if query == "无结果" else [{
            "text": "目标为 42。", "source_name": "测试文档", "source_url": "",
            "source_tool": "feishu", "heading_path": "", "confidence_tier": "critical",
            "focus_query": ["查目标值"], "sub_query": "合成目标", "chunk_id": "fixture:1",
        }]
        trace = [{"event": "fixture", "scenario": query}]
        if query == "降级":
            trace.append({"event": "rerank_error", "fail_open": True})
        yield message(render_handoff_summary(chunks), {
            "retrieval_result": json.dumps({"query": query, "chunks": chunks}, ensure_ascii=False),
            "retrieval_trace": json.dumps({"events": trace}, ensure_ascii=False),
        })


root_agent = WebRetrievalWorkflow(
    name="rag_timeline_fixture",
    sub_agents=[FixtureStage(name="retrieve_v1", retrieval=True), FixtureStage(name="answer")],
    retrieve_timeout_s=2,
    answer_timeout_s=2,
)
