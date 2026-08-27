"""Agent 护栏：暂停 / 急停 / 动作白名单。"""
from __future__ import annotations

import asyncio

# 允许下发给游戏的动作白名单（协议 v2.1 全部合法动作）
ALLOWED_ACTIONS = frozenset(
    {
        "move", "jump", "jump_once", "look_at", "sneak", "sprint", "swim", "fly", "fall_fly",
        "attack", "use_item", "interact_block", "interact_entity", "drop", "hotbar",
        "equip", "move_item", "craft", "chat", "get_state", "set_push",
    }
)


class SafetyGuard:
    """控制循环暂停/恢复/停止；并校验动作是否在白名单内。"""

    def __init__(self) -> None:
        self._paused = asyncio.Event()
        self._paused.set()  # 默认未暂停
        self._stopped = asyncio.Event()

    # ------------------------------------------------------------ 状态
    @property
    def paused(self) -> bool:
        return not self._paused.is_set()

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()

    # ------------------------------------------------------------ 控制
    def pause(self) -> None:
        self._paused.clear()

    def resume(self) -> None:
        self._paused.set()

    def stop(self) -> None:
        self._stopped.set()
        self._paused.set()  # 确保暂停中的循环能继续并观察到停止

    async def wait_if_paused(self) -> None:
        await self._paused.wait()

    # ------------------------------------------------------------ 白名单
    @staticmethod
    def check_action(action: str | None) -> bool:
        return action in ALLOWED_ACTIONS
