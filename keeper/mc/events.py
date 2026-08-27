"""事件模型与订阅总线（对应协议 §7）。"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Event:
    """Mod 推送的事件。name 对应协议 §7 事件名，data 为其载荷。"""

    name: str
    data: dict[str, Any] = field(default_factory=dict)


class EventBus:
    """事件订阅总线：支持同步/异步回调，回调抛错不影响其他订阅者。"""

    def __init__(self) -> None:
        self._subs: list[Callable[[Event], Any]] = []

    def subscribe(self, callback: Callable[[Event], Any]) -> None:
        if callback not in self._subs:
            self._subs.append(callback)

    def unsubscribe(self, callback: Callable[[Event], Any]) -> None:
        if callback in self._subs:
            self._subs.remove(callback)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    async def publish(self, event: Event) -> None:
        for callback in list(self._subs):
            result = callback(event)
            if inspect.isawaitable(result):
                await result
