import asyncio
import concurrent.futures
import threading
import unittest

from retrieve_agent import _dispatch_sources, _timeout_result
from agents.source_agent_base import Focus, SourceAgent
from tools.core.models import SourceAgentConfig


class SourceTimeoutTraceTests(unittest.TestCase):
    def test_timeout_keeps_completed_read_trace(self):
        class Agent:
            _trace_events = [{"event": "read", "chunk_id": "c1"}]

        result = _timeout_result("feishu", Agent(), 240.0)
        self.assertEqual(result.retrieval_trace[0]["chunk_id"], "c1")
        self.assertEqual(result.retrieval_trace[-1], {"event": "timeout", "source": "feishu", "timeout_s": 240.0})


class SourceThreadDrainTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_waiter_does_not_hide_running_sync_io(self):
        release = threading.Event()
        agent = SourceAgent.__new__(SourceAgent)
        agent._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        agent._inflight_futures = set()
        task = asyncio.create_task(agent._to_thread(release.wait, 2.0))
        await asyncio.sleep(0.02)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(await agent.drain_inflight(timeout_s=0.01), 1)
        release.set()
        await asyncio.sleep(0.05)
        self.assertEqual(await agent.drain_inflight(timeout_s=0.01), 0)
        agent._executor.shutdown(wait=True)

    async def test_dispatch_timeout_exposes_unsettled_sync_io_for_retry_gate(self):
        """A timed-out source must retain its executor future until it really ends.

        The runner uses ``remaining_sync_io`` to decline a whole-case retry.  This
        test is deliberately local: it models a blocking synchronous Feishu API
        call without sending any organization data to an external service.
        """
        release = threading.Event()
        started = threading.Event()

        class BlockingAgent:
            source = "feishu"
            cfg = SourceAgentConfig(source_timeout_s=0.10, cancel_drain_timeout_s=0.01)

            def __init__(self):
                self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                self._inflight_futures = set()
                self._trace_events = [{"event": "read_started", "route": "bitable_api"}]

            async def run(self, *_args, **_kwargs):
                def _blocking_read():
                    started.set()
                    return release.wait(2.0)

                return await SourceAgent._to_thread(self, _blocking_read)

            async def drain_inflight(self, timeout_s=10.0):
                return await SourceAgent.drain_inflight(self, timeout_s)

        agent = BlockingAgent()
        try:
            dispatch = asyncio.create_task(_dispatch_sources(
                {"feishu": agent}, [Focus(focus_id="F1", text="table", targets=["feishu"])],
            ))
            self.assertTrue(await asyncio.to_thread(started.wait, 0.5))
            results = await dispatch
            trace = results[0].retrieval_trace
            self.assertEqual(trace[0]["event"], "read_started")
            self.assertIn({"event": "timeout", "source": "feishu", "timeout_s": 0.10}, trace)
            drain = next(event for event in trace if event["event"] == "cancel_drain")
            self.assertEqual(drain["remaining_sync_io"], 1)
            self.assertEqual(drain["drain_timeout_s"], 0.01)
            release.set()
            await asyncio.sleep(0.05)
            self.assertEqual(await agent.drain_inflight(timeout_s=0.01), 0)
        finally:
            release.set()
            agent._executor.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
