#!/usr/bin/env python3
"""技能共用工具：背包遍历 / 方块查找（`_` 前缀文件，不注册为技能）。"""


def iter_items(state):
    """遍历背包全部物品，yield {slot, id, count}（slots 0-35 + armor 36-39 + offhand 40）。"""
    inv = state.get("inventory", {}) or {}
    for entry in inv.get("slots", []) + inv.get("armor", []):
        if entry.get("id"):
            yield entry
    off = inv.get("offhand")
    if off and off.get("id"):
        yield off


def count_items(state, suffix=None, exact=None) -> int:
    """统计物品总数（按 id 后缀或精确 id 匹配）。"""
    total = 0
    for entry in iter_items(state):
        iid = entry.get("id", "")
        if exact and iid == exact:
            total += entry.get("count", 1)
        elif suffix and iid.endswith(suffix):
            total += entry.get("count", 1)
    return total


def find_slot(state, suffix) -> int:
    """找第一个 id 以 suffix 结尾的物品槽位，返回 slot 或 None。"""
    for entry in iter_items(state):
        if entry.get("id", "").endswith(suffix):
            return entry["slot"]
    return None


def is_log(iid: str) -> bool:
    """是否原木类方块（含 _log/_wood，排除去皮 stripped_ 变体——去皮原木不掉原木）。"""
    return (iid.endswith("_log") or iid.endswith("_wood")) and not iid.startswith("stripped_")


def player_pos(state) -> dict:
    """玩家位置 {x, y, z}（整数化）。"""
    p = state.get("player", {}).get("position", {})
    return {"x": int(p.get("x", 0)), "y": int(p.get("y", 0)), "z": int(p.get("z", 0))}
