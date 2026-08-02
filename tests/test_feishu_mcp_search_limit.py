from __future__ import annotations

import concurrent.futures
import threading
import time
import unittest
from unittest.mock import patch

from feishu_read_tool import search


class McpSearchConcurrencyLimitTests(unittest.TestCase):
    def test_shared_search_slots_cap_concurrent_posts(self) -> None:
        active = 0
        max_active = 0
        lock = threading.Lock()

        def slow_post(*_args, **_kwargs):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                time.sleep(0.04)
                return object()
            finally:
                with lock:
                    active -= 1

        original = search._MCP_SEARCH_SEM
        try:
            search._MCP_SEARCH_SEM = threading.BoundedSemaphore(2)
            with patch.object(search.requests, "post", side_effect=slow_post):
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                    list(executor.map(
                        lambda _i: search._post_mcp_search({}, {}), range(5),
                    ))
        finally:
            search._MCP_SEARCH_SEM = original

        self.assertEqual(max_active, 2)

    def test_failed_post_releases_slot_for_following_request(self) -> None:
        calls = 0

        def fail_once(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("transport failure")
            return object()

        original = search._MCP_SEARCH_SEM
        try:
            search._MCP_SEARCH_SEM = threading.BoundedSemaphore(1)
            with patch.object(search.requests, "post", side_effect=fail_once):
                with self.assertRaises(RuntimeError):
                    search._post_mcp_search({}, {})
                self.assertIsNotNone(search._post_mcp_search({}, {}))
        finally:
            search._MCP_SEARCH_SEM = original


if __name__ == "__main__":
    unittest.main()
