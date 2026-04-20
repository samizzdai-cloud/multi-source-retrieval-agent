"""Feishu 99991400 共享 QPS 桶 —— 优先级令牌调度器。

背景
-----
飞书 **账号级**硬限流 (`code=99991400 request trigger frequency limit`)，
**5 qps 共享**跨以下接口:

    - docx.v1.document_block.list           (list_document_blocks)
    - board.v1.whiteboards.<id>.nodes       (_fetch_whiteboard_nodes)
    - drive.v1.files.<id>.comments          (get_doc_comments)

Sheet v2 (`read_sheet_range`) **不**在此桶内（实测 50 并发全 200）。

实测数据（Sprint 2 限流诊断，诊断脚本已清理）：
    - type43 30 并发 → 39/60 失败 (65%)，错误码 99991400
    - 混合 50 并发 → list / whiteboard 均限流
    - type30 sheet 50 并发 → 全 200

设计
-----
* **单例** `_SHARED_BUCKET` —— 整个 Python 进程共享，客户端多实例不重复限流。
* **稳态 5 qps + 令牌桶容量 5** —— 打满飞书上限，首批 5 个请求零等待（burst 容忍）。
* **优先级** —— 越小越先取:
    - P_BLOCK_API (0)   → list_document_blocks，Stage 3 关键路径
    - P_WHITEBOARD (10) → type43 深读，Stage 4 大块但次要
    - P_COMMENT (20)    → comments，最次要
    - 同优先级按 FIFO（seq 递增，防饿死）。
* **线程安全** —— 基于 `threading.Condition`，同时兼容 asyncio (外层包 `run_in_executor`)。
* **边界保护** —— `acquire(timeout=30)` 防死锁; 令牌桶容量上限防长时间空闲后暴冲。

使用
-----
    from feishu_read_tool.rate_limiter import DOC_API_BUCKET, P_BLOCK_API
    DOC_API_BUCKET.acquire(P_BLOCK_API)  # 阻塞直到拿到令牌
    # ... call API ...
"""

from __future__ import annotations

import heapq
import itertools
import threading
import time
from typing import Optional

# ── 优先级常量（越小越先）──────────────────────────────────────────
P_BLOCK_API = 0       # list_document_blocks: Stage 3 核心，不能等
P_WHITEBOARD = 10     # _fetch_whiteboard_nodes: Stage 4 IO pool
P_COMMENT = 20        # get_doc_comments: Stage 4 锦上添花


class PriorityTokenBucket:
    """线程安全的优先级令牌桶。

    稳态 qps 受 ``_refill_interval`` 控制（默认 5 qps → 200ms/token）。
    容量 = qps，起始满；空闲期不累积超限令牌。

    Parameters
    ----------
    qps : float
        稳态令牌补充速率（每秒几个）。
    capacity : int, optional
        桶容量（burst 上限）。默认 = ``int(qps)``。
    name : str
        日志标识。
    """

    def __init__(self, qps: float = 5.0, capacity: Optional[int] = None, name: str = "doc_api"):
        if qps <= 0:
            raise ValueError("qps must be positive")
        self._qps = qps
        self._refill_interval = 1.0 / qps
        self._capacity = capacity if capacity is not None else max(1, int(qps))
        self._tokens = float(self._capacity)
        self._last_refill = time.monotonic()

        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)

        # 优先级等待队列: heapq of (priority, seq, event)
        self._waiters: list[tuple[int, int, threading.Event]] = []
        self._seq = itertools.count()

        self._name = name

        # 统计（便于观测）
        self.stats_acquired = 0
        self.stats_waited_ms_total = 0.0
        self.stats_max_wait_ms = 0.0

    def _refill_locked(self) -> None:
        """必须在 _lock 持有下调用。按实际流逝时间补令牌。"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self._tokens = min(self._capacity, self._tokens + elapsed * self._qps)
        self._last_refill = now

    def acquire(self, priority: int = P_WHITEBOARD, timeout: float = 30.0) -> float:
        """获取 1 个令牌。阻塞直到拿到或超时。

        Parameters
        ----------
        priority : int
            优先级常量（P_BLOCK_API / P_WHITEBOARD / P_COMMENT），越小越先。
        timeout : float
            最大等待秒数。超时抛 ``TimeoutError``（保护逻辑，防死锁）。

        Returns
        -------
        float
            实际等待毫秒数（便于调用方观测）。
        """
        start = time.monotonic()
        deadline = start + timeout

        with self._cv:
            # 快路径：桶里有令牌且自己优先级最高
            self._refill_locked()
            if self._tokens >= 1 and (not self._waiters or priority <= self._waiters[0][0]):
                self._tokens -= 1
                waited_ms = 0.0
                self._record_stats(waited_ms)
                return waited_ms

            # 慢路径：入队等待
            my_seq = next(self._seq)
            my_event = threading.Event()
            heapq.heappush(self._waiters, (priority, my_seq, my_event))

            while True:
                # 放弃锁 + 等待通知（有新令牌到达或超时）
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # 超时 → 从队列移除，抛错
                    self._remove_waiter(my_event)
                    raise TimeoutError(
                        f"PriorityTokenBucket[{self._name}] acquire timeout after {timeout}s "
                        f"(priority={priority}, queue_len={len(self._waiters)})"
                    )

                self._refill_locked()

                # 自己是队首 + 有令牌 → 消费并离队
                if self._tokens >= 1 and self._waiters and self._waiters[0][2] is my_event:
                    heapq.heappop(self._waiters)
                    self._tokens -= 1
                    waited_ms = (time.monotonic() - start) * 1000
                    self._record_stats(waited_ms)
                    # 队首可能还有人等（不同 priority 被当前令牌满足不了），叫醒一次
                    self._cv.notify_all()
                    return waited_ms

                # 算到下个令牌还差多久
                need_tokens = 1.0 - self._tokens
                to_wait = max(0.0, need_tokens * self._refill_interval)
                # 与 deadline 取最小
                to_wait = min(to_wait, remaining)

                # 等待；其他 acquire 释放桶 / 补令牌后会 notify_all
                self._cv.wait(timeout=to_wait if to_wait > 0 else 0.001)

    def _remove_waiter(self, event: threading.Event) -> None:
        """从等待堆里安全移除一个 event（超时清理）。"""
        self._waiters = [(p, s, e) for (p, s, e) in self._waiters if e is not event]
        heapq.heapify(self._waiters)
        self._cv.notify_all()

    def _record_stats(self, waited_ms: float) -> None:
        self.stats_acquired += 1
        self.stats_waited_ms_total += waited_ms
        if waited_ms > self.stats_max_wait_ms:
            self.stats_max_wait_ms = waited_ms

    def snapshot(self) -> dict:
        """观测快照，供诊断脚本 / 日志使用。"""
        with self._lock:
            avg = (self.stats_waited_ms_total / self.stats_acquired) if self.stats_acquired else 0.0
            return {
                "name": self._name,
                "qps": self._qps,
                "capacity": self._capacity,
                "tokens_now": round(self._tokens, 2),
                "waiters": len(self._waiters),
                "acquired": self.stats_acquired,
                "avg_wait_ms": round(avg, 1),
                "max_wait_ms": round(self.stats_max_wait_ms, 1),
            }

    def reset_stats(self) -> None:
        with self._lock:
            self.stats_acquired = 0
            self.stats_waited_ms_total = 0.0
            self.stats_max_wait_ms = 0.0


# ── 进程级单例 ─────────────────────────────────────────────────────
# 飞书 99991400 是账号维度的 5 qps，多 FeishuClient 实例必须共享。
DOC_API_BUCKET = PriorityTokenBucket(qps=5.0, capacity=5, name="feishu_99991400")
