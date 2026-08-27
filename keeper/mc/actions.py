"""类型化动作构造与参数校验（对齐协议 v2.1 §5）。

每个 `build_*` 返回 `{"action": ..., "params": {...}}`，参数已本地校验，
不合法抛 `ParamError`（发送前拦截，对应协议的 103 类问题）。
"""
from __future__ import annotations

from typing import Any

from .errors import ParamError

# ---------------------------------------------------------------- 限速分类
# 对应 ratelimit.py 中类别
ACTION_CATEGORY: dict[str, str] = {
    "move": "persistent",
    "jump": "persistent",
    "jump_once": "persistent",
    "look_at": "persistent",
    "sneak": "persistent",
    "sprint": "persistent",
    "swim": "persistent",
    "fly": "persistent",
    "fall_fly": "persistent",
    "attack": "attack",
    "chat": "chat",
    "use_item": "other",
    "interact_block": "other",
    "interact_entity": "other",
    "drop": "other",
    "hotbar": "other",
    "equip": "other",
    "move_item": "other",
    "craft": "other",
    "get_state": "other",
    "set_push": "other",
}

ARMOR_NAMES = ("head", "chest", "legs", "feet")
FACES = ("up", "down", "north", "south", "east", "west")
ATTACK_MODES = ("once", "hold", "release")


# ---------------------------------------------------------------- 校验工具
def _require(params: dict[str, Any], key: str) -> Any:
    if key not in params or params[key] is None:
        raise ParamError(f"缺少必填字段 {key}")
    return params[key]


def _bool(params: dict[str, Any], key: str) -> bool:
    value = _require(params, key)
    if not isinstance(value, bool):
        raise ParamError(f"{key} 必须是布尔值")
    return value


def _num(params: dict[str, Any], key: str, lo: float, hi: float) -> float:
    value = _require(params, key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParamError(f"{key} 必须是数字")
    value = float(value)
    if not (lo <= value <= hi):
        raise ParamError(f"{key} 超出范围 {lo}..{hi}")
    return value


def _int(params: dict[str, Any], key: str, lo: int, hi: int) -> int:
    value = _require(params, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParamError(f"{key} 必须是整数")
    if not (lo <= value <= hi):
        raise ParamError(f"{key} 超出范围 {lo}..{hi}")
    return value


def _opt_int(params: dict[str, Any], key: str, lo: int, hi: int) -> int | None:
    if key not in params or params[key] is None:
        return None
    value = params[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParamError(f"{key} 必须是整数")
    if not (lo <= value <= hi):
        raise ParamError(f"{key} 超出范围 {lo}..{hi}")
    return value


def _opt_bool(params: dict[str, Any], key: str) -> bool | None:
    if key not in params or params[key] is None:
        return None
    value = params[key]
    if not isinstance(value, bool):
        raise ParamError(f"{key} 必须是布尔值")
    return value


def _opt_str(params: dict[str, Any], key: str, choices: tuple[str, ...]) -> str | None:
    if key not in params or params[key] is None:
        return None
    value = params[key]
    if not isinstance(value, str) or value not in choices:
        raise ParamError(f"{key} 非法（{'/'.join(choices)}）")
    return value


# ---------------------------------------------------------------- 动作构造
def build_move(
    forward: float = 0.0,
    backward: float = 0.0,
    left: float = 0.0,
    right: float = 0.0,
) -> dict[str, Any]:
    """移动（全量覆盖四轴）。"""
    params: dict[str, Any] = {}
    for key, value in (("forward", forward), ("backward", backward), ("left", left), ("right", right)):
        params[key] = _num({"v": value}, "v", 0.0, 1.0)
    return {"action": "move", "params": params}


def build_jump(value: bool) -> dict[str, Any]:
    return {"action": "jump", "params": {"value": _bool({"value": value}, "value")}}


def build_jump_once(ticks: int = 4) -> dict[str, Any]:
    return {"action": "jump_once", "params": {"ticks": _int({"ticks": ticks}, "ticks", 1, 20)}}


def build_look_at(
    x: float | None = None,
    y: float | None = None,
    z: float | None = None,
    entity: int | None = None,
    unlock: bool | None = None,
) -> dict[str, Any]:
    """锁定目标：{x,y,z} 或 {entity} 或 {unlock:true} / {}。"""
    params: dict[str, Any] = {}
    if unlock is not None:
        if not isinstance(unlock, bool):
            raise ParamError("unlock 必须是布尔值")
        params["unlock"] = unlock
    if entity is not None:
        if isinstance(entity, bool) or not isinstance(entity, int):
            raise ParamError("entity 必须是数字（实体 id）")
        params["entity"] = entity
    has_xyz = any(v is not None for v in (x, y, z))
    if has_xyz:
        if "entity" in params:
            raise ParamError("目标冲突：x/y/z 与 entity 不能同时提供")
        if x is None or y is None or z is None:
            raise ParamError("x/y/z 必须同时提供")
        params.update(x=float(x), y=float(y), z=float(z))
    if not params:
        return {"action": "look_at", "params": {}}
    return {"action": "look_at", "params": params}


def build_state_toggle(action: str, value: bool) -> dict[str, Any]:
    if action not in ("sneak", "sprint", "swim", "fly", "fall_fly"):
        raise ParamError(f"未知动作 {action}")
    return {"action": action, "params": {"value": _bool({"value": value}, "value")}}


def build_attack(mode: str) -> dict[str, Any]:
    if mode not in ATTACK_MODES:
        raise ParamError(f"mode 必须是 once/hold/release")
    return {"action": "attack", "params": {"mode": mode}}


def build_use_item(slot: int | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {}
    slot_v = _opt_int({"slot": slot} if slot is not None else {}, "slot", 0, 8)
    if slot_v is not None:
        params["slot"] = slot_v
    return {"action": "use_item", "params": params}


def build_interact_block(
    x: float, y: float, z: float, face: str | None = None, slot: int | None = None
) -> dict[str, Any]:
    params: dict[str, Any] = {"x": float(x), "y": float(y), "z": float(z)}
    face_v = _opt_str({"face": face} if face is not None else {}, "face", FACES)
    if face_v is not None:
        params["face"] = face_v
    slot_v = _opt_int({"slot": slot} if slot is not None else {}, "slot", 0, 8)
    if slot_v is not None:
        params["slot"] = slot_v
    return {"action": "interact_block", "params": params}


def build_interact_entity(entity_id: int, slot: int | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"id": _int({"id": entity_id}, "id", -2**31, 2**31 - 1)}
    slot_v = _opt_int({"slot": slot} if slot is not None else {}, "slot", 0, 8)
    if slot_v is not None:
        params["slot"] = slot_v
    return {"action": "interact_entity", "params": params}


def build_drop(slot: int | None = None, count: int = 1) -> dict[str, Any]:
    params: dict[str, Any] = {}
    slot_v = _opt_int({"slot": slot} if slot is not None else {}, "slot", 0, 40)
    if slot_v is not None:
        params["slot"] = slot_v
    if count < -1:
        raise ParamError("count 不能小于 -1")
    params["count"] = count
    return {"action": "drop", "params": params}


def build_hotbar(slot: int) -> dict[str, Any]:
    return {"action": "hotbar", "params": {"slot": _int({"slot": slot}, "slot", 0, 8)}}


def build_equip(slot: int, armor: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"slot": _int({"slot": slot}, "slot", 0, 35)}
    armor_v = _opt_str({"armor": armor} if armor is not None else {}, "armor", ARMOR_NAMES)
    if armor_v is not None:
        params["armor"] = armor_v
    return {"action": "equip", "params": params}


def build_move_item(from_slot: int, to_slot: int) -> dict[str, Any]:
    if from_slot == to_slot:
        raise ParamError("from 与 to 不能相同")
    return {
        "action": "move_item",
        "params": {
            "from": _int({"from": from_slot}, "from", 0, 40),
            "to": _int({"to": to_slot}, "to", 0, 40),
        },
    }


def build_craft(recipe: str, shift: bool = True) -> dict[str, Any]:
    if not isinstance(recipe, str) or not recipe:
        raise ParamError("recipe 必须是字符串（配方 id）")
    params: dict[str, Any] = {"recipe": recipe, "shift": _bool({"shift": shift}, "shift")}
    return {"action": "craft", "params": params}


def build_chat(message: str) -> dict[str, Any]:
    if not isinstance(message, str):
        raise ParamError("message 必须是字符串")
    if not message:
        raise ParamError("message 不能为空")
    if len(message) > 256:
        raise ParamError("message 超出 256 字符限制")
    return {"action": "chat", "params": {"message": message}}


def build_get_state() -> dict[str, Any]:
    return {"action": "get_state", "params": {}}


def build_set_push(pos: bool) -> dict[str, Any]:
    return {"action": "set_push", "params": {"pos": _bool({"pos": pos}, "pos")}}


# ---------------------------------------------------------------- 统一分发
def _need(params: dict[str, Any], key: str) -> Any:
    if key not in params or params[key] is None:
        raise ParamError(f"缺少必填字段 {key}")
    return params[key]


def build_action(action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """按动作名统一分发到对应构造器（供 LLM 决策解析 / Agent 使用）。"""
    p: dict[str, Any] = params or {}
    if action == "move":
        return build_move(
            forward=p.get("forward", 0.0),
            backward=p.get("backward", 0.0),
            left=p.get("left", 0.0),
            right=p.get("right", 0.0),
        )
    if action == "jump":
        return build_jump(_need(p, "value"))
    if action == "jump_once":
        return build_jump_once(p.get("ticks", 4))
    if action == "look_at":
        return build_look_at(x=p.get("x"), y=p.get("y"), z=p.get("z"), entity=p.get("entity"), unlock=p.get("unlock"))
    if action in ("sneak", "sprint", "swim", "fly", "fall_fly"):
        return build_state_toggle(action, _need(p, "value"))
    if action == "attack":
        return build_attack(_need(p, "mode"))
    if action == "use_item":
        return build_use_item(p.get("slot"))
    if action == "interact_block":
        return build_interact_block(_need(p, "x"), _need(p, "y"), _need(p, "z"), face=p.get("face"), slot=p.get("slot"))
    if action == "interact_entity":
        return build_interact_entity(_need(p, "id"), slot=p.get("slot"))
    if action == "drop":
        return build_drop(slot=p.get("slot"), count=p.get("count", 1))
    if action == "hotbar":
        return build_hotbar(_need(p, "slot"))
    if action == "equip":
        return build_equip(_need(p, "slot"), armor=p.get("armor"))
    if action == "move_item":
        return build_move_item(_need(p, "from"), _need(p, "to"))
    if action == "craft":
        return build_craft(_need(p, "recipe"), shift=p.get("shift", True))
    if action == "chat":
        return build_chat(_need(p, "message"))
    if action == "get_state":
        return build_get_state()
    if action == "set_push":
        return build_set_push(_need(p, "pos"))
    raise ParamError(f"未知动作 {action}")
