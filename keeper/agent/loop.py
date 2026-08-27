"""Agent 决策循环：观测 → 上下文 → LLM → 动作 → 执行。

循环主体 `AgentLoop`：
- 每轮 `run_once()` 采集状态 + 事件，组装观测文本，调用 LLM，解析动作并下发。
- 提供 `pause / resume / stop / set_goal` 控制与 `on_decision` 回调（供日志/面板）。
- 事件通过订阅 client 的事件总线缓冲（最多 N 条，观测时取最近若干条）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import AgentConfig
from ..llm.parse import Decision, ParseError, parse_decision
from ..llm.prompts import build_messages, system_prompt
from ..llm.provider import LLMProvider
from ..mc.client import KeyboardClient
from ..mc.events import Event
from .context import build_observation
from .memory import GoalManager, ShortMemory
from .safety import SafetyGuard

logger = logging.getLogger(__name__)


@dataclass
class DecisionRecord:
    """一轮决策的完整记录（供日志/管理面板）。"""

    ts: float
    observation: str
    llm_output: str = ""
    action: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    think: str = ""
    result: Any = None
    error: str | None = None
    latency: float | None = None  # LLM 调用耗时（秒）


DecisionCallback = Callable[[DecisionRecord], None]


class AgentLoop:
    """LLM 驱动的决策循环。"""

    def __init__(
        self,
        client: KeyboardClient,
        provider: LLMProvider,
        config: AgentConfig,
        goal: str = "",
        on_decision: DecisionCallback | None = None,
    ) -> None:
        self._client = client
        self._provider = provider
        self._cfg = config
        self._safety = SafetyGuard()
        self._goals = GoalManager(goal)
        self._on_decision = on_decision
        self._system = system_prompt()
        self._events: deque[Event] = deque(maxlen=50)
        self._client.on_event(self._events.append)
        self._memory = ShortMemory(maxlen=config.memory_len)
        self._last_key: tuple | None = None
        self._repeat = 0

    # ------------------------------------------------------------ 控制
    def pause(self) -> None:
        self._safety.pause()

    def resume(self) -> None:
        self._safety.resume()

    def stop(self) -> None:
        self._safety.stop()

    def set_goal(self, goal: str) -> None:
        self._goals.set_goal(goal)

    @property
    def safety(self) -> SafetyGuard:
        return self._safety

    @property
    def goal(self) -> str:
        return self._goals.goal

    def recent_events(self, n: int = 8) -> list[dict[str, Any]]:
        return [{"name": e.name, "data": e.data} for e in self._events][-n:]

    # ------------------------------------------------------------ 周期
    async def run_once(self) -> DecisionRecord:
        """执行一个「观测 → 决策 → 动作」周期，返回记录。"""
        state = await self._client.get_state()
        observation = build_observation(state, self.recent_events(), self._goals.goal)
        record = DecisionRecord(ts=time.time(), observation=observation)
        decision: Decision | None = None

        try:
            messages = build_messages(self._system, observation, self._memory.messages())
            t0 = time.monotonic()
            llm_text = await self._chat_with_retry(messages)
            record.latency = round(time.monotonic() - t0, 2)
            record.llm_output = llm_text

            decision = parse_decision(llm_text)
            record.action = decision.action
            record.params = decision.params
            record.think = decision.think

            if decision.action is not None:
                if not self._safety.check_action(decision.action):
                    record.error = f"动作不在白名单: {decision.action}"
                elif not self._check_repeat(decision.action, decision.params):
                    record.error = f"护栏: 相同动作重复执行超过 {self._cfg.max_repeat} 次"
                elif self._health_guard(state, decision):
                    record.error = f"护栏: 生命过低({state['player'].get('health')})，拦截移动类动作"
                    decision = Decision(None, {}, think="生命过低，停止移动自保")
                else:
                    record.result = await self._client.request(decision.action, decision.params)
        except ParseError as exc:
            record.error = f"parse: {exc}"
            logger.warning("LLM 输出解析失败: %s", exc)
        except Exception as exc:  # noqa: BLE001
            record.error = f"{type(exc).__name__}: {exc}"
            logger.exception("决策周期异常")

        # 更新短期记忆（把本轮的观测与决策回填，供下轮参考）
        if decision is not None:
            self._memory.add_user(observation)
            summary = f"[决策] think={record.think or ''} action={record.action or '无'} params={record.params or {}}"
            if record.result is not None:
                summary += f" 结果={record.result}"
            if record.error:
                summary += f" 错误={record.error}"
            self._memory.add_assistant(summary)

        if self._on_decision is not None:
            try:
                self._on_decision(record)
            except Exception:  # noqa: BLE001
                logger.exception("on_decision 回调异常")
        return record

    # ------------------------------------------------------------ 打磨辅助
    async def _chat_with_retry(self, messages: list[dict[str, str]]) -> str:
        """调用 LLM；空输出时按配置重试。"""
        text = await self._provider.chat(messages)
        retries = 0
        while not text.strip() and retries < self._cfg.empty_retries:
            retries += 1
            logger.warning("LLM 返回空内容，重试 %d/%d", retries, self._cfg.empty_retries)
            text = await self._provider.chat(messages)
        return text

    def _check_repeat(self, action: str, params: dict) -> bool:
        """相同动作+参数连续执行护栏；超限返回 False。"""
        key = (action, tuple(sorted((k, str(v)) for k, v in params.items())))
        if key == self._last_key:
            self._repeat += 1
        else:
            self._last_key = key
            self._repeat = 1
        return self._repeat <= self._cfg.max_repeat

    def _health_guard(self, state: dict, decision: Decision) -> bool:
        """生命过低时拦截移动类动作。"""
        if decision.action not in ("move", "jump", "sprint", "fly"):
            return False
        health = float((state.get("player") or {}).get("health", 20.0))
        return health < self._cfg.low_health

    # ------------------------------------------------------------ 主循环
    async def run(self) -> None:
        logger.info("agent 循环启动，目标=%r", self._goals.goal)
        while not self._safety.stopped:
            await self._safety.wait_if_paused()
            if self._safety.stopped:
                break
            try:
                await self.run_once()
            except Exception as exc:  # noqa: BLE001
                logger.error("循环异常: %s", exc)
                await asyncio.sleep(1.0)
            await asyncio.sleep(self._cfg.loop_interval_s)
        logger.info("agent 循环结束")
