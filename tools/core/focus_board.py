"""FocusBoard (P4)——焦点级满足追踪 + 早停信号源。

每条 chunk 在 source_agent 内 eval_only/plan_eval 之后回填本 board；
一旦 focus 的 critical / important 计数达到 ``focus_quota`` 即标 SATISFIED。

设计要点：
- asyncio 安全：每 focus 一把 Lock，幂等更新。
- 不持有 chunks 引用，仅持计数与状态——避免重复内存。
- 提供 satisfied / unsatisfied 查询给 source_agent（plan_eval focus-aware）
  与 retrieve_agent（流式 merge / 取消信号 — P5/P6）。

P4 仅落地"观察 + plan_eval focus-aware"两件事，**不**在此触发 cancel。
P5 会复用同一个 board，在 I/O 提交点读 ``is_satisfied`` 决定是否提交。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set


logger = logging.getLogger(__name__)


FocusStatus = str  # "pending" | "satisfied" | "noise_only" | "timeout"


@dataclass
class FocusState:
    focus_id: str
    text: str
    targets: List[str] = field(default_factory=list)         # 路由到的源列表
    critical: int = 0
    important: int = 0
    noise: int = 0
    contributors: Set[str] = field(default_factory=set)      # 已贡献过 chunk 的 source 集合
    status: FocusStatus = "pending"                          # 状态机


class FocusBoard:
    """焦点级满足追踪。

    用法：
        board = FocusBoard.from_focuses(focuses, quota={"critical_min":2,"important_min":0})
        # 在 source_agent eval_only 之后：
        await board.report(source="feishu", chunks=top_chunks)
        # 在 plan_eval / 早停判定时：
        if board.is_satisfied("focus_1"): ...

    线程模型：仅在 asyncio 单事件循环内使用（非跨进程）。
    """

    def __init__(self, quota: Dict[str, int]):
        self._quota = {
            "critical_min": max(0, int(quota.get("critical_min", 0))),
            "important_min": max(0, int(quota.get("important_min", 0))),
        }
        self._states: Dict[str, FocusState] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        # 每 focus 一个满足事件，供 P5 in-flight cancel 用 asyncio.wait 监听（事件驱动取代轮询）
        self._satisfied_events: Dict[str, asyncio.Event] = {}

    # ── 工厂 ──────────────────────────────────────────────────
    @classmethod
    def from_focuses(cls, focuses: Iterable, quota: Dict[str, int]) -> "FocusBoard":
        board = cls(quota)
        for f in focuses:
            fid = getattr(f, "focus_id", None)
            if not fid:
                continue
            board._states[fid] = FocusState(
                focus_id=fid,
                text=getattr(f, "text", ""),
                targets=list(getattr(f, "targets", []) or []),
            )
            board._locks[fid] = asyncio.Lock()
            board._satisfied_events[fid] = asyncio.Event()
        return board

    def satisfied_event(self, focus_id: str) -> Optional[asyncio.Event]:
        """获取 focus 满足事件；用于 asyncio.wait 监听。"""
        return self._satisfied_events.get(focus_id)

    # ── 查询 API ───────────────────────────────────────────────
    @property
    def quota(self) -> Dict[str, int]:
        return dict(self._quota)

    def focus_ids(self) -> List[str]:
        return list(self._states.keys())

    def state(self, focus_id: str) -> Optional[FocusState]:
        return self._states.get(focus_id)

    def is_satisfied(self, focus_id: str) -> bool:
        s = self._states.get(focus_id)
        return bool(s and s.status == "satisfied")

    def all_satisfied(self) -> bool:
        return bool(self._states) and all(
            s.status == "satisfied" for s in self._states.values()
        )

    def unsatisfied_focus_ids(self) -> List[str]:
        return [fid for fid, s in self._states.items() if s.status != "satisfied"]

    def snapshot(self) -> Dict[str, Dict[str, object]]:
        """plan_eval prompt 用：把整盘状态序列化为 LLM 可读 JSON-like dict。"""
        return {
            fid: {
                "status": s.status,
                "critical": s.critical,
                "important": s.important,
                "noise": s.noise,
                "contributors": sorted(s.contributors),
                "targets": list(s.targets),
            }
            for fid, s in self._states.items()
        }

    # ── 写入 API ───────────────────────────────────────────────
    async def report(self, source: str, chunks: Iterable) -> List[str]:
        """source_agent 输出 top_chunks 后调用，回填 focus 计数。

        返回本次 report 中**新升格为 satisfied** 的 focus_id 列表（供调用方决定
        是否广播取消信号；P4 阶段先只用作日志）。

        被一条 chunk 命中的 focus 集合：优先取 metadata["answered_focuses"]，
        否则 fall back 到 c.target_focuses。
        """
        # 先按 focus_id 聚合本次新增计数，再分别取 lock 更新（避免长持锁）
        delta: Dict[str, Dict[str, int]] = {}
        for c in chunks:
            tier = getattr(c, "tier", None)
            if tier not in ("critical", "important", "noise"):
                continue
            answered = list((getattr(c, "metadata", None) or {}).get("answered_focuses") or [])
            focus_ids = answered or list(getattr(c, "target_focuses", set()) or set())
            for fid in focus_ids:
                if fid not in self._states:
                    continue
                d = delta.setdefault(fid, {"critical": 0, "important": 0, "noise": 0})
                d[tier] = d.get(tier, 0) + 1

        newly_satisfied: List[str] = []
        for fid, d in delta.items():
            lock = self._locks[fid]
            async with lock:
                s = self._states[fid]
                s.critical += d.get("critical", 0)
                s.important += d.get("important", 0)
                s.noise += d.get("noise", 0)
                s.contributors.add(source)
                old_status = s.status
                if old_status != "satisfied" and self._meets_quota(s):
                    s.status = "satisfied"
                    newly_satisfied.append(fid)
                    logger.info(
                        "FocusBoard: %s SATISFIED by %s (critical=%d important=%d quota=%s)",
                        fid, source, s.critical, s.important, self._quota,
                    )
                    # 触发事件 → 其他源监听该 focus 的 in-flight 任务可立即取消
                    evt = self._satisfied_events.get(fid)
                    if evt is not None:
                        evt.set()
        return newly_satisfied

    def mark_terminal(self, focus_id: str, status: FocusStatus = "noise_only") -> None:
        """retrieve_agent 兜底用：所有源完成后仍未满足的 focus 标终态。"""
        s = self._states.get(focus_id)
        if s and s.status != "satisfied":
            s.status = status

    # ── 内部 ───────────────────────────────────────────────────
    def _meets_quota(self, s: FocusState) -> bool:
        return (
            s.critical >= self._quota["critical_min"]
            and s.important >= self._quota["important_min"]
        )
