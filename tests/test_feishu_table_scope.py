import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agents.feishu.feishu_source_agent import _merge_table_demands
from feishu_read_tool.basic_tool.bitable_read import search_records
from tools.feishu.feishu_retrieve import (
    TABLE_CHILD_READ_LIMIT,
    _dedupe_table_candidates,
)
from tools.core.rerank import RerankSettings
from tools.core.table_child_rerank import select_table_children


class FeishuTableScopeTests(unittest.TestCase):
    def test_system_and_benchmark_bitables_are_excluded_before_native_budget(self):
        result = {
            "items": [
                {"id": "system", "doc_type": "BITABLE", "title": "KB bailian"},
                {"id": "seed", "doc_type": "BITABLE", "title": "query_seed"},
                {"id": "business-a", "doc_type": "BITABLE", "title": "GL sales"},
                {"id": "business-b", "doc_type": "BITABLE", "title": "CA sales"},
            ],
            "_debug": {"direct_count": 4, "wiki_table_count": 0},
        }

        selected = _dedupe_table_candidates(result, direct_limit=2, wiki_limit=2)

        self.assertEqual([item["id"] for item in selected], ["business-a", "business-b"])
        self.assertEqual([item["_source"] for item in selected], ["direct", "direct"])

    def test_title_match_is_case_insensitive_but_not_a_fuzzy_relevance_rule(self):
        result = {
            "items": [
                {"id": "registered", "doc_type": "BITABLE", "title": "QUERY_SEED"},
                {"id": "business", "doc_type": "BITABLE", "title": "query_seed archive"},
            ],
            "_debug": {"direct_count": 2, "wiki_table_count": 0},
        }

        selected = _dedupe_table_candidates(result, direct_limit=2, wiki_limit=2)

        self.assertEqual([item["id"] for item in selected], ["business"])

    @patch("tools.core.table_child_rerank.RerankSettings.from_env")
    def test_child_expansion_uses_parent_rerank_and_is_hard_bounded(self, from_env):
        from_env.return_value = RerankSettings("key", "workspace")
        children = [
            {"id": f"t{i}", "_child_title": f"generic table {i}", "summary": ""}
            for i in range(10)
        ]
        children[8]["_child_title"] = "MX SKU daily sales and inventory"

        def transport(url, headers, payload, timeout):
            return {"results": [
                {"index": 8, "relevance_score": 0.9},
                {"index": 4, "relevance_score": 0.6},
                {"index": 1, "relevance_score": 0.4},
            ]}

        selected, trace = select_table_children(
            "MX SKU sales inventory", children,
            top_n=3, instruct="test", transport=transport,
        )

        self.assertEqual(trace["mode"], "qwen_parent_rerank")
        self.assertEqual(selected[0]["id"], "t8")
        self.assertEqual(len(selected), TABLE_CHILD_READ_LIMIT)

    def test_child_expansion_at_or_below_three_skips_rerank(self):
        children = [
            {"id": f"t{i}", "_child_title": f"子表{i}", "summary": ""}
            for i in range(3)
        ]

        selected, trace = select_table_children(
            "unmatched vocabulary", children, top_n=3, instruct="test",
        )

        self.assertEqual(trace["mode"], "all_children")
        self.assertFalse(trace["provider_called"])
        self.assertEqual([item["id"] for item in selected], ["t0", "t1", "t2"])

    def test_cross_query_merge_does_not_re_expand_parent_children(self):
        batches = []
        for query_index in range(2):
            ids = [f"t{query_index * 3 + i}" for i in range(3)]
            batches.append({
                "sq": SimpleNamespace(sub_query_id=f"sq{query_index}"),
                "table_candidates": [{
                    "id": "parent", "doc_type": "BITABLE", "title": "Sales",
                    "selected_table_ids": ids,
                    "selected_table_titles": [f"Table {item}" for item in ids],
                    "table_structure_child_count": 85,
                }],
            })

        merged, _ = _merge_table_demands(batches)

        self.assertEqual(merged[0]["selected_table_ids"], ["t0", "t1", "t2"])
        self.assertEqual(merged[0]["table_selected_child_count"], 3)
        self.assertEqual(merged[0]["table_child_overflow_count"], 82)

    def test_fallback_record_limit_stops_api_pagination(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "code": 0,
                    "data": {
                        "items": [{"record_id": f"r{i}"} for i in range(3)],
                        "has_more": True,
                        "page_token": "next",
                    },
                }

        with patch("feishu_read_tool.basic_tool.bitable_read.requests.post", return_value=Response()) as post:
            records = search_records("app", "table", "token", page_size=3, max_records=3)

        self.assertEqual(len(records), 3)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["params"]["page_size"], 3)
