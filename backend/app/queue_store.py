"""In-memory ticket store + urgency-ordered work queue.

Ordering: (effective urgency, arrival sequence). Arrival order breaks ties, so
within a level it's FIFO.

Aging: a waiting ticket is promoted one level per `aging_seconds`, so a steady
stream of high-priority tickets can't starve low ones forever. Aging stops at
'high'. A ticket that only got old never outranks a genuinely critical one.

Selection is an O(n) scan over pending tickets. Fine for an in-memory demo;
at scale you'd use one FIFO per level and do aging with a periodic sweep.
"""
from __future__ import annotations

import itertools
import threading
import time
from typing import Callable

from .triage import LEVELS

LEVEL_NAMES = {v: k for k, v in LEVELS.items()}


class TicketStore:
    def __init__(self, aging_seconds: float = 30, clock: Callable[[], float] = time.time):
        self.aging_seconds = aging_seconds
        self.clock = clock
        self._cond = threading.Condition()
        self._tickets: dict[str, dict] = {}
        self._pending: list[str] = []
        self._seq = itertools.count(1)

    # -- intake -------------------------------------------------------------
    def submit(self, text: str, urgency: str, urgency_reason: str) -> dict:
        with self._cond:
            seq = next(self._seq)
            tid = f"T{seq:04d}"
            self._tickets[tid] = {
                "id": tid, "seq": seq, "text": text,
                "urgency": urgency, "urgency_reason": urgency_reason,
                "status": "queued", "submitted_at": self.clock(),
                "started_at": None, "completed_at": None, "processed_order": None,
                "decision": None, "category": None, "reason": None,
                "action": None, "action_result": None, "scores": None, "matched": None,
            }
            self._pending.append(tid)
            self._cond.notify()
            return self._view(tid)

    # -- ordering -----------------------------------------------------------
    def _effective_level(self, t: dict, now: float) -> int:
        base = LEVELS[t["urgency"]]
        if base == 0 or not self.aging_seconds:
            return base
        bumps = int((now - t["submitted_at"]) // self.aging_seconds)
        return max(1, base - bumps)

    def _ordered_pending(self) -> list[str]:
        now = self.clock()
        return sorted(self._pending,
                      key=lambda i: (self._effective_level(self._tickets[i], now),
                                     self._tickets[i]["seq"]))

    # -- worker side --------------------------------------------------------
    def next_ticket(self, timeout: float | None = None) -> dict | None:
        """Block until a ticket is pending, then claim the most urgent one."""
        with self._cond:
            if not self._cond.wait_for(lambda: self._pending, timeout=timeout):
                return None
            tid = self._ordered_pending()[0]
            self._pending.remove(tid)
            t = self._tickets[tid]
            t["status"] = "processing"
            t["started_at"] = self.clock()
            t["processed_order"] = sum(
                1 for x in self._tickets.values() if x["started_at"] is not None)
            return self._view(tid)

    def complete(self, tid: str, result: dict) -> None:
        with self._cond:
            t = self._tickets[tid]
            t.update({k: result.get(k) for k in
                      ("decision", "category", "reason", "action",
                       "action_result", "scores", "matched")})
            t["status"] = "done"
            t["completed_at"] = self.clock()

    # -- reads --------------------------------------------------------------
    def _view(self, tid: str) -> dict:
        t = dict(self._tickets[tid])
        if t["status"] == "queued":
            order = self._ordered_pending()
            t["queue_position"] = order.index(tid) + 1
            t["effective_urgency"] = LEVEL_NAMES[self._effective_level(t, self.clock())]
        else:
            t["queue_position"] = None
            t["effective_urgency"] = t["urgency"]
        return t

    def get(self, tid: str) -> dict | None:
        with self._cond:
            return self._view(tid) if tid in self._tickets else None

    def list(self) -> list[dict]:
        with self._cond:
            return [self._view(tid) for tid in
                    sorted(self._tickets, key=lambda i: -self._tickets[i]["seq"])]
