#!/usr/bin/env python3
"""砍树技能：找树 → 切斧头 → 走过去 → 整树砍倒 → 验证原木入背包。

策略（2026-08-28 重构，对齐用户设计）：
1. get_blocks 渐进式扫描（radius 4→6→8→10→16，避开恶地截断）
2. 扫描到的原木按水平位置聚类成「整棵树」（X/Z 差 ≤3 视为同一棵）
3. 优先选「低处有原木」的完整树（残桩/浮空树干会被跳过）
4. 切斧头 → 走到树旁可站立点 → 从低处向上逐块挖掘
5. 挖完拾取引导 + 验证原木入背包
6. 扫描不到 → search_find 主动搜索（多方向逐段探索）

参数：x/y/z 指定树（可选）；max_count=最多砍几块（缺省 8）
"""

import logging

from ._util import count_items, find_slot, is_log, player_pos, poll
from ._base import Skill

log = logging.getLogger("brain.skills")

MAX_MINE = 8          # 单次技能最多砍的方块数
FIND_RADIUS = 16      # 找树最大半径
TREE_CLUSTER_DIST = 3  # 原木水平距离 ≤3 视为同一棵树（树干 1x1 或 2x2）


class MineWoodSkill(Skill):
    name = "mine_wood"
    description = ("砍树。可选参数x/y/z指定树（扫描坐标直接用），"
                   "max_count=最多砍几块（缺省8）。自动聚合成整树砍倒")

    def run(self, ctx, args):
        state = ctx.ok("get_state")
        before = count_items(state, suffix="_log") + count_items(state, suffix="_wood")
        pos = player_pos(state)
        try:
            max_mine = min(int(args.get("max_count", MAX_MINE)), MAX_MINE)
        except (TypeError, ValueError):
            max_mine = MAX_MINE

        # ① 找树：优先指定坐标（聚类成树），否则渐进扫描 + 聚类选树
        tree = None
        if args.get("x") is not None:
            target = {"x": int(args["x"]), "y": int(args.get("y", 64)), "z": int(args["z"])}
            tree = self._tree_at(ctx, target)
            if tree is None:
                log.info("指定坐标 (%d,%d,%d) 附近没有原木，回退自找", target["x"], target["y"], target["z"])
        if tree is None:
            tree = self._find_tree(ctx)
        if tree is None:
            # ② 扫描不到：主动走远搜索（每段 60 格，最多 5 段）
            tree = ctx.search_find(lambda: self._find_tree(ctx))
        if tree is None:
            return "失败：附近与搜索范围内都没有找到树"

        # ③ 切斧头 + 走到树旁可站立点（够得着树即可）
        self._switch_axe(ctx, state)
        if not self._reach_tree(ctx, tree):
            return "失败：未能走到树的可挖掘范围内（寻路失败或超时）"

        # ④ 整树挖掘：从低处向上，直到 max_mine 块或树挖完
        mined = self._mine_tree(ctx, tree, max_mine)

        # ⑤ 拾取引导 + 验证
        ctx.pickup_nearby()
        def log_count(st):
            return count_items(st, suffix="_log") + count_items(st, suffix="_wood")

        if not poll(ctx, lambda st: log_count(st) > before, timeout=8.0):
            return "失败：挖掘完成但背包原木未增加"
        after = log_count(ctx.ok("get_state"))
        return f"完成：砍倒 {mined} 块原木，背包原木 {before} → {after}"

    # ── 树聚类与查找 ────────────────────────────────────────────
    def _tree_at(self, ctx, target):
        """指定坐标附近（半径 2）找一棵树：聚类该处原木。"""
        blocks = ctx.ok("get_blocks", {"radius": 2, "max": 128})
        logs = [b for b in blocks.get("blocks", [])
                if is_log(b.get("id", ""))
                and abs(b["x"] - target["x"]) <= 2 and abs(b["z"] - target["z"]) <= 2]
        if not logs:
            return None
        return self._cluster_logs(logs)[0]

    def _find_tree(self, ctx):
        """渐进式扫描 + 聚类：返回一棵完整树（低处有原木，优先离玩家近）。

        只认「低处有原木」（min_y ≤ 玩家 y+1）的完整树；若半径内全是残桩
        （浮空树干），返回 None → 触发上层 search_find 走远搜索。
        """
        pos = player_pos(ctx.ok("get_state"))
        for radius in (4, 6, 8, 10, FIND_RADIUS):
            blocks = ctx.ok("get_blocks", {"radius": radius, "max": 512})
            logs = [b for b in blocks.get("blocks", [])
                    if is_log(b.get("id", "")) and b["y"] >= pos["y"] - 1]
            if not logs:
                continue
            trees = self._cluster_logs(logs)
            complete = [t for t in trees if t["min_y"] <= pos["y"] + 1]
            if complete:
                # 选完整树：优先最低原木低，同低度选离玩家近
                complete.sort(key=lambda t: (t["min_y"], self._tree_dist(t, pos)))
                return complete[0]
            # 有原木但全是残桩：继续扩半径找（不返回，避免挖残桩）
            log.info("半径 %d 内原木都是残桩（min_y=%d），扩大搜索", radius, min(t["min_y"] for t in trees))
        return None

    @staticmethod
    def _cluster_logs(logs):
        """把原木按水平位置聚类成树：X/Z 差 ≤TREE_CLUSTER_DIST 视为同一棵。

        返回 [{x, z, min_y, logs: [...]}]，x/z 取该树第一个原木坐标（代表柱）。
        """
        trees = []
        for b in logs:
            placed = False
            for t in trees:
                if abs(b["x"] - t["x"]) <= TREE_CLUSTER_DIST and abs(b["z"] - t["z"]) <= TREE_CLUSTER_DIST:
                    t["logs"].append(b)
                    t["min_y"] = min(t["min_y"], b["y"])
                    placed = True
                    break
            if not placed:
                trees.append({"x": b["x"], "z": b["z"], "min_y": b["y"], "logs": [b]})
        return trees

    @staticmethod
    def _tree_dist(tree, pos):
        """树到玩家水平距离。"""
        return (tree["x"] - pos["x"]) ** 2 + (tree["z"] - pos["z"]) ** 2

    def _reach_tree(self, ctx, tree) -> bool:
        """走到树旁可站立点，让树里至少一块原木进入挖掘范围。"""
        # 树里最低的原木作为导航目标
        target = min(tree["logs"], key=lambda b: b["y"])
        if self._in_reach(ctx, target):
            return True
        if not self._goto(ctx, target):
            return False
        return self._in_reach(ctx, target)

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
        """走到目标附近的可站立点（够得着就行，不追求正下方/旁边）。

        从目标水平 3x3 范围内找可站立点（下方实心 + 站立格/上方空气），
        优先离玩家近的；找不到则扩大扫描。走到后由调用方复查 _in_reach。
        """
        pos = player_pos(ctx.ok("get_state"))
        spot = self._nearest_standable(ctx, target, pos)
        if spot is None:
            return False
        log.info("move_to 可站立点 %s", (spot["x"], spot["y"], spot["z"]))
        ctx.ok("move_to", {**spot, "mode": "auto", "precision": 1.5})
        name, data = ctx.wait_event(("path_reached", "path_failed"), timeout=120)
        if name != "path_reached":
            log.info("move_to 结果: %s %s", name, str(data)[:120])
            return False
        return True

    def _nearest_standable(self, ctx, target, player_pos):
        """在目标附近找可站立点（下方实心 + 站立格/上方空气）。

        以玩家脚下高度为基准（±2）扫描目标水平 5x5 范围；**优先离目标树近**的点
        （砍树要够得着树，站得离树近才保证 _in_reach 成立），同距才偏好玩家近。
        找不到返回 None。
        """
        blocks = ctx.ok("get_blocks", {"radius": 6, "max": 512})
        solid = {(b["x"], b["y"], b["z"]) for b in blocks.get("blocks", [])}
        best, best_d, best_p = None, 1e9, 1e9
        px, py, pz = int(player_pos["x"]), int(player_pos["y"]), int(player_pos["z"])
        tx, tz = target["x"], target["z"]
        for x in range(target["x"] - 2, target["x"] + 3):
            for z in range(target["z"] - 2, target["z"] + 3):
                for y in range(py - 2, py + 3):  # 玩家脚下高度 ±2
                    if (x, y, z) not in solid and (x, y + 1, z) not in solid and (x, y - 1, z) in solid:
                        # 主序：离树水平距离；次序：离玩家距离（同距取低点已由 y 循环保证）
                        d_tree = (x - tx) ** 2 + (z - tz) ** 2
                        d_play = (x - px) ** 2 + (z - pz) ** 2 + (y - py) ** 2
                        if d_tree < best_d or (d_tree == best_d and d_play < best_p):
                            best_d, best, best_p = d_tree, {"x": x, "y": y, "z": z}, d_play
        return best

    def _mine_tree(self, ctx, tree, max_mine) -> int:
        """整树挖掘：从低处原木开始向上逐块挖，最多 max_mine 块。

        每块挖前检查 _in_reach（够不着重定位一次）；树挖完或超上限即停。
        """
        mined = 0
        # 低处优先：按 y 升序；同 y 按水平近树心
        ordered = sorted(tree["logs"], key=lambda b: (b["y"], abs(b["x"] - tree["x"]) + abs(b["z"] - tree["z"])))
        for target in ordered:
            if mined >= max_mine:
                break
            ctx.checkpoint()
            if not self._in_reach(ctx, target):
                # 太远（如树冠高处）：重定位一次，仍不可达则跳过该块（继续挖下一块）
                log.warning("原木 %s 超出挖掘范围，尝试靠近", (target["x"], target["y"], target["z"]))
                self._goto(ctx, target)
                if not self._in_reach(ctx, target):
                    continue
            if not self._mine_block(ctx, target):
                break
            mined += 1
        return mined

    @staticmethod
    def _in_reach(ctx, target) -> bool:
        """目标方块中心是否在挖掘范围内（水平 ≤4.0 且纵向 ≤4；攻击判定范围约 4.5 格）。"""
        pos = ctx.ok("get_state")["player"]["position"]
        dx = target["x"] + 0.5 - pos["x"]
        dy = target["y"] + 0.5 - pos["y"]
        dz = target["z"] + 0.5 - pos["z"]
        return dx * dx + dz * dz <= 4.0 * 4.0 and abs(dy) <= 4.0

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
