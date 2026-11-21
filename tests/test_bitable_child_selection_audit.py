import unittest
from unittest.mock import patch

from feishu_read_tool import bitable_reader
from tools.core.models import UnifiedChunk


class BitableChildSelectionAuditTests(unittest.TestCase):
    def test_cached_selection_proves_only_selected_children_enter_reader(self):
        tables = [
            {"table_id": f"tbl_{index}", "name": f"Table {index}"}
            for index in range(4)
        ]

        started = []
        def fake_read_single(parent_id, table, *args):
            started.append(table["table_id"])
            return [UnifiedChunk(
                text="synthetic table body",
                source_name="audit",
                source_url="",
                source_tool="feishu",
                heading_path="audit",
                metadata={"child_id": table["table_id"]},
            )]

        selected_ids = ["tbl_0", "tbl_1", "tbl_2"]
        with patch.object(bitable_reader, "list_tables") as list_api, \
                patch.object(bitable_reader, "_read_single_table", side_effect=fake_read_single):
            chunks = bitable_reader._read_single_bitable(
                {"id": "synthetic-parent", "title": "Synthetic base", "url": "",
                 "_cached_tables": tables, "selected_table_ids": selected_ids},
                [], "query", "test-token",
            )
        list_api.assert_not_called()
        self.assertEqual(sorted(selected_ids), sorted(started))
        self.assertEqual(set(selected_ids), {c.metadata["child_id"] for c in chunks})


if __name__ == "__main__":
    unittest.main()
