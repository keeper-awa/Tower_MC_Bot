"""协议 §9 速率限制。

分类：
- general    总消息 100 msg/s（所有请求都先经过）
- persistent move/jump/look_at 等持续类 20 msg/s
- attack     20 msg/s
- chat       间隔 ≥800ms
- other      其余瞬时动作 20 msg/s
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


class TokenBucket:
    """按速率补充令牌的桶。"""

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        self.rate = rate
        self.capacity = capacity if capacity is not None else rate
        self._tokens = float(self.capacity)
        self._updated = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
        self._updated = now

    async def acquire(self, amount: float = 1.0) -> None:
        while True:
            self._refill()
            if self._tokens >= amount:
                self._tokens -= amount
                return
            need = amount - self._tokens
            await asyncio.sleep(need / self.rate)


@dataclass
class Cooldown:
    """最小间隔限制（用于 chat）。"""

    interval: float

    def __post_init__(self) -> None:
        self._last = 0.0

    async def wait(self) -> None:
        now = time.monotonic()
        wait = self._last + self.interval - now
        if wait > 0:
            await asyncio.sleep(wait)
        self._last = time.monotonic()


# 协议 §9 默认速率
GENERAL_RATE = 100.0
PERSISTENT_RATE = 20.0
ATTACK_RATE = 20.0
OTHER_RATE = 20.0
CHAT_INTERVAL = 0.8


class RateLimiter:
    """按动作分类限速。"""

    def __init__(
        self,
        general_rate: float = GENERAL_RATE,
        persistent_rate: float = PERSISTENT_RATE,
        attack_rate: float = ATTACK_RATE,
        other_rate: float = OTHER_RATE,
        chat_interval: float = CHAT_INTERVAL,
    ) -> None:
        self._buckets = {
            "persistent": TokenBucket(persistent_rate),
            "attack": TokenBucket(attack_rate),
            "other": TokenBucket(other_rate),
        }
        self._general = TokenBucket(general_rate)
        self._chat = Cooldown(chat_interval)

    async def acquire(self, category: str = "other") -> None:
        if category == "chat":
            await self._chat.wait()
        elif category in self._buckets:
            await self._buckets[category].acquire()
        await self._general.acquire()
