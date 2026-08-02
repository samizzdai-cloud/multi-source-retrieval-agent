import asyncio
import unittest
from unittest import mock
from types import SimpleNamespace
from unittest.mock import patch

from eval.feishu_scope_first_runner import _inconclusive_reasons
from feishu_read_tool.pipeline_models import Chunk, ChunkContentType, PipelineConfig
from feishu_read_tool.basic_tool.sheet_utils import parse_sheet_token
from feishu_read_tool.docx_pipeline import _select_type30_sheet_tasks
from feishu_read_tool.table_handler import _embedded_sheet_failure_disposition, build_sheet_chunk
from tools.feishu.feishu_doc_pool import FeishuDocPool


class PartialReadContractTests(unittest.TestCase):
    def test_sheet_token_normalizes_invisible_and_outer_whitespace(self):
        self.assertEqual(
            ("appToken", "sheetId"),
            parse_sheet_token(" \ufeffappToken_sheetId\u200b "),
        )
        with self.assertRaises(ValueError):
            parse_sheet_token("app_token_extra")

    def test_type30_embeds_share_parent_child_selector_before_read(self):
        class Client:
            def query_all_sheets_metadata(self, parent, timeout):
                self.parent, self.timeout = parent, timeout
                return [
                    {"sheet_id": f"s{index}", "title": f"Tab {index}"}
                    for index in range(4)
                ]

        tasks = [
            ("block", {"block_type": 30, "block_id": f"b{index}",
                       "sheet": {"token": f"parent_s{index}"}}, 30, f"b{index}")
            for index in range(4)
        ]
        sink = []
        client = Client()
        with patch("tools.core.table_child_rerank.select_table_children") as chooser:
            chooser.return_value = ([{"id": "s1"}, {"id": "s3"}, {"id": "s0"}], {
                "mode": "qwen_parent_rerank", "input_count": 4, "selected_count": 3,
            })
            selected = _select_type30_sheet_tasks(
                tasks, client=client, query="needed evidence", cfg=PipelineConfig(io_failure_sink=sink),
            )
        self.assertEqual(["b0", "b1", "b3"], [item[3] for item in selected])
        self.assertEqual("parent", client.parent)
        self.assertEqual(3.0, client.timeout)
        self.assertEqual("type30_sheet_selection", sink[0]["source"])
        self.assertEqual("s0,s1,s3", sink[0]["selected_child_ids"])

    def test_type30_opaque_tab_ids_never_trigger_metadata_rerank(self):
        class Client:
            def query_all_sheets_metadata(self, parent, timeout):
                return [{"sheet_id": f"s{i}", "title": f"s{i}"} for i in range(4)]

        tasks = [
            ("block", {"sheet": {"token": f"parent_s{i}"}}, 30, f"b{i}")
            for i in range(4)
        ]
        sink = []
        with mock.patch("tools.core.table_child_rerank.select_table_children") as rerank:
            selected = _select_type30_sheet_tasks(
                tasks, client=Client(), query="needed evidence", cfg=PipelineConfig(io_failure_sink=sink),
            )
        rerank.assert_not_called()
        self.assertEqual([task[3] for task in selected], ["b0", "b1", "b2", "b3"])
        self.assertEqual("metadata_unusable_full_read", sink[0]["selection_mode"])
        self.assertEqual("4", sink[0]["selected_child_count"])

    def test_type30_failure_is_reported_without_body(self):
        sink = []
        cfg = PipelineConfig(io_failure_sink=sink)
        observed = {}

        def fail_once(*args, **kwargs):
            observed.update(kwargs)
            raise TimeoutError("timeout")

        with patch("feishu_read_tool.basic_tool.sheet_utils.parse_sheet_token", return_value=("app", "sheet")), \
             patch("feishu_read_tool.sheet_reader._api_call_with_backoff", side_effect=fail_once):
            chunks, consumed = build_sheet_chunk(
                block={"block_id": "block-1", "sheet": {"token": "app_sheet"}},
                client=SimpleNamespace(read_sheet_range=lambda *args: []),
                doc_id="doc-1", doc_title="", doc_url="", heading_path="",
                chunk_counter=[0], query="q", llm_caller=lambda _: "", cfg=cfg,
            )
        self.assertEqual(chunks, [])
        self.assertEqual(consumed, {"block-1"})
        self.assertEqual(len(sink), 1)
        self.assertEqual(sink[0]["source"], "type30_sheet")
        self.assertEqual(sink[0]["document_id"], "doc-1")
        self.assertEqual(sink[0]["block_id"], "block-1")
        self.assertTrue(sink[0]["error_type"])
        self.assertNotIn("content", sink[0])
        self.assertEqual(3.0, observed["per_call_timeout_s"])
        self.assertEqual(1, observed["max_attempts"])

    def test_type30_chunk_preserves_docx_block_and_sheet_lineage(self):
        chunks, _ = build_sheet_chunk(
            block={"block_id": "block-1", "sheet": {"token": "parent_child"}},
            client=SimpleNamespace(read_sheet_range=lambda *args: []),
            doc_id="doc-1", doc_title="", doc_url="", heading_path="",
            chunk_counter=[0], query="q", llm_caller=lambda _: "", cfg=PipelineConfig(),
            rows_raw=[["metric", "value"], ["sales", "10"]],
        )
        self.assertEqual(1, len(chunks))
        self.assertEqual("docx_embedded_sheet", chunks[0].metadata["source_kind"])
        self.assertEqual("parent", chunks[0].metadata["embedded_spreadsheet_token"])
        self.assertEqual("child", chunks[0].metadata["embedded_sheet_id"])
        self.assertEqual("block-1", chunks[0].metadata["embedded_block_id"])

    def test_doc_pool_carries_partial_marker_to_unified_chunk(self):
        async def scenario():
            pool = FeishuDocPool("token")
            pool._doc_meta["doc-1"] = {"title": "title", "url": "", "route_hint": "block_api"}
            pool._doc_route["doc-1"] = "block_api"
            pool._doc_chunks["doc-1"] = [Chunk(
                chunk_id="c1", doc_id="doc-1", doc_title="title", doc_url="",
                heading_path="", content_type=ChunkContentType.TEXT, content="safe", block_ids=[],
                token_count=1, has_table=False,
                metadata={"embedded_spreadsheet_token": "parent", "embedded_sheet_id": "child"},
            )]
            pool._doc_read_failures["doc-1"] = [{"error_type": "TimeoutError"}]
            pool._doc_ready_events["doc-1"] = asyncio.Event()
            pool._doc_ready_events["doc-1"].set()
            return await pool.wait_and_collect(["doc-1"])

        chunks = asyncio.run(scenario())
        self.assertTrue(chunks[0].metadata["reader_partial"])
        self.assertEqual(chunks[0].metadata["reader_failure_count"], 1)
        self.assertEqual(chunks[0].metadata["reader_failure_types"], ["TimeoutError"])
        self.assertEqual(chunks[0].metadata["embedded_spreadsheet_token"], "parent")
        self.assertEqual(chunks[0].metadata["embedded_sheet_id"], "child")

    def test_embedded_sheet_failure_does_not_mark_docx_partial(self):
        async def scenario():
            pool = FeishuDocPool("token")
            pool._doc_meta["doc-1"] = {"title": "title", "url": "", "route_hint": "block_api"}
            pool._doc_route["doc-1"] = "block_api"
            pool._doc_chunks["doc-1"] = [Chunk(
                chunk_id="c1", doc_id="doc-1", doc_title="title", doc_url="",
                heading_path="", content_type=ChunkContentType.TEXT, content="safe", block_ids=[],
                token_count=1, has_table=False,
            )]
            pool._doc_skipped_embeds["doc-1"] = [{
                "disposition": "transient", "block_id": "block-1",
                "spreadsheet_token": "parent", "sheet_id": "child", "error_type": "ReadTimeout",
            }]
            pool._doc_ready_events["doc-1"] = asyncio.Event()
            pool._doc_ready_events["doc-1"].set()
            return await pool.wait_and_collect(["doc-1"])

        chunk = asyncio.run(scenario())[0]
        self.assertFalse(chunk.metadata["reader_partial"])
        self.assertEqual(chunk.metadata["embedded_sheet_skip_count"], 1)
        self.assertEqual(chunk.metadata["embedded_sheet_failure_disposition"], "transient")
        self.assertEqual(chunk.metadata["embedded_sheet_failures"][0]["sheet_id"], "child")

    def test_partial_read_is_formally_inconclusive(self):
        record = {"retrieval_trace": {"events": [{"event": "partial_read"}]}}
        self.assertEqual(_inconclusive_reasons(record), ["read_partial"])

    def test_invalid_embedded_token_is_skipped_not_retried(self):
        self.assertEqual(
            _embedded_sheet_failure_disposition(ValueError("invalid spreadsheet token")),
            "skip_invalid_token",
        )
        self.assertEqual(
            _embedded_sheet_failure_disposition(TimeoutError("read timed out")),
            "transient",
        )
