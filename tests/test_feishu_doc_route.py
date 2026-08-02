"""Read-route contracts for Feishu document candidates."""

import unittest
import asyncio
import concurrent.futures
import time
from unittest.mock import AsyncMock, patch

from agents.feishu.feishu_source_agent import _assign_doc_route_hints, _doc_collect_timeout
from tools.feishu.feishu_doc_pool import FeishuDocPool
from tools.feishu.table_pool import TablePool
from tools.feishu.feishu_retrieve import IO_FUTURE_REGISTRY, _exec_to_thread


class FeishuDocRouteTests(unittest.TestCase):
    def test_deep_dive_keeps_started_document_readable_before_consumer_deadline(self):
        self.assertAlmostEqual(_doc_collect_timeout("deep_dive", 45.0), 31.5)
        self.assertEqual(_doc_collect_timeout("deep_dive", 180.0), 35.0)

    def test_normal_docx_uses_block_even_when_low_rank_single_hit(self):
        docs = [
            {"document_id": "rank-one", "best_api_rank": 1, "selected_query_types": ["concept"]},
            {"document_id": "rank-four", "best_api_rank": 4, "selected_query_types": ["concept"]},
        ]

        _assign_doc_route_hints(docs)

        self.assertEqual([doc["route_hint"] for doc in docs], ["block_api", "block_api"])

    def test_personal_recent_remains_mcp_reader(self):
        docs = [
            {"document_id": "personal", "selected_query_types": ["personal_recent"]},
            {"document_id": "normal", "selected_query_types": ["structured"]},
        ]

        _assign_doc_route_hints(docs)

        self.assertEqual(docs[0]["route_hint"], "mcp")
        self.assertEqual(docs[1]["route_hint"], "block_api")


class FeishuDocPoolDrainTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_and_collect_wakes_on_document_event_without_poll_tail(self):
        pool = FeishuDocPool("test-token")
        pool._doc_ready_events["doc-1"] = asyncio.Event()

        started = time.perf_counter()
        waiter = asyncio.create_task(pool.wait_and_collect(["doc-1"], total_timeout=0.5))
        await asyncio.sleep(0.02)
        pool._doc_ready_events["doc-1"].set()
        self.assertEqual(await waiter, [])
        # The legacy 150ms poll could not meet this bound after an event set.
        self.assertLess(time.perf_counter() - started, 0.12)

    async def test_sync_reader_remains_visible_after_waiter_timeout(self):
        pool = FeishuDocPool("test-token")

        def blocking_reader():
            time.sleep(0.06)
            return "done"

        task = asyncio.create_task(pool._to_thread(blocking_reader))
        await asyncio.sleep(0.01)
        self.assertEqual(await pool.drain_inflight(timeout_s=0.0), 1)
        self.assertEqual(await task, "done")
        self.assertEqual(await pool.drain_inflight(timeout_s=0.0), 0)

    async def test_consumer_deadline_event_is_body_free_and_once_only(self):
        pool = FeishuDocPool("test-token")
        pool._doc_route["doc-1"] = "block_api"
        pool._consumer_deadlines["doc-1"] = {
            "event": "consumer_deadline", "document_id": "doc-1",
            "route": "block_api", "elapsed_s": 25.0, "deadline_s": 25.0,
            "sync_io_may_continue": True,
        }

        event = pool.consume_observability_events()

        self.assertEqual(event[0]["document_id"], "doc-1")
        self.assertNotIn("content", event[0])
        self.assertEqual(pool.consume_observability_events(), [])

    async def test_block_failure_starts_one_sequential_mcp_fallback(self):
        pool = FeishuDocPool("test-token")
        doc = {"id": "doc-1", "title": "test"}
        pool._doc_ready_events["doc-1"] = asyncio.Event()
        pool._reading.add("doc-1")

        async def fake_mcp(docs):
            self.assertEqual([item["id"] for item in docs], ["doc-1"])
            pool._doc_chunks["doc-1"] = [object()]
            pool._done.add("doc-1")
            pool._reading.discard("doc-1")
            pool._doc_ready_events["doc-1"].set()

        with patch("feishu_read_tool.docx_pipeline.run_rag_pipeline", side_effect=RuntimeError("block down")), patch(
            "tools.feishu.feishu_doc_pool.asyncio.sleep", new=AsyncMock(),
        ), patch.object(pool, "_read_mcp_batch", new=AsyncMock(side_effect=fake_mcp)) as mcp:
            await pool._read_block_api_batch([doc])

        self.assertEqual(mcp.await_count, 1)
        self.assertEqual(pool._doc_route["doc-1"], "mcp_fallback")
        self.assertEqual(
            [event["event"] for event in pool.consume_observability_events()],
            ["block_api_failed", "mcp_fallback_started", "mcp_fallback_success"],
        )


class FeishuTablePoolDrainTests(unittest.IsolatedAsyncioTestCase):
    async def test_sheet_or_bitable_sync_reader_remains_visible_to_drain(self):
        pool = TablePool("test-token")

        def blocking_reader():
            time.sleep(0.06)
            return "done"

        task = asyncio.create_task(pool._to_thread(blocking_reader))
        await asyncio.sleep(0.01)
        self.assertEqual(await pool.drain_inflight(timeout_s=0.0), 1)
        self.assertEqual(await task, "done")
        self.assertEqual(await pool.drain_inflight(timeout_s=0.0), 0)


class FeishuDirectWikiDrainTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_wiki_executor_call_registers_with_request_drain(self):
        registry = set()
        token = IO_FUTURE_REGISTRY.set(registry)
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            task = asyncio.create_task(_exec_to_thread(executor, lambda: (time.sleep(0.06), "done")[1]))
            await asyncio.sleep(0.01)
            self.assertEqual(len(registry), 1)
            self.assertEqual(await task, "done")
            self.assertEqual(registry, set())
        finally:
            IO_FUTURE_REGISTRY.reset(token)
            executor.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
