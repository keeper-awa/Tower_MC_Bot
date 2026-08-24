#!/usr/bin/env python3
"""大脑工具集：LLM 可调用的动作（对应 Tower 协议全部动作 + 记忆管理）。

每个工具 = name + JSON schema（LLM 看 description 决定参数）+ 执行函数（调 TowerClient）。
执行结果统一转字符串回喂给 LLM（截断 2000 字符，防上下文膨胀）。
"""

import json
import logging
import time
from pathlib import Path

log = logging.getLogger("brain.tools")


def _s(**props):
    return {"type": "object", "properties": props}


def sanitize_chat(text: str) -> str:
    """净化聊天内容：剔除 Minecraft 不允许的字符（否则服务端踢人 非法聊天字符）。

    允许范围对齐原版 SharedConstants.isAllowedChatCharacter：
    ASCII、CJK、日韩假名、常用符号区等；emoji（辅助平面，如 🌲🪵👍）全部剔除。
    """
    out = []
    for ch in text:
        code = ord(ch)
        ok = (0x20 <= code <= 0xFF
              or 0x300 <= code <= 0x36F
              or 0x2000 <= code <= 0x206F
              or 0x20A0 <= code <= 0x20CF
              or 0x20D0 <= code <= 0x20FF
              or 0x2100 <= code <= 0x214F
              or 0x2600 <= code <= 0x27BF
              or 0x2B00 <= code <= 0x2BFF
              or 0x3000 <= code <= 0x303F
              or 0x3040 <= code <= 0x309F
              or 0x30A0 <= code <= 0x30FF
              or 0x3400 <= code <= 0x4DBF
              or 0x4E00 <= code <= 0x9FFF
              or 0xA000 <= code <= 0xA4CF
              or 0xAC00 <= code <= 0xD7A3
              or 0xF900 <= code <= 0xFAFF
              or 0xFE30 <= code <= 0xFE4F
              or 0xFF00 <= code <= 0xFF60
              or 0xFFE0 <= code <= 0xFFE6)
        if ok:
            out.append(ch)
    cleaned = "".join(out).strip()
    if cleaned != text:
        log.info("聊天内容已净化（剔除非法字符 %d 个）", len(text) - len(cleaned))
    return cleaned


# ── 工具定义（schema）──────────────────────────────────────────────
TOOL_DEFS = [
    # 感知
    {"type": "function", "function": {"name": "get_state", "description": "获取完整状态快照：位置/血量/饥饿/背包/经验/生物群系", "parameters": _s()}},
    {"type": "function", "function": {"name": "raycast", "description": "准星射线扫描：返回准星指到的方块/实体（含距离与命中面）", "parameters": _s(distance={"type": "integer", "description": "射线长度 4-64，默认 10"}, through_liquid={"type": "boolean", "description": "是否穿过液体"})}},
    {"type": "function", "function": {"name": "get_blocks", "description": "获取周围非空气方块列表，附脚下/面前/头上摘要", "parameters": _s(radius={"type": "integer", "description": "半径 1-16，默认 8"}, max={"type": "integer", "description": "最大数量 1-512，默认 512"})}},
    {"type": "function", "function": {"name": "get_entities", "description": "获取附近实体列表（怪物/动物/掉落物，含血量与敌意标记）", "parameters": _s(radius={"type": "integer", "description": "半径 1-32，默认 16"}, type={"type": "string", "description": "过滤实体类型如 minecraft:zombie"})}},
    {"type": "function", "function": {"name": "screenshot", "description": "截取当前画面并返回文件路径（如需看图配合 look_at 调整视角）", "parameters": _s()}},
    # 导航
    {"type": "function", "function": {"name": "move_to", "description": "自动驾驶到指定坐标（原版寻路，自动开门/跳台阶）。执行后等待 path_reached 事件确认到达。跨深水（河/海）时需 allow_water:true 放行寻路", "parameters": _s(x={"type": "integer", "description": "目标 X"}, y={"type": "integer", "description": "目标 Y"}, z={"type": "integer", "description": "目标 Z"}, mode={"type": "string", "enum": ["auto", "waypoints"], "description": "auto=自动驾驶（默认）；waypoints=只返回路径"}, allow_water={"type": "boolean", "description": "是否允许路径穿越深水，默认 false"}, precision={"type": "number", "description": "到达判定距离 0.5-4.0，默认 1.5"})}},
    {"type": "function", "function": {"name": "cancel_navigation", "description": "取消当前导航（紧急情况用）", "parameters": _s()}},
    # 移动/姿态
    {"type": "function", "function": {"name": "move", "description": "持续移动控制（0/1 值；想停止传 0）", "parameters": _s(forward={"type": "number", "description": "前进 0-1"}, backward={"type": "number"}, left={"type": "number"}, right={"type": "number"})}},
    {"type": "function", "function": {"name": "jump", "description": "持续跳跃（value=false 停止）", "parameters": _s(value={"type": "boolean"})}},
    {"type": "function", "function": {"name": "jump_once", "description": "跳一次（过障碍用）", "parameters": _s()}},
    {"type": "function", "function": {"name": "look_at", "description": "视角锁定/解锁：坐标或实体；空参数=解锁。技巧：想朝某方向走时，锁定该方向 20~30 格外的空气方块坐标，视角即持续对准行走方向（配合 move 前进）", "parameters": _s(x={"type": "integer"}, y={"type": "integer"}, z={"type": "integer"}, entity={"type": "integer", "description": "实体 id（与坐标二选一）"})}},
    {"type": "function", "function": {"name": "sneak", "description": "潜行开关", "parameters": _s(value={"type": "boolean"})}},
    {"type": "function", "function": {"name": "sprint", "description": "疾跑开关", "parameters": _s(value={"type": "boolean"})}},
    # 交互
    {"type": "function", "function": {"name": "attack", "description": "攻击/挖掘：once 单次；hold 持续（直到 release）", "parameters": _s(mode={"type": "string", "enum": ["once", "hold", "release"]})}},
    {"type": "function", "function": {"name": "use_item", "description": "右键使用手持物品（吃食物/放方块等）", "parameters": _s()}},
    {"type": "function", "function": {"name": "interact_block", "description": "右键方块（开门/开箱/放方块需对准）", "parameters": _s(x={"type": "integer"}, y={"type": "integer"}, z={"type": "integer"}, slot={"type": "integer", "description": "使用前切换的槽位"})}},
    {"type": "function", "function": {"name": "interact_entity", "description": "右键实体（喂食/交易/骑乘）", "parameters": _s(entity={"type": "integer", "description": "实体 id"}, slot={"type": "integer"})}},
    {"type": "function", "function": {"name": "hotbar", "description": "切换手持槽位 0-8", "parameters": _s(slot={"type": "integer"})}},
    {"type": "function", "function": {"name": "drop", "description": "丢弃物品", "parameters": _s(slot={"type": "integer"}, count={"type": "integer"})}},
    # 背包
    {"type": "function", "function": {"name": "equip", "description": "穿戴装备（slot 0-40：0-8 工具栏 9-35 背包 36-39 盔甲 40 副手）", "parameters": _s(slot={"type": "integer"})}},
    {"type": "function", "function": {"name": "move_item", "description": "移动物品（from/to 均为 0-40 槽位）", "parameters": _s(from_slot={"type": "integer"}, to_slot={"type": "integer"}, count={"type": "integer"})}},
    {"type": "function", "function": {"name": "craft", "description": "合成物品（recipe 配方 id；需先打开工作台）", "parameters": _s(recipe={"type": "string", "description": "配方 id 如 minecraft:planks"}, shift={"type": "boolean"})}},
    # 聊天
    {"type": "function", "function": {"name": "chat", "description": "在游戏聊天中说话（与其他玩家/AI 交流）", "parameters": _s(message={"type": "string"})}},
    # 记忆（大脑自有；M5.2 起 AI 不再自设长期目标，仅记录经验教训）
    {"type": "function", "function": {"name": "update_memory", "description": "记录经验教训到 memory.md（追加一条带编号；如任务中新学到的技巧/踩坑）", "parameters": _s(section={"type": "string", "enum": ["lesson"]}, content={"type": "string", "description": "要记录的内容（一行）"})}},
]


class Toolset:
    """工具执行器：name → 调用 TowerClient 的动作。"""

    def __init__(self, client, brain_cfg: dict, memory):
        self.client = client
        self.brain_cfg = brain_cfg
        self.memory = memory
        self.sent_chats = []  # [(message, 时间戳)]：AI 最近发送的聊天，用于过滤自身回显

    def is_echo(self, event_data) -> bool:
        """是否 AI 自己刚发的消息回显。

        chat 工具以玩家身份发送 → 回显事件与玩家消息在协议层面无法区分
        （self 恒为 true），只能按文本匹配：与最近 120s 内 AI 发过的消息
        一致的视为回显（玩家恰好重复同一句话的极小概率可接受）。
        """
        msg = (event_data or {}).get("message", "")
        if not msg:
            return False
        now = time.time()
        for m, ts in reversed(self.sent_chats):
            if now - ts > 120:
                continue
            if m == msg:
                return True
        return False

    @property
    def schemas(self):
        return TOOL_DEFS

    def execute(self, name: str, args: dict) -> str:
        """执行工具，返回给 LLM 的结果文本（统一字符串，截断 2000 字符）。"""
        try:
            fn = getattr(self, "do_" + name)
            result = fn(args)
        except Exception as e:
            result = f"工具执行失败: {e}"
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        if len(text) > 2000:
            text = text[:2000] + "...(截断)"
        return text

    # ── 感知 ─────────────────────────────────────────────────────
    def do_get_state(self, args):
        return self.client.ok("get_state")

    def do_raycast(self, args):
        return self.client.ok("raycast", args)

    def do_get_blocks(self, args):
        return self.client.ok("get_blocks", args)

    def do_get_entities(self, args):
        return self.client.ok("get_entities", args)

    def do_screenshot(self, args):
        return self.client.ok("screenshot", args)

    # ── 导航 ─────────────────────────────────────────────────────
    def do_move_to(self, args):
        return self.client.ok("move_to", args)

    def do_cancel_navigation(self, args):
        return self.client.ok("move_to", {"cancel": True})

    # ── 移动/姿态 ─────────────────────────────────────────────────
    def do_move(self, args):
        return self.client.ok("move", args)

    def do_jump(self, args):
        return self.client.ok("jump", args)

    def do_jump_once(self, args):
        return self.client.ok("jump_once", args)

    def do_look_at(self, args):
        return self.client.ok("look_at", args)

    def do_sneak(self, args):
        return self.client.ok("sneak", args)

    def do_sprint(self, args):
        return self.client.ok("sprint", args)

    # ── 交互 ─────────────────────────────────────────────────────
    def do_attack(self, args):
        return self.client.ok("attack", args)

    def do_use_item(self, args):
        return self.client.ok("use_item", args)

    def do_interact_block(self, args):
        return self.client.ok("interact_block", args)

    def do_interact_entity(self, args):
        return self.client.ok("interact_entity", args)

    def do_hotbar(self, args):
        return self.client.ok("hotbar", args)

    def do_drop(self, args):
        return self.client.ok("drop", args)

    # ── 背包 ─────────────────────────────────────────────────────
    def do_equip(self, args):
        return self.client.ok("equip", args)

    def do_move_item(self, args):
        return self.client.ok("move_item", args)

    def do_craft(self, args):
        return self.client.ok("craft", args)

    # ── 聊天 ─────────────────────────────────────────────────────
    def do_chat(self, args):
        msg = sanitize_chat(str(args.get("message", "")))
        if msg:
            self.sent_chats.append((msg, time.time()))
            if len(self.sent_chats) > 20:
                self.sent_chats.pop(0)
        return self.client.ok("chat", {"message": msg})

    # ── 记忆 ─────────────────────────────────────────────────────
    def do_update_memory(self, args):
        content = args.get("content", "")
        if args.get("section") != "lesson" or not content.strip():
            return "参数错误：section 必须为 lesson，content 不能为空"
        return self.memory.append("lesson", content)
