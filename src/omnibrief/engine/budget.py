"""LLM budget guard (design-v2 §8): hard caps per run and per UTC day, shared by every topic.

Wraps any LLMProvider. When a cap is reached it raises LLMError("budget …"), which every digest
path already treats as "publish the un-summarised fallback". Attempts count, not successes:
a failing call still used quota.
"""

from __future__ import annotations

import threading
from datetime import datetime

from omnibrief.llm import LLMError, LLMProvider


class BudgetExhausted(LLMError):
    pass


class BudgetedProvider:
    name = "budgeted"

    def __init__(self, inner: LLMProvider, state: dict, now: datetime, per_run: int, per_day: int):
        self.inner = inner
        today = now.date().isoformat()
        self.state = state if state.get("date") == today else {"date": today, "calls": 0}
        self.per_run = per_run
        self.per_day = per_day
        self.run_calls = 0
        self._lock = threading.Lock()

    def generate(self, system: str, prompt: str) -> str:
        with self._lock:
            if self.run_calls >= self.per_run:
                raise BudgetExhausted(f"budget: {self.per_run} LLM calls per run used")
            if self.state["calls"] >= self.per_day:
                raise BudgetExhausted(f"budget: {self.per_day} LLM calls per day used")
            self.run_calls += 1
            self.state["calls"] += 1
        return self.inner.generate(system, prompt)
