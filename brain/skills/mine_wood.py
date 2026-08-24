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

from ._util import count_items, find_slot, is_log, player_pos, poll
from ._base import Skill

log = logging.getLogger("brain.skills")

MAX_MINE = 8          # 单次技能最多砍的方块数
FIND_RADIUS = 16      # 找树半径


class MineWoodSkill(Skill):
    name = "mine_wood"
    description = "砍树。可选参数x/y/z指定树（扫描坐标直接用），缺省自找"

    def run(self, ctx, args):
        state = ctx.ok("get_state")
        before = count_items(state, suffix="_log") + count_items(state, suffix="_wood")
        pos = player_pos(state)

        # 可指定目标坐标（环境预扫描提供）：先确认该处仍是原木，否则回退自找
        target = None
        if args.get("x") is not None:
            target = {"x": int(args["x"]), "y": int(args.get("y", 64)), "z": int(args["z"])}
            if not self._is_log_at(ctx, target):
                log.info("指定坐标 (%d,%d,%d) 不是原木，回退自找", target["x"], target["y"], target["z"])
                target = None
        if target is None:
            target = self._find_log(ctx)
        if target is None:
            # 原地没有树：主动走远搜索（每段 60 格，最多 5 段）
            target = ctx.search_find(lambda: self._find_log(ctx))
        if target is None:
            return "失败：附近与搜索范围内都没有找到树"
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
            if not self._in_reach(ctx, log_target):
                # 太远（如浮空原木找不到支撑点）：重定位一次，仍不可达则放弃
                log.warning("原木 %s 超出挖掘范围，尝试靠近", (log_target["x"], log_target["y"], log_target["z"]))
                self._goto(ctx, log_target)
                if not self._in_reach(ctx, log_target):
                    break
            if not self._mine_block(ctx, log_target):
                break
            mined += 1
            target = log_target  # 顺沿树干往上砍

        # 拾取引导：掉落物可能散落/落水（浮空原木/沼泽场景必现），代码级逐个捡起
        ctx.pickup_nearby()
        # 掉落拾取有延迟：轮询等待原木入背包（最多 8s）
        def log_count(st):
            return count_items(st, suffix="_log") + count_items(st, suffix="_wood")

        if not poll(ctx, lambda st: log_count(st) > before, timeout=8.0):
            return "失败：挖掘完成但背包原木未增加"
        after = log_count(ctx.ok("get_state"))
        return f"完成：砍倒 {mined} 块原木，背包原木 {before} → {after}"

    # ── 内部步骤 ────────────────────────────────────────────────
    @staticmethod
    def _is_log_at(ctx, target) -> bool:
        """指定坐标处是否仍是原木方块（点查：半径 2 小窗口找匹配）。"""
        try:
            blocks = ctx.ok("get_blocks", {"radius": 2, "max": 128})
            return any(b["x"] == target["x"] and b["y"] == target["y"] and b["z"] == target["z"]
                       and is_log(b.get("id", "")) for b in blocks.get("blocks", []))
        except Exception:
            return False

    def _find_log(self, ctx):
        """半径内最近的原木方块（不低于脚下高度）。"""
        pos = player_pos(ctx.ok("get_state"))
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

    @staticmethod
    def _in_reach(ctx, target) -> bool:
        """目标方块中心是否在挖掘范围内（水平 ≤3.5 且纵向 ≤3；攻击判定范围约 4.5 格）。"""
        pos = ctx.ok("get_state")["player"]["position"]
        dx = target["x"] + 0.5 - pos["x"]
        dy = target["y"] + 0.5 - pos["y"]
        dz = target["z"] + 0.5 - pos["z"]
        return dx * dx + dz * dz <= 3.5 * 3.5 and abs(dy) <= 3.0

    def _mine_block(self, ctx, target) -> bool:
        """对准目标方块中心持续挖掘，等 mine_done 后松手。

        注意：look_at 瞄准的是世界坐标点，必须给方块中心（+0.5）——
        瞄准角落时射线会在角落周围摆动，挖掘目标频繁切换，进度永远涨不满。"""
        ctx.ok("look_at", {"x": target["x"] + 0.5, "y": target["y"] + 0.5, "z": target["z"] + 0.5})
        ctx.ok("attack", {"mode": "hold"})
        try:
            name, _ = ctx.wait_event(("mine_done",), timeout=90, interruptible=False)
            return name == "mine_done"
        finally:
            ctx.ok("attack", {"mode": "release"})


skill = MineWoodSkill()
