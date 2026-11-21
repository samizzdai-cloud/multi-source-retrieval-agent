import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from google.adk.runners import InMemoryRunner
from google.genai import types

from agents.feishu.feishu_source_agent import FeishuSourceAgent
from agents.source_agent_base import Focus, SubQuery
from retrieve_agent import RetrieveAgent
from tools.core.models import ChunkEntry, RetrieveConfig
from tools.core.planner_runner import NormalizedPlan
from tools.core.retrieval_progress import progress_sink, report_progress
from web_retrieval_workflow import WebRetrievalWorkflow


class FixtureStage(BaseAgent):
    delay: float = 0.0
    retrieval: bool = False

    async def _run_async_impl(self, ctx):
        if self.retrieval:
            await asyncio.to_thread(report_progress, "fixture progress")
        await asyncio.sleep(self.delay)
        state = {}
        if self.retrieval:
            state["retrieval_result"] = json.dumps({"query": "fixture", "chunks": [{"text": "42"}]})
        else:
            assert json.loads(ctx.session.state["retrieval_result"])["chunks"][0]["text"] == "42"
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            content=types.Content(role="model", parts=[types.Part(text=self.name)]),
            actions=EventActions(state_delta=state),
        )


class WebRetrievalWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_round_reports_distinct_documents_before_evaluation_and_handoff(self):
        config = RetrieveConfig(preset="balanced", enabled_sources=["feishu"])
        source = FeishuSourceAgent(cfg=config.effective_config_for("feishu"), uat="fixture")
        retrieve = RetrieveAgent(name="retrieve", config=config, sources={"feishu": source})
        app = WebRetrievalWorkflow(name="web_test", sub_agents=[retrieve, FixtureStage(name="answer")])
        runner = InMemoryRunner(agent=app, app_name="web_test")
        session = await runner.session_service.create_session(app_name="web_test", user_id="test")
        plan = NormalizedPlan(
            focuses=[Focus("focus_0", "Hypershell 流量", ["feishu"]), Focus("focus_1", "nubia 转化", ["feishu"])],
            pre_planned_by_source={"feishu": [SubQuery("Hypershell 流量", "focus_0"), SubQuery("nubia 产品", "focus_1")]},
        )

        async def execute_fixture(queries, pool, *, loop_n):
            fid = "focus_0" if loop_n == 1 else "focus_1"
            cid = f"c{loop_n}"
            pool[cid] = ChunkEntry(id=cid, source="feishu", content="42", target_focuses={fid},
                                   metadata={"source_name": f"Doc {loop_n}"})
            source._trace_events.extend([
                {"event": "candidate", "focus": fid, "document_id": cid},
                {"event": "candidate", "focus": fid, "document_id": cid, "fallback_origin": "original_surface"},
                {"event": "read", "focuses": [fid], "document_id": cid},
                {"event": "read", "focuses": [fid], "document_id": cid},
            ])
            if loop_n == 2:
                self.assertEqual([q.target_focus for q in queries], ["focus_1"])
                source._trace_events.append({"event": "candidate", "focus": fid, "document_id": "unread"})

        with (
            patch("retrieve_agent._prepare_plan", return_value=plan),
            patch("retrieve_agent.ProfileGovernanceAgent") as governance,
            patch.object(source, "_execute_batch", side_effect=execute_fixture),
            patch.object(source, "_rerank_new_chunks", new_callable=AsyncMock),
            patch.object(source, "_partial_eval_if_needed", new_callable=AsyncMock),
            patch("agents.source_agent_base._run_llm_agent", new_callable=AsyncMock) as judge,
        ):
            governance.return_value.observe_events = AsyncMock(return_value={"event_counts": {}})
            judge.side_effect = [
                {"decision": "continue", "focus_decisions": [
                    {"focus_id": "focus_0", "status": "covered"},
                    {"focus_id": "focus_1", "status": "gap", "gap_type": "upstream_evidence_needed"},
                ], "replan_queries": [{"query": "nubia 转化报表", "target_focus": "focus_1"}]},
                {"decision": "stop", "focus_decisions": [
                    {"focus_id": fid, "status": "covered"} for fid in ("focus_0", "focus_1")
                ], "top_k_chunk_ids": ["c1"], "tiers": [
                    {"chunk_id": cid, "tier": "critical", "answered_focuses": [fid]}
                    for cid, fid in (("c1", "focus_0"), ("c2", "focus_1"))
                ]},
            ]
            events = [event async for event in runner.run_async(
                user_id="test", session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text="fixture")]),
            )]
        messages = [part.text for event in events if event.content for part in event.content.parts if part.text]
        sequence = ["第 2 轮续查", "开始第 2 轮检索", "第 2 轮：检索到 2 份候选文档，筛选后读取 1 份",
                    "第 2 轮评估：已满足 2/2", "Doc 2 · Focus：nubia 转化", "Transfer to AnswerAgent"]
        positions = [next(i for i, message in enumerate(messages) if marker in message) for marker in sequence]
        self.assertEqual(positions, sorted(set(positions)))
        web_text = "\n".join(messages)
        self.assertIn("source_plan_eval", web_text)
        self.assertIn("第 2 轮续查\n- focus_1 · nubia 转化", web_text)
        self.assertNotIn("nubia 转化报表", web_text)

    async def test_initial_planning_shows_focuses_without_internal_subqueries(self):
        config = RetrieveConfig(preset="balanced", enabled_sources=["feishu"])
        source = FeishuSourceAgent(cfg=config.effective_config_for("feishu"), uat="fixture")
        agent = RetrieveAgent(name="retrieve", config=config, sources={"feishu": source})
        runner = InMemoryRunner(agent=agent, app_name="web_test")
        session = await runner.session_service.create_session(app_name="web_test", user_id="test")
        sub_query = SubQuery("internal search terms", "focus_0", query_type="concept")
        planned = NormalizedPlan(
            focuses=[Focus("focus_0", "Customer segmentation", ["feishu"]),
                     Focus("focus_1", "Marketing policy", ["feishu"])],
            pre_planned_by_source={"feishu": [sub_query]},
        )
        messages = []
        token = progress_sink.set(messages.append)
        try:
            with (
                patch("retrieve_agent._prepare_plan", return_value=planned),
                patch("retrieve_agent._dispatch_sources", new_callable=AsyncMock, return_value=[]) as dispatch,
                patch("retrieve_agent.ProfileGovernanceAgent") as governance,
            ):
                governance.return_value.observe_events = AsyncMock(return_value={"event_counts": {}})
                events = [event async for event in runner.run_async(
                    user_id="test", session_id=session.id,
                    new_message=types.Content(role="user", parts=[types.Part(text="fixture")]),
                )]
        finally:
            progress_sink.reset(token)
        text = "\n".join(part.text or "" for event in events if event.content for part in event.content.parts)
        self.assertIn("focus_0 · Customer segmentation · feishu", text)
        self.assertIn("focus_1 · Marketing policy · feishu", text)
        self.assertNotIn(sub_query.query, text)
        self.assertNotIn("[concept]", text)
        self.assertEqual(dispatch.await_args.kwargs["pre_planned_by_source"], {"feishu": [sub_query]})

    async def run_web(self, retrieve_delay, answer_delay, budget, answer_budget=None):
        app = WebRetrievalWorkflow(name="web_test", sub_agents=[
            FixtureStage(name="retrieve", delay=retrieve_delay, retrieval=True),
            FixtureStage(name="answer", delay=answer_delay),
        ], retrieve_timeout_s=budget, answer_timeout_s=answer_budget or budget)
        runner = InMemoryRunner(agent=app, app_name="web_test")
        session = await runner.session_service.create_session(app_name="web_test", user_id="test")
        events = [event async for event in runner.run_async(
            user_id="test", session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="fixture")]),
        ) if event.author != "user"]
        return [event.content.parts[0].text for event in events], events[-1].actions.state_delta

    async def test_independent_deadlines_and_state_handoff(self):
        messages, _ = await self.run_web(0.72, 0.72, 1.2)
        self.assertEqual(messages, ["fixture progress", "retrieve", "Transfer to AnswerAgent", "answer"])

    async def test_retrieval_timeout_prevents_answer_and_clears_evidence(self):
        messages, state = await self.run_web(2, 0, 0.2)
        self.assertEqual(messages[0], "fixture progress")
        self.assertNotIn("Transfer to AnswerAgent", messages)
        self.assertEqual(state["web_stage_timeout"], "RetrieveAgent")
        self.assertEqual(json.loads(state["retrieval_result"])["chunks"], [])

    async def test_answer_timeout_follows_handoff(self):
        messages, state = await self.run_web(0, 2, 0.5, answer_budget=0.1)
        self.assertIn("Transfer to AnswerAgent", messages)
        self.assertEqual(state["web_stage_timeout"], "AnswerAgent")

    async def test_new_token_rebuilds_source_once_without_restarting(self):
        app = WebRetrievalWorkflow(name="web_test")
        previous = SimpleNamespace(_uat="old", cfg=object(), drain_inflight=AsyncMock(return_value=0), aclose=AsyncMock())
        fresh = SimpleNamespace(_uat="new")
        retrieve = SimpleNamespace(sources={"feishu": previous}, source_config=object(), config=SimpleNamespace(preset="balanced"))
        token_state = SimpleNamespace(access_expires_at=0)
        with (
            patch("web_retrieval_workflow.load_token_state", return_value=token_state),
            patch("web_retrieval_workflow.ensure_valid_project_access_token", return_value="new"),
            patch("web_retrieval_workflow._build_feishu_source_agent", return_value=fresh) as build,
        ):
            self.assertEqual(await app._prepare_feishu(retrieve), "")
            self.assertEqual(await app._prepare_feishu(retrieve), "")
        self.assertIs(retrieve.sources["feishu"], fresh)
        previous.aclose.assert_awaited_once()
        build.assert_called_once()
