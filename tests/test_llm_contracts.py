import asyncio
import json
import unittest
import time
from unittest.mock import AsyncMock, patch

from agents.source_agent_base import (
    ChunkRefRegistry, Focus, SourceAgent, _build_partial_eval_prompt,
    _build_plan_eval_prompt,
)
from tools.core.llm_contracts import (
    build_partial_eval_model, tiers_list_to_dict, tiers_list_to_map, validate_plan_eval_output,
)
from tools.core.models import ChunkEntry, EvalShortlistConfig, SourceAgentConfig
from tools.core.focus_board import FocusBoard
from tools.core.rerank import RankedItem, RerankError, RerankSettings


class TierNormalizationTests(unittest.TestCase):
    def test_replanning_keeps_focus_and_receives_source_rules_and_budget(self):
        from agents.feishu.feishu_source_agent import FeishuSourceAgent
        from tools.core.models import RetrieveConfig

        config = RetrieveConfig(preset="balanced", enabled_sources=["feishu"])
        source = FeishuSourceAgent(cfg=config.effective_config_for("feishu"), uat="fixture")
        focus = Focus("focus_0", "核实品牌当前站点状态并寻找增长依据", ["feishu", "bailian"])
        prompt = _build_plan_eval_prompt(
            [focus], [], loop_n=2, min_loops=1, max_loops=3, source_top_k=12,
            queries_per_focus=source.cfg.sub_queries_per_focus,
            source_query_rules=source._source_query_rules_block(),
        )
        self.assertIn(focus.text, prompt)
        self.assertIn("replan_queries_per_focus_limit: 2", prompt)
        self.assertIn("Feishu 专属规则", prompt)
        self.assertNotIn("具体时间点请并入 query", prompt)
        self.assertEqual(source._compute_sub_query_total_cap(4), 8)
        self.assertEqual(focus.targets, ["feishu", "bailian"])

    def test_feishu_plan_eval_requires_query_array_for_both_decisions(self):
        from agents.feishu.feishu_retrieve_llm_build import build_feishu_plan_eval_agent

        agent = build_feishu_plan_eval_agent(12)
        self.assertIn("replan_queries", agent.output_schema.model_json_schema()["required"])
        self.assertIn('decision=continue', agent.instruction)
        self.assertIn('replan_queries_per_focus_limit', agent.instruction)

    def test_malformed_tier_items_are_ignored_without_aborting_evaluation(self):
        tiers = [
            "critical",
            {"chunk_id": "c1", "tier": "important", "relevance": 0.8},
            None,
            {"tier": "noise"},
        ]
        self.assertEqual(tiers_list_to_dict(tiers), {"c1": "important"})
        self.assertEqual(
            tiers_list_to_map(tiers),
            {"c1": {"tier": "important", "relevance": 0.8,
                    "answered_focuses": [], "angle_tag": ""}},
        )

    def test_partial_eval_uses_request_local_refs_and_preview_only(self):
        chunk = ChunkEntry(
            id="feishu:doc:block-1", source="feishu",
            content="full body that must stay downstream",
            preview_content="controlled preview", target_focuses={"F1"},
        )
        registry = ChunkRefRegistry.build([chunk])
        prompt = _build_partial_eval_prompt(
            [Focus("F1", "verify evidence")], [chunk], "batch-1", refs=registry,
        )
        self.assertIn("chunk_ref=C1", prompt)
        self.assertIn("controlled preview", prompt)
        self.assertNotIn("full body that must stay downstream", prompt)
        resolved, malformed = registry.resolve_eval_output({
            "focus_groups": [{"focus_id": "F1", "tiers": [
                {"chunk_id": "C1", "tier": "important"},
            ], "reason": ""}],
        })
        self.assertEqual(malformed, 0)
        self.assertEqual(resolved["partial_tiers"][0]["chunk_id"], chunk.id)
        self.assertEqual(resolved["partial_tiers"][0]["tier"], "important")
        self.assertEqual(resolved["partial_tiers"][0]["focus_id"], "F1")

    def test_partial_prompt_keeps_observed_query_lineage_audit_only(self):
        chunks = [
            ChunkEntry(id="c1", source="feishu", content="a", preview_content="a",
                       sub_query="Meta country performance", target_focuses={"F1"}),
            ChunkEntry(id="c2", source="feishu", content="b", preview_content="b",
                       sub_query="Store conversion outcome", target_focuses={"F1"}),
        ]
        prompt = _build_partial_eval_prompt([Focus("F1", "budget decision")], chunks, "batch-1")
        self.assertIn("observed_query_lineage_block", prompt)
        self.assertIn("Meta country performance | Store conversion outcome", prompt)
        self.assertIn("audit context only; never a grouping or retention rule", prompt)
        self.assertIn("do not group chunks by query", prompt)
        self.assertIn("different headings, tables, metrics, countries, time slices, or documents", prompt)
        self.assertIn("direct recommendation, caution, or diagnosis", prompt)
        self.assertIn("the recommendation is not a duplicate", prompt)
        self.assertIn("recall-first admission, not a precision ranking", prompt)
        self.assertIn("never only representative examples", prompt)
        self.assertIn("unselected by this provisional admission", prompt)
        self.assertIn("not a semantic noise verdict", prompt)

    def test_plan_eval_ledger_uses_request_local_refs_not_canonical_ids(self):
        chunk = ChunkEntry(id="feishu:private:canonical", source="feishu", content="full", preview_content="preview")
        registry = ChunkRefRegistry.build([chunk])
        prompt = _build_plan_eval_prompt(
            [Focus("F1", "objective")], [chunk], 2, 1, 2, 6,
            partial_ledgers=[{
                "focus_id": "F1",
                "evidence_chunk_ids": [chunk.id],
                "tier_by_chunk_id": {chunk.id: "critical"},
                "tier_counts": {"critical": 1, "important": 0},
            }],
            refs=registry,
        )
        self.assertIn('"evidence_chunk_refs": ["C1"]', prompt)
        self.assertIn('"provisional_tier_by_ref": {"C1": "critical"}', prompt)
        self.assertNotIn(chunk.id, prompt)

    def test_plan_eval_prompt_uses_focus_not_io_progress_as_coverage_contract(self):
        prompt = _build_plan_eval_prompt(
            [Focus("F1", "decide the change using the current baseline and observed outcome")],
            [], 2, 1, 2, 6,
        )
        self.assertIn("Evidence analysis framework", prompt)
        self.assertIn("never proves coverage", prompt)
        self.assertIn("Entity alignment", prompt)
        self.assertIn("Cognitive level", prompt)
        self.assertNotIn("focus_status", prompt)

    def test_focus_board_uses_partial_tiers_for_io_gate_without_double_counting(self):
        board = FocusBoard.from_focuses([Focus("F1", "objective")], {
            "critical_min": 2, "important_min": 0,
        })
        chunks = [
            ChunkEntry("c1", "feishu", "a", metadata={"partial_tier_by_focus": {"F1": "critical"}}),
            ChunkEntry("c2", "feishu", "b", metadata={"partial_tier_by_focus": {"F1": "critical"}}),
        ]

        async def exercise():
            await board.report("feishu", chunks)
            await board.report("feishu", chunks)

        asyncio.run(exercise())
        state = board.state("F1")
        self.assertIsNotNone(state)
        self.assertTrue(board.is_satisfied("F1"))
        self.assertEqual(state.critical, 2)

    def test_partial_tiers_are_focus_scoped_and_do_not_overwrite_final_tiers(self):
        chunk = ChunkEntry(id="feishu:d:b", source="feishu", content="full", preview_content="preview")
        registry = ChunkRefRegistry.build([chunk])
        resolved, malformed = registry.resolve_eval_output({
            "focus_groups": [{"focus_id": "F1", "tiers": [
                {"chunk_id": "C1", "tier": "critical"},
            ], "reason": ""}],
        })
        self.assertEqual(malformed, 0)
        self.assertEqual(resolved["focus_groups"][0]["tiers"][0]["chunk_id"], chunk.id)
        self.assertEqual(resolved["partial_tiers"][0]["tier"], "critical")
        self.assertEqual(resolved["tiers"], [])

    def test_partial_singleton_objects_are_normalized_without_inventing_evidence(self):
        chunk = ChunkEntry(id="feishu:d:b", source="feishu", content="full", preview_content="preview")
        registry = ChunkRefRegistry.build([chunk])
        resolved, malformed = registry.resolve_eval_output({
            "focus_groups": {
                "focus_id": "F1",
                "tiers": {"chunk_id": "C1", "tier": "important"},
                "reason": "",
            },
        })
        self.assertEqual(malformed, 0)
        self.assertEqual(resolved["partial_tiers"], [{
            "chunk_id": chunk.id, "tier": "important", "focus_id": "F1",
        }])
        self.assertEqual(
            resolved["partial_contract_normalizations"],
            ["focus_groups_object_to_list", "focus_group_tiers_object_to_list"],
        )

    def test_plan_eval_normalizes_provider_gap_alias_to_upstream(self):
        payload = validate_plan_eval_output({
            "decision": "stop", "tiers": [], "top_k_chunk_ids": [],
            "focus_decisions": [{
                "focus_id": "F1", "status": "partially_covered",
                "gap_type": "evidence_gap",
                "reason": "缺乏市场需求变化的定量证据，无法评估影响",
            }],
        })
        self.assertEqual(payload["focus_decisions"][0]["status"], "gap")
        self.assertEqual(
            payload["focus_decisions"][0]["gap_type"], "upstream_evidence_needed",
        )
        self.assertEqual(payload["contract_normalizations"][0]["raw_status"], "partially_covered")
        self.assertEqual(payload["contract_normalizations"][0]["raw_gap_type"], "evidence_gap")

    def test_partial_eval_schema_requires_focus_groups_but_has_no_chunk_quota(self):
        model = build_partial_eval_model()
        with self.assertRaises(Exception):
            model.model_validate({"focus_ledger": []})
        valid = model.model_validate({"focus_groups": [{
            "focus_id": "F1",
            "tiers": [{"chunk_id": f"C{i}", "tier": "important"} for i in range(100)],
            "reason": "",
        }]})
        self.assertEqual(valid.focus_groups[0].focus_id, "F1")
        self.assertEqual(len(valid.focus_groups[0].tiers), 100)

    def test_plan_eval_resolves_covered_but_upstream_needed_contradiction(self):
        payload = validate_plan_eval_output({
            "decision": "stop", "tiers": [], "top_k_chunk_ids": [],
            "focus_decisions": [{
                "focus_id": "F1", "status": "covered", "gap_type": "none",
                "reason": "No chunks provide verifiable impact evidence. Further upstream data is needed.",
            }],
        })
        self.assertEqual(payload["focus_decisions"][0]["status"], "gap")
        self.assertEqual(payload["focus_decisions"][0]["gap_type"], "upstream_evidence_needed")

    def test_generic_missing_reason_does_not_silently_become_upstream(self):
        payload = validate_plan_eval_output({
            "decision": "stop", "tiers": [], "top_k_chunk_ids": [],
            "focus_decisions": [{
                "focus_id": "F1", "status": "gap", "gap_type": "evidence_gap",
                "reason": "The retrieved pages are missing the requested product name.",
            }],
        })
        self.assertEqual(payload["focus_decisions"][0]["gap_type"], "query_refinement")


class PartialTierGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_eval_reduces_oversized_input_in_bounded_batches(self):
        agent = SourceAgent.__new__(SourceAgent)
        agent.source = "test"
        agent.cfg = SourceAgentConfig(eval_shortlist=EvalShortlistConfig(input_token_budget=10000))
        agent._effective_source_top_k = 1
        agent._current_focus_lookup = {"F1": Focus("F1", "objective")}
        agent._trace_events = []
        agent.eval_only = object()
        agent._request_token_estimate = lambda _agent, prompt: prompt.count("- chunk_ref=") * 1000 + 100
        pool = {str(i): ChunkEntry(id=str(i), source="test", content=f"EVIDENCE_{i}", target_focuses={"F1"}) for i in range(10)}
        seen = []

        async def evaluate(_agent, prompt):
            seen.append(prompt)
            return {"top_k_chunk_ids": ["C1"], "tiers": [{
                "chunk_id": "C1", "tier": "important", "relevance": 0.9, "answered_focuses": ["F1"],
            }]}

        with patch("agents.source_agent_base._run_llm_agent", side_effect=evaluate):
            selected = await agent._eval_only_snapshot([Focus("F1", "objective")], pool)
        self.assertEqual(len(selected), 1)
        self.assertTrue(all(agent._request_token_estimate(None, prompt) <= 10000 for prompt in seen))
        for i in range(10):
            self.assertTrue(any(f"EVIDENCE_{i}" in prompt for prompt in seen))
        self.assertTrue(any(e["event"] == "source_eval_budget_reduction" for e in agent._trace_events))

    async def test_many_short_chunks_do_not_trigger_partial_by_count(self):
        agent = SourceAgent.__new__(SourceAgent)
        agent.source = "test"
        agent.cfg = SourceAgentConfig()
        agent.partial_eval = object()
        agent._partial_pending_tasks = []
        agent._partial_submitted_ids = set()
        pool = {str(i): ChunkEntry(id=str(i), source="test", content="short", target_focuses={"F1"}) for i in range(241)}
        with patch("agents.source_agent_base._run_llm_agent", new_callable=AsyncMock) as evaluate:
            await agent._partial_eval_if_needed([Focus("F1", "objective")], pool, loop_n=1)
        evaluate.assert_not_awaited()
        self.assertEqual(len(pool), 241)

    async def test_partial_provider_json_error_fails_open_without_aborting_source(self):
        agent = SourceAgent.__new__(SourceAgent)
        agent.source = "test"
        agent.cfg = SourceAgentConfig(eval_shortlist=EvalShortlistConfig(input_token_budget=3000))
        agent._trace_events = []
        agent._partial_ledgers = []
        agent._partial_pending_tasks = []
        agent._partial_buffer_ids = []
        agent._partial_buffer_tokens = 0
        agent._partial_submitted_ids = set()
        agent._partial_generation = 0
        agent.partial_eval = object()
        agent._partial_prompt_tokens = lambda _focuses, _chunks: agent._context_budget().flush_threshold
        pool = {
            f"c{i}": ChunkEntry(id=f"c{i}", source="test", content=f"body {i}",
                                 preview_content=f"preview {i}", target_focuses={"F1"})
            for i in range(2)
        }
        with patch(
            "agents.source_agent_base._run_llm_agent",
            new=AsyncMock(side_effect=json.JSONDecodeError("Expecting value", "not json", 0)),
        ):
            await agent._partial_eval_if_needed([Focus("F1", "objective")], pool, loop_n=1)
        self.assertEqual(set(pool), {"c0", "c1"})
        self.assertEqual({chunk.metadata["partial_eval_status"] for chunk in pool.values()}, {"partial_eval_incomplete"})
        provider_error = next(event for event in agent._trace_events if event["event"] == "partial_eval_provider_error")
        self.assertEqual(provider_error["error_type"], "JSONDecodeError")
        self.assertTrue(provider_error["fail_open"])

    async def test_empty_partial_output_is_retained_and_not_resubmitted(self):
        """Missing Focus groups are a contract underflow, never all-noise."""
        agent = SourceAgent.__new__(SourceAgent)
        agent.source = "test"
        agent.cfg = SourceAgentConfig(eval_shortlist=EvalShortlistConfig(input_token_budget=3000))
        agent._trace_events = []
        agent._partial_ledgers = []
        agent._partial_pending_tasks = []
        agent._partial_buffer_ids = []
        agent._partial_buffer_tokens = 0
        agent._partial_submitted_ids = set()
        agent._partial_generation = 0
        agent.partial_eval = object()
        agent._partial_prompt_tokens = lambda _focuses, _chunks: agent._context_budget().flush_threshold
        pool = {
            f"c{i}": ChunkEntry(id=f"c{i}", source="test", content=f"body {i}",
                                 preview_content=f"preview {i}", target_focuses={"F1"})
            for i in range(2)
        }
        response = {"focus_groups": []}
        with patch("agents.source_agent_base._run_llm_agent", new=AsyncMock(return_value=response)) as run_llm:
            await agent._partial_eval_if_needed([Focus("F1", "objective")], pool, loop_n=1)
            await agent._partial_eval_if_needed([Focus("F1", "objective")], pool, loop_n=1)
        self.assertEqual(run_llm.await_count, 1)
        self.assertEqual(set(pool), {"c0", "c1"})
        self.assertEqual(agent._partial_generation, 1)
        self.assertEqual({chunk.metadata["partial_eval_status"] for chunk in pool.values()}, {"partial_eval_incomplete"})
        marker = next(event for event in agent._trace_events if event["event"] == "partial_eval_contract_error")
        self.assertEqual(marker["generation"], 1)
        self.assertEqual(marker["missing_focus_groups"], ["F1"])

    async def test_complete_partial_tiers_remove_unselected_refs(self):
        agent = SourceAgent.__new__(SourceAgent)
        agent.source = "test"
        agent.cfg = SourceAgentConfig(eval_shortlist=EvalShortlistConfig(input_token_budget=3000))
        agent._trace_events = []
        agent._partial_ledgers = []
        agent._partial_pending_tasks = []
        agent._partial_buffer_ids = []
        agent._partial_buffer_tokens = 0
        agent._partial_submitted_ids = set()
        agent._partial_generation = 0
        agent.partial_eval = object()
        agent._partial_prompt_tokens = lambda _focuses, _chunks: agent._context_budget().flush_threshold
        rows = [
            ChunkEntry(
                id=f"test:doc-{i % 3}:chunk-{i}", source="test", content=f"body {i}",
                preview_content=f"preview {i}", target_focuses={"F1"},
            )
            for i in range(5)
        ]
        pool = {chunk.id: chunk for chunk in rows}
        with patch("agents.source_agent_base._run_llm_agent", new=AsyncMock(return_value={
            "focus_groups": [{"focus_id": "F1", "reason": "", "tiers": [
                {"chunk_id": "C1", "tier": "important"},
                {"chunk_id": "C2", "tier": "critical"},
            ]}],
        })):
            await agent._partial_eval_if_needed([Focus("F1", "objective")], pool, loop_n=1)
        self.assertEqual(len(pool), 2)
        self.assertEqual({chunk.metadata["partial_eval_status"] for chunk in pool.values()}, {"partial_tiered"})
        self.assertEqual({chunk.tier for chunk in pool.values()}, {None})
        self.assertEqual(agent._partial_ledgers[0]["tier_counts"], {"critical": 1, "important": 1})
        self.assertEqual(set(agent._partial_ledgers[0]["evidence_chunk_ids"]), {"test:doc-0:chunk-0", "test:doc-1:chunk-1"})
        summary = next(event for event in agent._trace_events if event["event"] == "partial_tier_summary")
        self.assertEqual(summary["unselected_count"], 3)

    async def test_no_qualifying_requires_a_reason_and_is_carried_to_plan_eval(self):
        agent = SourceAgent.__new__(SourceAgent)
        agent.source = "test"
        agent.cfg = SourceAgentConfig(eval_shortlist=EvalShortlistConfig(input_token_budget=3000))
        agent._trace_events = []
        agent._partial_ledgers = []
        agent._partial_pending_tasks = []
        agent._partial_buffer_ids = []
        agent._partial_buffer_tokens = 0
        agent._partial_submitted_ids = set()
        agent._partial_generation = 0
        agent.partial_eval = object()
        agent._partial_prompt_tokens = lambda _focuses, _chunks: agent._context_budget().flush_threshold
        rows = [
            ChunkEntry(id=f"c{i}", source="test", content="body", preview_content="preview", target_focuses={"F1"})
            for i in range(2)
        ]
        pool = {chunk.id: chunk for chunk in rows}
        with patch("agents.source_agent_base._run_llm_agent", new=AsyncMock(return_value={
            "focus_groups": [{"focus_id": "F1", "reason": "no_qualifying_chunks", "tiers": []}],
        })):
            await agent._partial_eval_if_needed([Focus("F1", "objective")], pool, loop_n=1)
        self.assertEqual(pool, {})
        self.assertEqual(agent._partial_ledgers[0]["remaining_gap"], "no_qualifying_chunks")

    async def test_missing_whole_focus_group_retains_affected_batch(self):
        """Only a missing Focus group is underflow; omitted refs are unselected."""
        agent = SourceAgent.__new__(SourceAgent)
        agent.source = "test"
        agent.cfg = SourceAgentConfig(eval_shortlist=EvalShortlistConfig(
            input_token_budget=3000,
        ))
        agent._trace_events = []
        agent._partial_ledgers = []
        agent._partial_pending_tasks = []
        agent._partial_buffer_ids = []
        agent._partial_buffer_tokens = 0
        agent._partial_submitted_ids = set()
        agent._partial_generation = 0
        agent.partial_eval = object()
        agent._partial_prompt_tokens = lambda _focuses, _chunks: agent._context_budget().flush_threshold
        rows = [
            ChunkEntry(id="test:doc-0:chunk-0", source="test", content="body 0",
                       preview_content="preview 0", target_focuses={"F1"}),
            ChunkEntry(id="test:doc-1:chunk-0", source="test", content="body 1",
                       preview_content="preview 1", target_focuses={"F2"}),
        ]
        pool = {chunk.id: chunk for chunk in rows}
        with patch("agents.source_agent_base._run_llm_agent", new=AsyncMock(return_value={
            "focus_groups": [{"focus_id": "F1", "reason": "", "tiers": [
                {"chunk_id": "C1", "tier": "important"},
            ]}],
        })):
            await agent._partial_eval_if_needed(
                [Focus("F1", "objective one"), Focus("F2", "objective two")], pool, loop_n=1,
            )
        self.assertEqual(len(pool), 2)
        self.assertEqual(pool[rows[0].id].metadata["partial_eval_status"], "partial_tiered")
        self.assertEqual(
            {chunk.metadata["partial_eval_status"] for chunk in pool.values()},
            {"partial_tiered", "partial_eval_incomplete"},
        )
        event = next(event for event in agent._trace_events if event["event"] == "partial_eval_contract_error")
        self.assertEqual(event["missing_focus_groups"], ["F2"])

    async def test_partial_schema_rejects_explicit_noise_row(self):
        agent = SourceAgent.__new__(SourceAgent)
        agent.source = "test"
        agent.cfg = SourceAgentConfig(eval_shortlist=EvalShortlistConfig(input_token_budget=3000))
        agent._trace_events = []
        agent._partial_ledgers = []
        agent._partial_pending_tasks = []
        agent._partial_buffer_ids = []
        agent._partial_buffer_tokens = 0
        agent._partial_submitted_ids = set()
        agent._partial_generation = 0
        agent.partial_eval = object()
        agent._partial_prompt_tokens = lambda _focuses, _chunks: agent._context_budget().flush_threshold
        rows = [
            ChunkEntry(id=f"c{i}", source="test", content="body", preview_content="preview", target_focuses={"F1"})
            for i in range(2)
        ]
        pool = {chunk.id: chunk for chunk in rows}
        with patch("agents.source_agent_base._run_llm_agent", new=AsyncMock(return_value={
            "focus_groups": [{"focus_id": "F1", "reason": "", "tiers": [
                {"chunk_id": "C1", "tier": "noise"},
            ]}],
        })):
            await agent._partial_eval_if_needed([Focus("F1", "objective")], pool, loop_n=1)
        self.assertEqual(set(pool), {"c0", "c1"})
        error = next(event for event in agent._trace_events if event["event"] == "partial_eval_contract_error")
        self.assertGreater(error["malformed_chunk_refs"], 0)

    async def test_streaming_admission_waits_for_input_budget(self):
        agent = SourceAgent.__new__(SourceAgent)
        agent.source = "test"
        agent.cfg = SourceAgentConfig(eval_shortlist=EvalShortlistConfig(input_token_budget=10000))
        agent._trace_events = []
        agent._partial_ledgers = []
        agent._partial_pending_tasks = []
        agent._partial_buffer_ids = []
        agent._partial_buffer_tokens = 0
        agent._partial_submitted_ids = set()
        agent._partial_generation = 0
        agent._current_focus_lookup = {"F1": Focus("F1", "objective")}
        agent.partial_eval = object()
        agent._partial_prompt_tokens = lambda _focuses, _chunks: agent._context_budget().flush_threshold
        first = ChunkEntry(id="c1", source="test", content="body", preview_content="preview", target_focuses={"F1"})
        second = ChunkEntry(id="c2", source="test", content="body", preview_content="preview", target_focuses={"F1"})
        agent._partial_prompt_tokens = lambda _focuses, chunks: len(chunks) * 2000
        pool = {"c1": first}
        with patch("agents.source_agent_base._run_llm_agent", new=AsyncMock(return_value={
            "focus_groups": [{"focus_id": "F1", "reason": "", "tiers": [
                {"chunk_id": "C1", "tier": "important"},
                {"chunk_id": "C2", "tier": "important"},
            ]}],
        })) as run_llm:
            await agent._offer_chunk_for_context_eval(first, pool, loop_n=1)
            self.assertEqual(len(agent._partial_pending_tasks), 0)
            pool["c2"] = second
            await agent._offer_chunk_for_context_eval(second, pool, loop_n=1)
            self.assertEqual(len(agent._partial_pending_tasks), 1)
            await agent._drain_partial_evals(pool)
        self.assertEqual(run_llm.await_count, 1)
        self.assertEqual(agent._partial_generation, 1)
        generation = next(event for event in agent._trace_events if event["event"] == "partial_eval_generation")
        self.assertEqual(generation["trigger"], "input_token_ratio")
        self.assertEqual(set(pool), {"c1", "c2"})
        self.assertEqual({chunk.metadata["partial_eval_status"] for chunk in pool.values()}, {"partial_tiered"})


class RerankFailOpenTests(unittest.IsolatedAsyncioTestCase):
    async def test_content_modes_preserve_doc_pool_embedded_tables(self):
        for mode, expected in (("all", {"doc", "table"}), ("table_pool_only", {"table"}), ("off", set())):
            agent = SourceAgent.__new__(SourceAgent)
            agent.source = "feishu"
            agent.cfg = SourceAgentConfig(rerank_enabled=True, content_rerank_mode=mode)
            agent._trace_events = []
            pool = {
                "doc": ChunkEntry(id="doc", source="feishu", content="document table", target_focuses={"F1"},
                                  metadata={"reader_pool": "doc", "content_type": "table_md"}),
                "table": ChunkEntry(id="table", source="feishu", content="independent", target_focuses={"F1"},
                                    metadata={"content_type": "sheet"}),
            }
            seen = set()

            def rank(_query, items, *, settings):
                seen.update(item[0] for item in items)
                return [RankedItem(item_id=item[0], relevance_score=1, batch_index=0, rank=i) for i, item in enumerate(items)]

            with patch("agents.source_agent_base.RerankSettings.from_env", return_value=RerankSettings("key", "workspace")), patch(
                "agents.source_agent_base.rerank_items", side_effect=rank,
            ):
                await agent._rerank_new_chunks([Focus("F1", "conversion")], pool, loop_n=1)
            self.assertEqual(seen, expected)
            self.assertIn("doc", pool)

    async def test_default_rerank_pressure_and_keep_ratio_are_calibrated(self):
        cfg = SourceAgentConfig()
        self.assertEqual(cfg.rerank_trigger_chunk_count, 60)
        self.assertEqual(cfg.rerank_keep_ratio, 0.7)

    async def test_transport_failure_retains_and_freezes_existing_pool(self):
        agent = SourceAgent.__new__(SourceAgent)
        agent.source = "test"
        agent.cfg = SourceAgentConfig(rerank_enabled=True, rerank_max_retries=0)
        agent._trace_events = []
        chunk = ChunkEntry(
            id="c1", source="test", content="body", preview_content="preview", target_focuses={"F1"},
        )
        pool = {chunk.id: chunk}
        settings = RerankSettings("key", "workspace")
        with patch("agents.source_agent_base.RerankSettings.from_env", return_value=settings), patch(
            "agents.source_agent_base.rerank_items", side_effect=RerankError("timeout"),
        ) as rerank:
            await agent._rerank_new_chunks([Focus("F1", "objective")], pool, loop_n=1)
            await agent._rerank_new_chunks([Focus("F1", "objective")], pool, loop_n=2)
        self.assertEqual(rerank.call_count, 1)
        self.assertTrue(pool["c1"].metadata["rerank_processed"])
        self.assertTrue(pool["c1"].metadata["rerank_fail_open"])
        marker = next(event for event in agent._trace_events if event["event"] == "rerank")
        self.assertTrue(marker["fail_open"])

    async def test_independent_focus_groups_rerank_with_bounded_concurrency(self):
        agent = SourceAgent.__new__(SourceAgent)
        agent.source = "test"
        agent.cfg = SourceAgentConfig(rerank_enabled=True, rerank_max_concurrency=2)
        agent._trace_events = []
        first = ChunkEntry(id="c1", source="test", content="body", preview_content="preview", target_focuses={"F1"})
        second = ChunkEntry(id="c2", source="test", content="body", preview_content="preview", target_focuses={"F2"})
        pool = {"c1": first, "c2": second}
        settings = RerankSettings("key", "workspace")

        def slow_rank(_query, items, *, settings):
            time.sleep(0.08)
            return [
                RankedItem(item_id=item_id, relevance_score=0.9, batch_index=0, rank=index)
                for index, (item_id, _text) in enumerate(items)
            ]

        started = time.perf_counter()
        with patch("agents.source_agent_base.RerankSettings.from_env", return_value=settings), patch(
            "agents.source_agent_base.rerank_items", side_effect=slow_rank,
        ):
            await agent._rerank_new_chunks([Focus("F1", "one"), Focus("F2", "two")], pool, loop_n=1)
        self.assertLess(time.perf_counter() - started, 0.14)
        rerank_event = next(event for event in agent._trace_events if event["event"] == "rerank")
        self.assertEqual(rerank_event["groups"][0]["max_concurrency"], 2)

    async def test_rerank_retries_only_after_the_bounded_call_returns(self):
        agent = SourceAgent.__new__(SourceAgent)
        agent.source = "test"
        agent.cfg = SourceAgentConfig(
            rerank_enabled=True, rerank_timeout_s=8.0,
            rerank_max_retries=1, rerank_retry_delay_s=0.0,
        )
        agent._trace_events = []
        chunk = ChunkEntry(id="c1", source="test", content="body", preview_content="preview", target_focuses={"F1"})
        pool = {"c1": chunk}
        settings = RerankSettings("key", "workspace")
        ranked = [RankedItem(item_id="c1", relevance_score=0.9, batch_index=0, rank=0)]
        with patch("agents.source_agent_base.RerankSettings.from_env", return_value=settings), patch(
            "agents.source_agent_base.rerank_items", side_effect=[RerankError("timeout"), ranked],
        ) as rerank:
            await agent._rerank_new_chunks([Focus("F1", "one")], pool, loop_n=1)
        self.assertEqual(rerank.call_count, 2)
        event = next(event for event in agent._trace_events if event["event"] == "rerank")
        self.assertEqual(event["groups"][0]["attempts"], 2)
        self.assertEqual(event["groups"][0]["timeout_s"], 8.0)

    async def test_rerank_wall_timeout_fails_open_without_duplicate_retry(self):
        agent = SourceAgent.__new__(SourceAgent)
        agent.source = "test"
        agent.cfg = SourceAgentConfig(
            rerank_enabled=True, rerank_timeout_s=0.01,
            rerank_max_retries=1, rerank_retry_delay_s=0.0,
        )
        agent._trace_events = []
        chunk = ChunkEntry(id="c1", source="test", content="body", preview_content="preview", target_focuses={"F1"})
        pool = {"c1": chunk}
        settings = RerankSettings("key", "workspace", timeout_s=0.01)

        def blocking_rank(_query, _items, *, settings):
            time.sleep(0.05)
            return [RankedItem(item_id="c1", relevance_score=0.9, batch_index=0, rank=0)]

        with patch("agents.source_agent_base.RerankSettings.from_env", return_value=settings), patch(
            "agents.source_agent_base.rerank_items", side_effect=blocking_rank,
        ) as rerank:
            await agent._rerank_new_chunks([Focus("F1", "one")], pool, loop_n=1)
        self.assertEqual(rerank.call_count, 1)
        self.assertTrue(pool["c1"].metadata["rerank_fail_open"])
        timeout = next(event for event in agent._trace_events if event["event"] == "rerank_wall_timeout")
        self.assertEqual(timeout["timeout_s"], 0.01)

    async def test_rerank_generation_flushes_only_at_configured_chunk_pressure(self):
        agent = SourceAgent.__new__(SourceAgent)
        agent.source = "test"
        agent.cfg = SourceAgentConfig(rerank_enabled=True, rerank_trigger_chunk_count=2)
        agent._trace_events = []
        agent._current_focus_lookup = {"F1": Focus("F1", "one")}
        one = ChunkEntry(id="c1", source="test", content="body", target_focuses={"F1"})
        two = ChunkEntry(id="c2", source="test", content="body", target_focuses={"F1"})
        pool = {"c1": one}
        with patch.object(agent, "_rerank_new_chunks", new=AsyncMock()) as rerank:
            self.assertFalse(await agent._flush_rerank_generation_if_ready(pool, loop_n=1))
            pool["c2"] = two
            self.assertTrue(await agent._flush_rerank_generation_if_ready(pool, loop_n=1))
        self.assertEqual(rerank.await_count, 1)
        event = next(event for event in agent._trace_events if event["event"] == "rerank_generation")
        self.assertEqual(event["pending_count"], 2)


if __name__ == "__main__":
    unittest.main()
