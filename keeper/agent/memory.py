"""目标管理与短期记忆。"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


class GoalManager:
    """持有当前自然语言目标及其完成状态。"""

    def __init__(self, initial_goal: str = "") -> None:
        self._goal = initial_goal
        self._done = False

    @property
    def goal(self) -> str:
        return self._goal

    @property
    def done(self) -> bool:
        return self._done

    def set_goal(self, goal: str) -> None:
        self._goal = goal
        self._done = False

    def mark_done(self) -> None:
        self._done = True

    def clear(self) -> None:
        self._goal = ""
        self._done = False


@dataclass
class HistoryItem:
    role: str  # "user" | "assistant"
    content: str
    ts: float = field(default_factory=time.time)


class ShortMemory:
    """最近对话轮次的有限窗口记忆。"""

    def __init__(self, maxlen: int = 10) -> None:
        self._items: deque[HistoryItem] = deque(maxlen=maxlen)

    def add_user(self, content: str) -> None:
        self._items.append(HistoryItem("user", content))

    def add_assistant(self, content: str) -> None:
        self._items.append(HistoryItem("assistant", content))

    def clear(self) -> None:
        self._items.clear()

    @property
    def count(self) -> int:
        return len(self._items)

    def messages(self) -> list[dict[str, str]]:
        return [{"role": item.role, "content": item.content} for item in self._items]
