import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eval.audit_feishu_bitable_child_selection import run_audit
from feishu_read_tool import bitable_reader
from tools.core.models import UnifiedChunk


class BitableChildSelectionAuditTests(unittest.TestCase):
    def test_audit_proves_only_selected_children_enter_reader(self):
        tables = [
            {"table_id": f"tbl_{index}", "name": f"Table {index}"}
            for index in range(4)
        ]

        def fake_selector(query, children, **kwargs):
            return list(children[:3]), {
                "mode": "qwen_parent_rerank",
                "provider_called": True,
                "input_count": 4,
            }

        def fake_read_single(parent_id, table, *args):
            return [UnifiedChunk(
                text="body is never serialized by the audit",
                source_name="audit",
                source_url="",
                source_tool="feishu",
                heading_path="audit",
                metadata={"child_id": table["table_id"]},
            )]

        with tempfile.TemporaryDirectory() as tmpdir, \
                patch("eval.audit_feishu_bitable_child_selection.list_tables", return_value=tables), \
                patch("eval.audit_feishu_bitable_child_selection.select_table_children", side_effect=fake_selector), \
                patch.object(bitable_reader, "_read_single_table", side_effect=fake_read_single):
            artifact = run_audit(
                parent="https://<tenant>.feishu.cn/<redacted>",
                query="query",
                token="test-token",
                output=Path(tmpdir) / "audit.json",
            )

        self.assertTrue(artifact["passed"])
        self.assertTrue(artifact["body_redacted"])
        self.assertEqual(["tbl_0", "tbl_1", "tbl_2"], artifact["read"]["started_child_ids"])
        self.assertEqual([], artifact["read"]["unexpected_started_child_ids"])
        self.assertEqual([], artifact["read"]["unexpected_returned_child_ids"])
        self.assertNotIn("body is never serialized by the audit", str(artifact))


if __name__ == "__main__":
    unittest.main()
