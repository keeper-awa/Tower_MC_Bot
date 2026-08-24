#!/usr/bin/env python3
"""砍树技能：找树 → 切斧头 → 走过去 → 砍倒 → 验证原木入背包。

原 markdown 技能（skills/mine_wood.md）步骤翻译为代码：
1. get_blocks 找最近 log 方块坐标（半径 16）
2. 切斧头（快捷栏有则切，背包有则移到快捷栏，没有空手）
3. move_to 树旁（等 path_reached 确认到达）
4. look_at 对准树干 → attack hold → 等 mine_done → attack release
5. 顺带砍掉紧邻的相连原木（最多 8 块）
6. get_state 验证背包原木数量增加
"""

import logging

from ._util import count_items, find_slot, is_log, player_pos
from .base import Skill

log = logging.getLogger("brain.skills")

MAX_MINE = 8          # 单次技能最多砍的方块数
FIND_RADIUS = 16      # 找树半径


class MineWoodSkill(Skill):
    name = "mine_wood"
    description = "砍树获取原木：找最近的树走过去砍倒，验证原木入背包"

    def run(self, ctx, args):
        state = ctx.ok("get_state")
        before = count_items(state, suffix="_log") + count_items(state, suffix="_wood")
        pos = player_pos(state)

        target = self._find_log(ctx, pos)
        if target is None:
            return "失败：半径 16 内没有找到树"
        self._switch_axe(ctx, state)
        if not self._goto(ctx, target):
            return "失败：未能走到树旁（寻路失败或超时）"

        # 砍树：先砍目标，再顺带紧邻原木（沿树干向上 / 相邻树冠）
        mined = 0
        while mined < MAX_MINE:
            ctx.checkpoint()
            log_target = self._next_log(ctx, target)
            if log_target is None:
                break
            if not self._mine_block(ctx, log_target):
                break
            mined += 1
            target = log_target  # 顺沿树干往上砍

        state2 = ctx.ok("get_state")
        after = count_items(state2, suffix="_log") + count_items(state2, suffix="_wood")
        if after <= before:
            return "失败：挖掘完成但背包原木未增加"
        return f"完成：砍倒 {mined} 块原木，背包原木 {before} → {after}"

    # ── 内部步骤 ────────────────────────────────────────────────
    def _find_log(self, ctx, pos):
        """半径内最近的原木方块（不低于脚下高度）。"""
        blocks = ctx.ok("get_blocks", {"radius": FIND_RADIUS, "max": 512})
        logs = [b for b in blocks.get("blocks", [])
                if is_log(b.get("id", "")) and b["y"] >= pos["y"] - 1]
        if not logs:
            return None
        return min(logs, key=lambda b: (b["x"] - pos["x"]) ** 2
                   + (b["z"] - pos["z"]) ** 2 + (b["y"] - pos["y"]) ** 2)

    def _switch_axe(self, ctx, state):
        """切斧头：快捷栏有则直接切；背包有则移到快捷栏 0；没有空手砍。"""
        slot = find_slot(state, "_axe")
        if slot is None:
            log.info("没有斧头，空手砍（慢一些）")
            return
        if slot < 9:
            ctx.ok("hotbar", {"slot": slot})
        else:
            ctx.ok("move_item", {"from_slot": slot, "to_slot": 0})
            ctx.ok("hotbar", {"slot": 0})
        log.info("已切换斧头（槽位 %d）", slot)

    def _goto(self, ctx, target) -> bool:
        """走到目标原木正下方地面；等 path_reached/path_failed。"""
        ground = {"x": target["x"], "y": target["y"] - 1, "z": target["z"]}
        ctx.ok("move_to", {**ground, "mode": "auto", "precision": 1.5})
        name, _ = ctx.wait_event(("path_reached", "path_failed"), timeout=120)
        return name == "path_reached"

    def _next_log(self, ctx, origin):
        """找下一个要砍的原木：优先同一列更高处，其次紧邻（水平 ≤2 格）最近者。"""
        blocks = ctx.ok("get_blocks", {"radius": 5, "max": 256})
        logs = [b for b in blocks.get("blocks", []) if is_log(b.get("id", ""))]
        if not logs:
            return None
        same_column = [b for b in logs
                       if b["x"] == origin["x"] and b["z"] == origin["z"] and b["y"] >= origin["y"]]
        if same_column:
            return min(same_column, key=lambda b: b["y"])
        return min(logs, key=lambda b: abs(b["x"] - origin["x"])
                   + abs(b["z"] - origin["z"]) + abs(b["y"] - origin["y"]))

    def _mine_block(self, ctx, target) -> bool:
        """对准目标方块持续挖掘，等 mine_done 后松手。"""
        ctx.ok("look_at", {"x": target["x"], "y": target["y"], "z": target["z"]})
        ctx.ok("attack", {"mode": "hold"})
        try:
            name, _ = ctx.wait_event(("mine_done",), timeout=90, interruptible=False)
            return name == "mine_done"
        finally:
            ctx.ok("attack", {"mode": "release"})


skill = MineWoodSkill()
