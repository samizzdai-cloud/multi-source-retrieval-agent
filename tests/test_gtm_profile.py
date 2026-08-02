import tempfile
import unittest
import json
import hashlib
from unittest.mock import patch
from pathlib import Path

from tools.core.gtm_profile import (
    GTMProfileRepository,
    ProfileGovernanceAgent,
    RetrievalGovernanceStore,
    RetrievalDelta,
    auto_write_allowed,
    build_shadow_report,
    extract_profile_deltas,
)
from tools.core.models import ProfileRerankConfig
from tools.core.rerank import RerankResult, RerankSettings
from tools.core.profile_snapshot import ProfileSnapshotManager, SnapshotValidation
from tools.core.models import ProfileGovernanceConfig


class GTMProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.repo = GTMProfileRepository(Path(self.tmp.name) / "profile.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def _freeze_registry(self, package_limit=450):
        with self.repo._connect() as db:
            rows = db.execute(
                "SELECT id,name,variants FROM expression_set WHERE scope=? AND status='active' ORDER BY id",
                (self.repo.scope,),
            ).fetchall()
        included = [int(row[0]) for row in rows[:package_limit]]
        excluded = [int(row[0]) for row in rows[package_limit:]]
        selected = [row for row in rows if int(row[0]) in set(included)]
        root = Path(self.tmp.name) / "snapshots"
        root.mkdir(exist_ok=True)
        manifest = root / "s1.manifest.json"
        manifest.write_text(json.dumps({"snapshot_id": "s1", "registry": {
            "included_ids": included, "excluded_ids": excluded, "package_limit": package_limit,
            "content_fingerprint": hashlib.sha256(json.dumps(selected, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
        }}), encoding="utf-8")
        self.repo._active_pointer = {"snapshot_id": "s1", "manifest_path": str(manifest)}

    def test_entity_and_expression_are_independent_and_bounded(self):
        self.assertTrue(self.repo.apply_patch(
            {"action": "add_entity", "type": "entity", "name": "REDMAGIC", "kind": "brand"},
            trace_supported=True,
        ))
        self.assertTrue(self.repo.apply_patch(
            {"action": "add_expression", "type": "expression", "name": "affiliate marketing",
             "variants": "affiliate|联盟营销"}, trace_supported=True,
        ))
        pack = self.repo.build_pack("REDMAGIC affiliate performance")
        self.assertEqual(pack.entities[0]["name"], "REDMAGIC")
        self.assertEqual(pack.expressions[0]["name"], "affiliate marketing")
        aliases, expressions = pack.fallback_variants("REDMAGIC affiliate performance")
        self.assertFalse(aliases)
        self.assertEqual(expressions, ["REDMAGIC 联盟营销 performance"])

    def test_profile_fts_never_blocks_punctuation_bearing_queries(self):
        self.assertTrue(self.repo.apply_patch(
            {"action": "add_expression", "type": "expression", "name": "新品销量",
             "variants": "新品销售|首销"}, trace_supported=True,
        ))
        self.assertIsInstance(self.repo.build_pack("Hypershell 5.20-5.26 新品销量"), object)

    def test_callback_rejects_sku_generic_and_unsupported_patch(self):
        for name in ("SKU-A123", "data", "report"):
            self.assertFalse(self.repo.apply_patch(
                {"action": "add_entity", "type": "entity", "name": name}, trace_supported=True,
            ))
        self.assertFalse(self.repo.apply_patch(
            {"action": "add_entity", "type": "entity", "name": "REDMAGIC"}, trace_supported=False,
        ))

    def test_auto_write_gate_needs_three_independent_successes(self):
        deltas = [
            RetrievalDelta(focus=f"f{i}", source="feishu", query_type="concept",
                           changed_kind="alias", first_candidate_hits=0, final_evidence_hits=1,
                           root_request_id=f"r{i}", query_signature=f"q{i % 2}")
            for i in range(3)
        ]
        self.assertTrue(auto_write_allowed(
            deltas, holdout_candidate_delta=0, holdout_evidence_delta=0, false_expansions=0,
        ))
        self.assertFalse(auto_write_allowed(
            deltas, holdout_candidate_delta=0, holdout_evidence_delta=-0.01, false_expansions=0,
        ))

    def test_shadow_report_aggregates_only_compact_events(self):
        class Result:
            retrieval_trace = [
                {"event": "query", "content": "must not persist"},
                {"event": "candidate"}, {"event": "read", "chunk_id": "c1"}, {"event": "judgment"},
            ]
        report = build_shadow_report([Result()])
        self.assertEqual(report["mode"], "shadow")
        self.assertEqual(report["event_counts"], {
            "query": 1, "candidate": 1, "read": 1, "judgment": 1,
            "timeout": 0, "profile_fallback": 0,
        })
        self.assertNotIn("content", report["events"][0])
        self.assertEqual(report["events"][2]["chunk_id"], "c1")

    def test_governance_store_persists_body_free_report(self):
        store = RetrievalGovernanceStore(Path(self.tmp.name) / "governance.sqlite")
        store.append({"mode": "shadow", "events": [{"event": "query", "api_query": "safe"}]})
        self.assertEqual(store.count(), 1)

    def test_governance_outbox_is_durable_and_idempotent(self):
        store = RetrievalGovernanceStore(Path(self.tmp.name) / "governance.sqlite")
        report = {"mode": "shadow", "events": [{"event": "query", "api_query": "safe"}]}
        self.assertTrue(store.enqueue(report, idempotency_key="res-1"))
        self.assertFalse(store.enqueue(report, idempotency_key="res-1"))
        self.assertEqual(store.outbox_counts(), {"pending": 1})
        self.assertEqual(store.drain_outbox(), 1)
        self.assertEqual(store.count(), 1)
        self.assertEqual(store.outbox_counts(), {"done": 1})

    def test_extract_profile_delta_never_treats_candidate_as_evidence(self):
        store = RetrievalGovernanceStore(Path(self.tmp.name) / "governance.sqlite")
        store.append({"mode": "shadow", "events": [{
            "event": "profile_fallback", "source": "feishu", "focus": "f1",
            "query_type": "structured", "fallback_origin": "profile",
            "fallback_kind": "alias", "primary_candidate_count": 0,
            "fallback_candidate_count": 3, "content": "must not persist",
        }]})
        deltas = extract_profile_deltas(store.iter_reports())
        self.assertEqual(len(deltas), 1)
        self.assertEqual(deltas[0].fallback_candidate_hits, 3)
        self.assertEqual(deltas[0].final_evidence_hits, 0)
        self.assertFalse(deltas[0].qualifies())

    def test_governance_callback_requires_holdout_gate(self):
        agent = ProfileGovernanceAgent(self.repo, RetrievalGovernanceStore(Path(self.tmp.name) / "governance.sqlite"))
        deltas = [
            RetrievalDelta(focus=f"f{i}", source="feishu", query_type="concept",
                           changed_kind="alias", first_candidate_hits=0, final_evidence_hits=1,
                           root_request_id=f"r{i}", query_signature=f"q{i % 2}")
            for i in range(3)
        ]
        patch = {"action": "add_entity", "type": "entity", "name": "REDMAGIC", "kind": "brand"}
        self.assertFalse(agent.callback(
            patch, deltas=deltas, holdout_candidate_delta=0, holdout_evidence_delta=-0.01,
            false_expansions=0,
        ))
        self.assertTrue(agent.callback(
            patch, deltas=deltas, holdout_candidate_delta=0, holdout_evidence_delta=0,
            false_expansions=0,
        ))

    def test_delta_joins_fallback_query_to_final_retained_chunk(self):
        report = {"events": [
            {"event": "profile_fallback", "resolution_id": "r1", "source": "feishu",
             "focus": "f1", "query_id": "q1", "query_type": "concept",
             "api_query": "联盟营销", "fallback_kind": "expression",
             "profile_item_id": 17, "profile_item_type": "expression", "snapshot_id": "s1",
             "primary_candidate_count": 0, "fallback_candidate_count": 1},
            {"event": "read", "query_ids": ["q1"], "chunk_id": "c1"},
            {"event": "final_retained", "chunk_id": "c1", "tier": "important"},
        ]}
        delta = extract_profile_deltas([{"report": report}])[0]
        self.assertEqual(delta.final_evidence_hits, 1)
        self.assertEqual(delta.root_request_id, "r1")
        self.assertEqual(delta.profile_item_id, 17)
        self.assertEqual(delta.profile_item_type, "expression")
        self.assertEqual(delta.snapshot_id, "s1")
        self.assertEqual(delta.query_signature, "联盟营销")

    def test_profile_item_id_is_trace_only_not_planner_context(self):
        self.assertTrue(self.repo.apply_patch(
            {"action": "add_expression", "type": "expression", "name": "affiliate marketing",
             "variants": "affiliate|partner marketing"}, trace_supported=True,
        ))
        pack = self.repo.build_pack("affiliate results")
        self.assertGreater(pack.expressions[0]["id"], 0)
        self.assertNotIn('\"id\"', pack.prompt_block())
        variants = pack.fallback_variant_specs("affiliate results")
        self.assertEqual("expression", variants[0][1])
        self.assertGreater(variants[0][2], 0)

    def test_profile_resolution_semantic_candidate_is_planner_only(self):
        self.assertTrue(self.repo.apply_patch(
            {"action": "add_expression", "type": "expression", "name": "affiliate marketing",
             "variants": "联盟营销|伙伴营销"}, trace_supported=True,
        ))
        self._freeze_registry()
        settings = RerankSettings(api_key="k", workspace_id="w")
        with patch("tools.core.rerank.RerankSettings.from_env", return_value=settings), patch(
            "tools.core.rerank.rerank_documents",
            return_value=[RerankResult(index=0, relevance_score=0.91)],
        ):
            resolution = self.repo.resolve(
                "partner channel performance",
                ProfileRerankConfig(shadow=False, min_score=0.8, min_margin=0.05),
                resolution_id="res_test",
            )
        self.assertEqual(resolution.resolution_id, "res_test")
        self.assertEqual(resolution.planner_pack.expressions[0]["name"], "affiliate marketing")
        self.assertEqual(resolution.fallback_pack.expressions, [])
        self.assertIn("<profile_context>", resolution.planner_prompt_block())

    def test_registry_limit_is_explicit_and_never_silently_batched(self):
        for index in range(3):
            self.assertTrue(self.repo.apply_patch(
                {"action": "add_expression", "type": "expression", "name": f"intent phrase {index}",
                 "variants": ""}, trace_supported=True,
            ))
        self._freeze_registry(package_limit=2)
        resolution = self.repo.resolve(
            "unmatched request", ProfileRerankConfig(enabled=False, package_limit=2),
        )
        self.assertEqual(resolution.registry_state, "active")
        self.assertEqual(resolution.registry_included_count, 2)
        self.assertEqual(resolution.registry_excluded_count, 1)

    def test_delete_is_reversible_tombstone(self):
        self.assertTrue(self.repo.apply_patch(
            {"action": "add_expression", "type": "expression", "name": "channel attribution",
             "variants": "渠道归因"}, trace_supported=True,
        ))
        with self.repo._connect() as db:
            item_id = db.execute("SELECT id FROM expression_set WHERE normalized=?", ("channel attribution",)).fetchone()[0]
        self.assertTrue(self.repo.apply_patch(
            {"action": "delete", "type": "expression", "id": item_id}, trace_supported=True,
        ))
        self.assertEqual(self.repo.build_pack("channel attribution").expressions, [])
        self.assertTrue(self.repo.apply_patch(
            {"action": "reactivate", "type": "expression", "id": item_id}, trace_supported=True,
        ))
        self.assertEqual(self.repo.build_pack("channel attribution").expressions[0]["name"], "channel attribution")

    def test_snapshot_publish_requires_commissioning_and_policy_stability(self):
        root = Path(self.tmp.name)
        manager = ProfileSnapshotManager(
            live_path=self.repo.path, root=root / "snapshots",
            rerank=ProfileRerankConfig(enabled=False),
            governance=ProfileGovernanceConfig(mode="autonomous"),
        )
        deltas = [
            RetrievalDelta(
                focus=f"f{i}", source="feishu", query_type="concept",
                changed_kind="expression", first_candidate_hits=0,
                final_evidence_hits=1, root_request_id=f"r{i}",
                query_signature=f"signature-{i % 2}",
            ) for i in range(3)
        ]
        validation = SnapshotValidation(
            independent_requests=3, query_signatures=2,
            successful_deltas=3, total_deltas=3,
            candidate_recall_delta=0.05, evidence_recall_delta=0.0,
            false_expansions=0, median_rank_improvement=2,
        )
        patch_value = {"action": "add_expression", "type": "expression",
                       "name": "affiliate marketing", "variants": "联盟营销"}
        self.assertIsNone(manager.publish_patch(patch_value, deltas=deltas, validation=validation))
        self.assertTrue(manager.commission(approved=True))
        snapshot_id = manager.publish_patch(patch_value, deltas=deltas, validation=validation)
        self.assertTrue(snapshot_id)
        self.assertEqual(manager.active_snapshot_id(), snapshot_id)

        changed = ProfileSnapshotManager(
            live_path=self.repo.path, root=manager.root,
            rerank=ProfileRerankConfig(enabled=False, instruction_version="v2"),
            governance=ProfileGovernanceConfig(mode="autonomous"),
        )
        self.assertFalse(changed.is_commissioned())


if __name__ == "__main__":
    unittest.main()
