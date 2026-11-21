import unittest
import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agents.feishu.feishu_source_agent import FeishuSourceAgent
from agents.source_agent_base import Focus, SubQuery, _tiered_top_ids
from tools.core.focus_board import FocusBoard
from tools.core.final_assembler import render_final_payload, render_handoff_summary
from tools.core.models import ChunkEntry, RetrieveConfig, SourceAgentConfig
from tools.feishu.feishu_doc_pool import FeishuDocPool
from tools.core.retrieval_progress import progress_sink


class PlanEvalFinalizeTests(unittest.TestCase):
    def test_handoff_summary_uses_final_chunks_and_unique_document_focuses(self):
        chunks = [
            {"source_name": "Report", "source_url": "report", "focus_query": ["phase"], "text": "raw body"},
            {"source_name": "Report", "source_url": "report", "focus_query": ["phase", "metrics"]},
            {"source_name": "Report", "source_url": "another", "focus_query": []},
        ]
        self.assertEqual(
            render_handoff_summary(chunks),
            "最终证据链入选 3 条切片。\n\n- Report · Focus：phase；metrics\n- Report · Focus：未标注",
        )

    def test_uses_existing_critical_and_important_judgments_only(self):
        pool = {
            "api-1": ChunkEntry(id="api-1", source="feishu", content="a"),
            "api-2": ChunkEntry(id="api-2", source="feishu", content="b"),
            "api-3": ChunkEntry(id="api-3", source="feishu", content="c"),
            "api-4": ChunkEntry(id="api-4", source="feishu", content="d"),
        }
        tiers = {
            "api-1": {"tier": "important", "relevance": 0.9},
            "api-2": {"tier": "critical", "relevance": 0.1},
            "api-3": {"tier": "noise", "relevance": 1.0},
        }

        self.assertEqual(_tiered_top_ids(tiers, pool, 3), ["api-2", "api-1"])

    def test_preserves_native_order_for_equal_judgments(self):
        pool = {
            "api-2": ChunkEntry(id="api-2", source="feishu", content="b"),
            "api-1": ChunkEntry(id="api-1", source="feishu", content="a"),
        }
        tiers = {
            "api-2": {"tier": "critical", "relevance": 0.5},
            "api-1": {"tier": "critical", "relevance": 0.5},
        }

        self.assertEqual(_tiered_top_ids(tiers, pool, 2), ["api-2", "api-1"])

    def test_final_payload_contains_full_content_and_only_retained_evidence(self):
        chunks = [
            ChunkEntry(id="c1", source="feishu", content="FULL", preview_content="PREVIEW",
                       tier="important", target_focuses={"F1"}),
            ChunkEntry(id="c2", source="feishu", content="NOISE FULL", preview_content="NOISE",
                       tier="noise", target_focuses={"F1"}),
        ]
        payload = render_final_payload("q", chunks, [Focus("F1", "objective")])
        self.assertEqual([item["chunk_id"] for item in payload["chunks"]], ["c1"])
        self.assertEqual(payload["chunks"][0]["text"], "FULL")
        self.assertNotIn("preview_content", payload["chunks"][0])

    def test_background_does_not_inherit_the_retrieval_target(self):
        background = ChunkEntry(id="bg", source="feishu", content="背景", tier="important",
                                target_focuses={"F1"}, metadata={"answered_focuses": []})
        payload = render_final_payload("q", [background], [Focus("F1", "objective")])
        self.assertEqual(payload["chunks"][0]["focus_query"], [])


class FeishuRetrievalBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_read_invalidates_stop_without_another_search_round(self):
        pool = FeishuDocPool("fixture")
        agent = FeishuSourceAgent(
            cfg=SourceAgentConfig(max_loops=3, content_rerank_mode="off"), doc_pool=pool, uat="fixture",
        )
        focuses = [Focus("F1", "Nubia conversion evidence")]

        async def execute(_queries, chunks, *, loop_n):
            chunks["initial"] = ChunkEntry(id="initial", source="feishu", content="INITIAL", target_focuses={"F1"})
            pool._requested_docs["late-doc"] = None
            pool._doc_meta["late-doc"] = {"title": "Late document", "selected_focus_ids": ["F1"]}
            pool._doc_ready_events["late-doc"] = asyncio.Event()

        calls = []

        async def evaluate(_agent, prompt):
            calls.append(prompt)
            if len(calls) == 1:
                self.assertNotIn("LATE_TARGET", prompt)
                pool._doc_chunks["late-doc"] = [SimpleNamespace(
                    content="LATE_TARGET", doc_title="Late document", doc_url="", heading_path="Conversion",
                    content_type="text", metadata={},
                )]
                pool._doc_ready_events["late-doc"].set()
                ref = "C1"
            else:
                ref = re.search(r"chunk_ref=(C\d+)[^\n]*LATE_TARGET", prompt).group(1)
            return {
                "decision": "stop", "top_k_chunk_ids": [ref],
                "tiers": [{"chunk_id": ref, "tier": "critical", "relevance": 0.9, "answered_focuses": ["F1"]}],
                "focus_decisions": [{"focus_id": "F1", "status": "covered", "gap_type": "none"}],
            }

        with patch.object(agent, "_execute_batch", side_effect=execute) as search, patch(
            "agents.source_agent_base._run_llm_agent", side_effect=evaluate,
        ):
            result = await agent.run(focuses, pre_planned_sub_queries=[SubQuery("nubia", "F1", query_type="concept")])
        self.assertEqual(result.loops_used, 1)
        self.assertEqual(search.await_count, 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.top_chunks[0].content, "LATE_TARGET")
        self.assertTrue(any(e["event"] == "evaluation_snapshot_superseded" for e in agent._trace_events))

    async def test_repairs_stop_for_omitted_zero_candidate_focus_only(self):
        cfg = RetrieveConfig(preset="balanced").effective_config_for("feishu")
        agent = FeishuSourceAgent(cfg=cfg, uat="fixture")
        agent._profile_resolution = SimpleNamespace(
            fallback_pack=None,
            exact_entity_matches=[{"name": "Hypershell"}],
            exact_expression_matches=[{"name": "流量"}],
        )
        focuses = [Focus("F0", "Hypershell 站点流量与转化"), Focus("F1", "VOC 客群分层")]
        initial = [
            SubQuery("站点流量 转化率 渠道漏斗", "F0", query_type="structured", time_filter="last_30_days"),
            SubQuery("VOC 客群分层", "F1", query_type="concept"),
        ]
        messages = []
        token = progress_sink.set(messages.append)

        async def execute_fixture(_queries, pool, *, loop_n):
            fid = "F1" if loop_n == 1 else "F0"
            pool[f"c{loop_n}"] = ChunkEntry(id=f"c{loop_n}", source="feishu", content=fid, target_focuses={fid})
            agent._trace_events.extend([
                {"event": "candidate", "focus": fid, "document_id": f"doc-{fid}"},
                {"event": "read", "focuses": [fid], "document_id": f"doc-{fid}"},
            ])

        try:
            with (
                patch.object(agent, "_execute_batch", side_effect=execute_fixture) as execute,
                patch.object(agent, "_rerank_new_chunks", new_callable=AsyncMock),
                patch.object(agent, "_partial_eval_if_needed", new_callable=AsyncMock),
                patch.object(agent, "_eval_only_finalize", new_callable=AsyncMock, return_value=[]),
                patch("agents.source_agent_base._run_llm_agent", new_callable=AsyncMock) as judge,
            ):
                judge.side_effect = [
                    {"decision": "stop", "focus_decisions": [{"focus_id": "F1", "status": "covered"}]},
                    {"decision": "stop", "focus_decisions": [{"focus_id": fid, "status": "covered"} for fid in ("F0", "F1")]},
                ]
                result = await agent.run(focuses, pre_planned_sub_queries=initial)
        finally:
            progress_sink.reset(token)

        self.assertEqual(result.loops_used, 2)
        self.assertEqual([(q.target_focus, q.query, q.time_filter) for q in execute.await_args_list[1].args[0]],
                         [("F0", "Hypershell 流量", "last_30_days")])
        first_eval = next(e for e in result.retrieval_trace if e["event"] == "plan_eval")
        self.assertEqual((first_eval["raw_decision"], first_eval["decision"]), ("stop", "continue"))
        start = messages.index("feishu · 开始第 2 轮检索")
        self.assertIn("第 2 轮：检索到 1 份候选文档", messages[start + 1])
        self.assertTrue(any("F0：证据不足" in text and "F1：已满足" in text for text in messages))

    async def test_empty_pool_retries_once_without_running_eval(self):
        cfg = RetrieveConfig(preset="balanced").effective_config_for("feishu")
        agent = FeishuSourceAgent(cfg=cfg, uat="fixture")
        with (
            patch.object(agent, "_execute_batch", new_callable=AsyncMock) as execute,
            patch("agents.source_agent_base._run_llm_agent", new_callable=AsyncMock) as judge,
        ):
            result = await agent.run(
                [Focus("F0", "Hypershell 流量归因")],
                pre_planned_sub_queries=[SubQuery("Hypershell 流量 渠道 归因", "F0", query_type="structured")],
            )
        self.assertEqual(result.loops_used, 2)
        self.assertEqual(execute.await_args_list[1].args[0][0].query, "Hypershell 流量")
        judge.assert_not_awaited()
        first_eval = next(e for e in result.retrieval_trace if e["event"] == "plan_eval")
        self.assertIsNone(first_eval["raw_decision"])
        self.assertEqual(first_eval["decision_reason"], "zero_candidate_recovery")

    async def test_does_not_repeat_a_short_query_used_by_first_round_fallback(self):
        cfg = RetrieveConfig(preset="balanced").effective_config_for("feishu")
        agent = FeishuSourceAgent(cfg=cfg, uat="fixture")

        async def execute_fixture(_queries, _pool, *, loop_n):
            agent._trace_events.append({
                "event": "source_fallback", "focus": "F0",
                "api_query": "hypershell   流量", "fallback_doc_candidate_count": 0,
            })

        with patch.object(agent, "_execute_batch", side_effect=execute_fixture) as execute:
            result = await agent.run(
                [Focus("F0", "Hypershell 流量归因")],
                pre_planned_sub_queries=[SubQuery("Hypershell 流量 渠道 归因", "F0", query_type="structured")],
            )
        self.assertEqual(result.loops_used, 1)
        execute.assert_awaited_once()

    async def test_replan_visibility_and_second_round_model_stop_with_gap(self):
        cfg = RetrieveConfig(preset="balanced").effective_config_for("feishu")
        agent = FeishuSourceAgent(cfg=cfg, uat="fixture")
        focuses = [Focus("F1", "阶段依据"), Focus("F2", "成效"), Focus("F3", "社媒策略")]
        initial = [SubQuery(query="阶段与成效", target_focus="F1", query_type="concept")]
        messages = []
        token = progress_sink.set(messages.append)

        async def execute_fixture(queries, pool, *, loop_n):
            if loop_n == 1:
                pool["c1"] = ChunkEntry(id="c1", source="feishu", content="阶段结论", target_focuses={"F1"},
                                        metadata={"sub_query_ids": [queries[0].sub_query_id]})

        try:
            with (
                patch.object(agent, "_execute_batch", side_effect=execute_fixture) as execute,
                patch.object(agent, "_rerank_new_chunks", new_callable=AsyncMock),
                patch.object(agent, "_partial_eval_if_needed", new_callable=AsyncMock),
                patch.object(agent, "_eval_only_finalize", new_callable=AsyncMock, return_value=[]),
                patch("agents.source_agent_base._run_llm_agent", new_callable=AsyncMock) as judge,
            ):
                judge.side_effect = [
                    {
                        "decision": "continue",
                        "focus_decisions": [{"focus_id": "F1", "status": "Covered", "reason": "已有阶段实绩"},
                                            {"focus_id": "F2", "status": "gap"}],
                        "tiers": [{"chunk_id": "C1", "tier": "critical", "answered_focuses": ["F1"]}],
                        "replan_queries": [
                            {"query": "不应重复查阶段依据", "target_focus": "F1"},
                            {"query": "六月中旬 社媒 转化数据", "target_focus": "F2",
                             "query_type": "data", "time_filter": ">2026-06-10"},
                        ],
                    },
                    {"decision": "stop", "focus_decisions": [
                        {"focus_id": "F1", "status": "covered"},
                        {"focus_id": "F2", "status": "gap", "gap_type": "upstream_evidence_needed"},
                        {"focus_id": "F3", "status": "covered"},
                    ]},
                ]
                result = await agent.run(focuses, pre_planned_sub_queries=initial)
        finally:
            progress_sink.reset(token)

        self.assertEqual(result.loops_used, 2)
        submitted = execute.await_args_list[1].args[0]
        self.assertEqual([(sq.target_focus, sq.query_type, sq.time_filter) for sq in submitted],
                         [("F2", "structured", ">2026-06-10")])
        first_round = next(text for text in messages if "第 1 轮评估" in text)
        for text in ("F1：已满足", "F2：有缺口", "F3：未判定", "第 2 轮续查",
                     "F2 · 成效"):
            self.assertIn(text, first_round)
        for detail in ("不应重复查阶段依据", "六月中旬 社媒 转化数据", "[structured]", ">2026-06-10"):
            self.assertNotIn(detail, "\n".join(messages))
        second_prompt = judge.await_args_list[1].args[1]
        self.assertIn("阶段与成效", second_prompt)
        self.assertIn(submitted[0].query, second_prompt)
        self.assertIn('"query_group_supporting_chunks": 1', second_prompt)
        self.assertIn('"previous_focus_assessment": "已有阶段实绩"', second_prompt)
        self.assertTrue(any("第 2 轮评估：已满足 2/3" in text and "F2：有缺口" in text for text in messages))
        self.assertFalse(any("第 3 轮" in text for text in messages))
        first_eval, second_eval = [event for event in result.retrieval_trace if event["event"] == "plan_eval"]
        self.assertEqual(first_eval["submitted_replan_query_count"], 1)
        self.assertEqual(second_eval["raw_decision"], "stop")
        self.assertEqual(second_eval["decision_reason"], "no_executable_replan")
        self.assertEqual(second_eval["model_replan_query_count"], 0)
        replan_trace = next(event for event in result.retrieval_trace if event.get("replan"))
        self.assertEqual((replan_trace["api_query"], replan_trace["time_filter"]),
                         ("六月中旬 社媒 转化数据", ">2026-06-10"))

    async def test_balanced_can_execute_three_retrieval_batches(self):
        cfg = RetrieveConfig(preset="balanced").effective_config_for("feishu")
        self.assertEqual(cfg.max_loops, 3)
        agent = FeishuSourceAgent(cfg=cfg, uat="fixture")
        focuses = [Focus("F1", "objective")]
        plan = SubQuery(query="initial", target_focus="F1", query_type="concept")

        async def execute_fixture(_queries, pool, **_kwargs):
            pool["c1"] = ChunkEntry(id="c1", source="feishu", content="body", target_focuses={"F1"})

        with (
            patch.object(agent, "_execute_batch", side_effect=execute_fixture) as execute,
            patch.object(agent, "_rerank_new_chunks", new_callable=AsyncMock),
            patch.object(agent, "_partial_eval_if_needed", new_callable=AsyncMock),
            patch.object(agent, "_eval_only_finalize", new_callable=AsyncMock, return_value=[]),
            patch("agents.source_agent_base._run_llm_agent", new_callable=AsyncMock) as judge,
        ):
            judge.side_effect = [
                {"decision": "continue",
                 "focus_decisions": [{"focus_id": "F1", "status": "gap", "gap_type": "query_refinement"}],
                 "replan_queries": [
                    {"query": query, "target_focus": "F1", "query_type": "concept"},
                    {"query": "malformed row has no focus"},
                ]}
                for query in ("second", "third", "beyond budget")
            ]
            result = await agent.run(focuses, pre_planned_sub_queries=[plan])

        self.assertEqual(result.loops_used, 3)
        self.assertEqual([call.args[0][0].query for call in execute.await_args_list],
                         ["initial", "second", "third"])
        self.assertTrue(any(event.get("rejected_replan_queries") == 1 for event in result.retrieval_trace))

    async def test_model_stop_with_upstream_replan_runs_despite_provisional_quota(self):
        cfg = RetrieveConfig(preset="balanced").effective_config_for("feishu")
        agent = FeishuSourceAgent(cfg=cfg, uat="fixture")
        focus = Focus("F1", "nubia 最新转化率")
        agent._focus_board = FocusBoard.from_focuses([focus], quota={"critical_min": 2})
        await agent._focus_board.report("feishu", [
            ChunkEntry(id=f"old-{n}", source="feishu", content="旧资料", tier="critical", target_focuses={"F1"})
            for n in (1, 2)
        ])
        initial = SubQuery("nubia 产品", "F1", query_type="concept", time_filter="last_30_days",
                           sub_query_id="feishu:F1:sq1")

        async def execute_fixture(queries, pool, *, loop_n):
            sq = queries[0]
            pool[f"c{loop_n}"] = ChunkEntry(id=f"c{loop_n}", source="feishu", content="资料", target_focuses={"F1"})
            agent._trace_events.append({"event": "candidate", "query_id": sq.sub_query_id,
                                        "focus": "F1", "document_id": f"doc{loop_n}"})

        with (
            patch.object(agent, "_execute_batch", side_effect=execute_fixture) as execute,
            patch.object(agent, "_rerank_new_chunks", new_callable=AsyncMock),
            patch.object(agent, "_partial_eval_if_needed", new_callable=AsyncMock),
            patch("agents.source_agent_base._run_llm_agent", new_callable=AsyncMock) as judge,
        ):
            judge.side_effect = [
                {"decision": "stop",
                 "focus_decisions": [{"focus_id": "F1", "status": "gap", "gap_type": "upstream_evidence_needed"}],
                 "replan_queries": [{"target_focus": "F1", "query": "nubia 转化报表", "query_type": "structured"}]},
                {"decision": "stop", "focus_decisions": [{"focus_id": "F1", "status": "covered"}],
                 "top_k_chunk_ids": ["c2"], "tiers": [{"chunk_id": "c2", "tier": "critical", "answered_focuses": ["F1"]}]},
            ]
            result = await agent.run([focus], pre_planned_sub_queries=[initial])

        submitted = execute.await_args_list[1].args[0][0]
        self.assertEqual((submitted.query, submitted.time_filter), ("nubia 转化报表", "last_30_days"))
        self.assertNotEqual(submitted.sub_query_id, initial.sub_query_id)
        self.assertIn('"query_group_candidate_documents": 1', judge.await_args_list[1].args[1])
        self.assertEqual([chunk.id for chunk in result.top_chunks], ["c2"])
        event = next(e for e in result.retrieval_trace if e["event"] == "plan_eval")
        self.assertEqual((event["raw_decision"], event["decision"]), ("stop", "continue"))
        self.assertEqual(event["decision_reason"], "replan_overrides_model_decision")
        self.assertTrue(event["io_saturated"])
        self.assertIn('"query_group_candidate_documents": 1', judge.await_args_list[0].args[1])
        self.assertIn('"time_filter": "last_30_days"', judge.await_args_list[0].args[1])

    async def test_missing_replan_finalizes_existing_focus_evidence_without_another_model_call(self):
        cfg = RetrieveConfig(preset="balanced", enabled_sources=["feishu"]).effective_config_for("feishu")
        agent = FeishuSourceAgent(cfg=cfg, uat="fixture")
        focuses = [Focus("F0", "Hypershell 流量"), Focus("F1", "nubia 转化")]
        pool_rows = {f"h{n}": "F0" for n in range(13)} | {"nubia": "F1"}

        async def execute_fixture(_queries, pool, **_kwargs):
            for cid, fid in pool_rows.items():
                pool[cid] = ChunkEntry(id=cid, source="feishu", content=cid, target_focuses={fid})

        with (
            patch.object(agent, "_execute_batch", side_effect=execute_fixture) as execute,
            patch.object(agent, "_rerank_new_chunks", new_callable=AsyncMock),
            patch.object(agent, "_partial_eval_if_needed", new_callable=AsyncMock),
            patch("agents.source_agent_base._run_llm_agent", new_callable=AsyncMock) as judge,
        ):
            judge.return_value = {
                "decision": "continue", "replan_queries": [],
                "focus_decisions": [
                    {"focus_id": "F0", "status": "gap", "gap_type": "query_refinement"},
                    {"focus_id": "F1", "status": "covered", "gap_type": "none",
                     "reason": "虽缺乏定量 ROI 复盘，但足以提供建议"},
                ],
                "top_k_chunk_ids": list(pool_rows)[:-1],
                "tiers": [{"chunk_id": cid, "tier": "critical", "answered_focuses": [fid]}
                          for cid, fid in pool_rows.items()],
            }
            result = await agent.run(focuses, pre_planned_sub_queries=[
                SubQuery("Hypershell 流量", "F0"), SubQuery("nubia 转化", "F1"),
            ])
        judge.assert_awaited_once()
        execute.assert_awaited_once()
        self.assertEqual([chunk.id for chunk in result.top_chunks], [*list(pool_rows)[:11], "nubia"])
        event = next(e for e in result.retrieval_trace if e["event"] == "plan_eval")
        self.assertEqual((event["raw_decision"], event["decision"]), ("continue", "stop"))
        self.assertEqual(event["decision_reason"], "no_executable_replan")
        self.assertEqual(event["focus_decisions"][1]["status"], "covered")
        self.assertTrue(any(e.get("error_type") == "continue_without_executable_replan" for e in result.retrieval_trace))

    async def test_initial_query_cap_is_enforced_for_feishu(self):
        cfg = RetrieveConfig(preset="balanced").effective_config_for("feishu")
        agent = FeishuSourceAgent(cfg=cfg, uat="fixture")
        agent._trace_events = []
        rows = [SubQuery(f"brand anchor{i}", "F0", query_type="structured") for i in range(4)]
        kept = agent._post_process_sub_queries([Focus("F0", "brand evidence")], rows)
        self.assertEqual([q.query for q in kept], ["brand anchor0", "brand anchor1"])

    async def test_replan_cap_keeps_two_new_queries_after_dedup(self):
        agent = FeishuSourceAgent(cfg=RetrieveConfig(preset="balanced").effective_config_for("feishu"), uat="fixture")
        batches = []

        async def execute(queries, pool, **_kwargs):
            batches.append([q.query for q in queries])
            pool["c1"] = ChunkEntry(id="c1", source="feishu", content="evidence", target_focuses={"F0"})

        first = {
            "decision": "continue", "replan_queries": [
                {"query": query, "target_focus": "F0", "query_type": "structured"}
                for query in ("brand old", "brand new0", "brand new1", "brand new2")
            ],
            "focus_decisions": [{"focus_id": "F0", "status": "gap", "gap_type": "query_refinement"}],
        }
        last = {
            "decision": "stop", "replan_queries": [], "top_k_chunk_ids": ["C1"],
            "tiers": [{"chunk_id": "C1", "tier": "important", "answered_focuses": ["F0"]}],
            "focus_decisions": [{"focus_id": "F0", "status": "covered", "gap_type": "none"}],
        }
        with patch.object(agent, "_execute_batch", side_effect=execute), patch.object(
            agent, "_rerank_new_chunks", new_callable=AsyncMock,
        ), patch("agents.source_agent_base._run_llm_agent", side_effect=[first, last]):
            result = await agent.run([Focus("F0", "brand evidence")], pre_planned_sub_queries=[
                SubQuery("brand old", "F0", query_type="structured"),
            ])
        self.assertEqual(result.loops_used, 2)
        self.assertEqual(batches, [["brand old"], ["brand new0", "brand new1"]])
