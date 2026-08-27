"""观测上下文组装：把玩家状态 + 事件 + 目标转成给 LLM 的文本。

感知缺口说明：v2.1 协议无方块/实体列表、无截图，MVP 只能拿到玩家自身
状态与事件。这里保持纯函数、输入输出明确，后续接入 get_blocks / 截图时
只需扩展 build_observation（新增感知段），不影响调用方。
"""
from __future__ import annotations

from typing import Any


def format_state(snapshot: dict[str, Any]) -> str:
    """把 get_state 快照格式化成紧凑文本。"""
    player: dict[str, Any] = snapshot.get("player", {})
    pos: dict[str, Any] = player.get("position", {})
    rot: dict[str, Any] = player.get("rotation", {})
    abilities: dict[str, Any] = player.get("abilities", {})
    parts = [
        f"位置(x={pos.get('x', 0.0):.2f},y={pos.get('y', 0.0):.2f},z={pos.get('z', 0.0):.2f})",
        f"朝向(yaw={rot.get('yaw', 0.0):.1f},pitch={rot.get('pitch', 0.0):.1f})",
        f"生命{player.get('health', 0)}/20",
        f"饥饿{player.get('food', 0)}/20",
        f"模式{player.get('gamemode', '?')}",
        f"维度{player.get('dimension', '?')}",
        f"着地{'是' if player.get('on_ground') else '否'}",
        f"时间{snapshot.get('world', {}).get('time_of_day', 0)}",
        f"飞行{'中' if abilities.get('flying') else '否'}",
    ]
    return " ".join(parts)


def format_events(events: list[dict[str, Any]], limit: int = 8) -> str:
    """把最近事件格式化成一行摘要。"""
    if not events:
        return "无"
    lines: list[str] = []
    for ev in events[-limit:]:
        name = ev.get("name", "?")
        data: dict[str, Any] = ev.get("data", {}) or {}
        if name == "chat":
            lines.append(f"聊天[{data.get('sender')}]: {data.get('message')}")
        elif name == "damage":
            lines.append(f"受伤({data.get('amount')} by {data.get('source')})")
        elif name == "death":
            lines.append("玩家死亡")
        elif name == "respawn":
            lines.append(f"重生({data.get('dimension')})")
        elif name == "game_mode":
            lines.append(f"模式变化->{data.get('mode')}")
        elif name == "mine":
            lines.append(f"挖掘中 {data.get('progress', 0.0):.0%}")
        elif name == "mine_done":
            lines.append(f"挖掘完成 destroyed={data.get('destroyed')}")
        elif name == "player_ready":
            lines.append("进入世界")
        else:
            lines.append(f"{name}{data}")
    return "；".join(lines)


def build_observation(
    snapshot: dict[str, Any],
    events: list[dict[str, Any]],
    goal: str = "",
) -> str:
    """组装完整观测文本。"""
    lines = ["【玩家状态】" + format_state(snapshot)]
    if goal:
        lines.append("【当前目标】" + goal)
    lines.append("【最近事件】" + format_events(events))
    return "\n".join(lines)
