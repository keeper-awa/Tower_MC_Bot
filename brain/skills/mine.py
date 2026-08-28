#!/usr/bin/env python3
"""挖掘大类技能（mine）：统一挖木头/矿石/方块，替代独立的 mine_wood/mine_ore。

设计（2026-08-28，对齐用户「挖掘归大类」方向）：
- 参数 what：wood=砍树 / ore=挖矿 / cobblestone / 具体方块 id（缺省 wood）
- 找目标：wood→原木聚类成树；ore/block→扫描目标方块（按距离选）
- 树识别放宽：优先「低处有原木」的完整树，没有则退而选最近的原木簇（残桩也挖）
- 工具切换：wood→斧；ore/stone→镐（按 kc.PICKAXE_CHAIN 判定等级）
- 挖掘循环：每挖 PICKUP_EVERY 块 → pickup_nearby 阶段拾取（掉落物不积压）
- 复用：_mine_block（中心瞄准+挖后验证+清挡路）/ _goto / _in_reach / _blocking_between

参数：{what, count, x/y/z}；count=最多挖几块（缺省按需），x/y/z 指定坐标优先。
"""

import logging
import math

from ._util import count_items, find_blocks, find_slot, is_log, player_pos, poll
from ._base import Skill
from . import kc

log = logging.getLogger("brain.skills")

MAX_MINE = 8           # 单次最多挖的方块数
FIND_RADIUS = 16       # 找目标最大半径
CLUSTER_DIST = 3       # 原木水平距离 ≤3 视为同一棵树
PICKUP_EVERY = 4       # 每挖几块阶段拾取一次


def _is_ore(iid: str) -> bool:
    """是否矿石类方块（含深板岩变体）。"""
    name = iid[len("minecraft:"):] if iid.startswith("minecraft:") else iid
    return name.endswith("_ore") or name in ("stone", "cobblestone", "deepslate", "andesite",
                                             "diorite", "granite", "tuff", "dripstone_block")


class MineSkill(Skill):
    name = "mine"
    description = (
        "挖掘大类：挖木头/矿石/方块。what=目标类型（wood=砍树 / ore=挖矿 / cobblestone / 具体方块id，缺省wood）；"
        "count=最多挖几块（缺省按需）；x/y/z=指定坐标。自动找目标、切合适工具（斧/镐）、每挖几块捡掉落。"
    )

    # ── 主流程 ────────────────────────────────────────────────
    def run(self, ctx, args):
        what = str(args.get("what", "wood")).strip().lower()
        kind = self._classify(what)
        state = ctx.ok("get_state")
        before = self._before_count(state, kind, what)
        try:
            want = max(1, int(args.get("count", 0)) or MAX_MINE)
        except (TypeError, ValueError):
            want = MAX_MINE

        self._switch_tool(ctx, state, kind)

        # ① 目标：指定坐标优先，否则找目标
        targets = None
        if args.get("x") is not None:
            tgt = {"x": int(args["x"]), "y": int(args.get("y", 64)), "z": int(args["z"])}
            targets = self._target_at(ctx, tgt, kind, what)
            if targets is None:
                log.info("指定坐标 (%d,%d,%d) 附近没有 %s，回退自找",
                         tgt["x"], tgt["y"], tgt["z"], what)

        # ② 循环挖：一棵树/一处挖完不够 → 继续找下一棵，直到挖满 want 或找不到
        mined_total = 0
        avoid = set()  # 挖不动（不可达）的目标坐标，避免死循环反复找同一棵
        while mined_total < want:
            if targets is None:
                targets = self._find_targets(ctx, kind, what, avoid)
                if targets is None:
                    targets = ctx.search_find(lambda: self._find_targets(ctx, kind, what, avoid))
                if targets is None:
                    log.info("找不到更多 %s，停止（已挖 %d/%d）", what, mined_total, want)
                    break
            if not self._reach_target(ctx, targets[0]):
                log.warning("目标不可达，换下一处")
                avoid.add((targets[0]["x"], targets[0]["z"]))
                targets = None
                continue
            mined = self._mine_blocks(ctx, targets, want - mined_total,
                                      self._pick_suffix(kind, what))
            mined_total += mined
            if mined == 0:
                # 这棵树一块没挖到（够不着/卡住）：排除，避免死循环
                avoid.add((targets[0]["x"], targets[0]["z"]))
            targets = None  # 挖完这一处，重新找下一棵

        # ③ 最后拾取 + 验证
        ctx.pickup_nearby(want_suffix=self._pick_suffix(kind, what))
        if not poll(ctx, lambda st: self._after_count(st, kind, what) > before, timeout=8.0):
            return f"失败：挖掘完成但背包 {what} 未增加"
        after = self._after_count(ctx.ok("get_state"), kind, what)
        if mined_total >= want:
            return f"完成：挖到 {mined_total} 块 {what}（背包 {before} → {after}）"
        return f"完成（未满）：只挖到 {mined_total}/{want} 块 {what}（背包 {before} → {after}，附近没有更多目标）"

    # ── 目标分类与计数 ────────────────────────────────────────
    @staticmethod
    def _classify(what: str) -> str:
        """what → kind：wood / ore / block。"""
        if what in ("wood", "log", "tree", "原木"):
            return "wood"
        if what in ("ore", "矿", "矿石"):
            return "ore"
        return "block"

    @staticmethod
    def _match_id(iid: str, kind: str, what: str) -> bool:
        """方块 id 是否匹配目标。"""
        if kind == "wood":
            return is_log(iid)
        if kind == "ore" and what in ("ore", "矿", "矿石"):
            return _is_ore(iid)
        # block：精确 id 匹配（what 可能是 minecraft:xxx 或裸名）
        target = what if what.startswith("minecraft:") else f"minecraft:{what}"
        return iid == target

    @staticmethod
    def _before_count(state, kind, what) -> int:
        """挖前背包目标物品数量。"""
        if kind == "wood":
            return count_items(state, suffix="_log") + count_items(state, suffix="_wood")
        return count_items(state, exact=what if what.startswith("minecraft:") else f"minecraft:{what}")

    @staticmethod
    def _after_count(state, kind, what) -> int:
        return MineSkill._before_count(state, kind, what)

    @staticmethod
    def _pick_suffix(kind, what) -> str:
        """拾取过滤后缀。wood→_log（含原木/去皮）；ore/block→物品名（id 简化）。"""
        if kind == "wood":
            return "_log"
        # 矿石掉落：铁/金/煤/钻石原矿或圆石——按 what 结尾匹配（iron_ore → iron_ore 掉落 iron_raw? 简化按 what）
        return what.split(":")[-1]

    # ── 目标查找 ──────────────────────────────────────────────
    def _target_at(self, ctx, target, kind, what):
        """指定坐标附近（半径 2）找目标：wood→聚类成树；block→精确匹配方块。"""
        blocks = ctx.ok("get_blocks", {"radius": 2, "max": 128})
        hits = [b for b in blocks.get("blocks", [])
                if self._match_id(b.get("id", ""), kind, what)
                and abs(b["x"] - target["x"]) <= 2 and abs(b["z"] - target["z"]) <= 2]
        if not hits:
            return None
        if kind == "wood":
            return self._cluster_logs(hits)[0]["logs"]
        # block/ore：取目标方块本身（可挖集合）
        return hits

    def _find_targets(self, ctx, kind, what, avoid=None):
        """渐进式扫描 + 聚类/最近：返回可挖目标方块列表（wood 返回整树原木）。

        avoid：{(x,z)} 已确认挖不动的坐标簇，跳过（防死循环）。
        用 find_blocks（mod filter 根治树林里 max=512 被树叶/草挤占）。
        """
        avoid = avoid or set()
        pos = player_pos(ctx.ok("get_state"))
        if kind == "wood":
            # filter="log" 只取原木（mod 新 jar）；旧 jar 忽略 filter 时 matcher=is_log 兜底过滤
            logs = find_blocks(ctx, filter_str="log", matcher=is_log, y_lo=-6, y_hi=8)
            if not logs:
                return None
            trees = self._cluster_logs(logs)
            # 放宽树识别：从「够得着」的簇（最低原木 ≤ 玩家 y+8）里选离玩家**最近**的
            reachable = [t for t in trees if t["min_y"] <= pos["y"] + 8]
            pool = reachable if reachable else trees
            pool = [t for t in pool if (t["x"], t["z"]) not in avoid]
            if not pool:
                return None
            pool.sort(key=lambda t: self._dist(t, pos))
            return pool[0]["logs"]
        # block/ore：按目标 id 过滤（filter=what 尾缀或精确 id）
        name = what.split(":")[-1]
        matcher = lambda b: self._match_id(b, kind, what)
        hits = find_blocks(ctx, filter_str=name, matcher=matcher, y_lo=-6, y_hi=8)
        if not hits:
            return None
        hits = [b for b in hits if (b["x"], b["z"]) not in avoid]
        if not hits:
            return None
        return hits

    @staticmethod
    def _cluster_logs(logs):
        """把原木按水平位置聚类成树：X/Z 差 ≤CLUSTER_DIST 视为同一棵。

        返回 [{x, z, min_y, logs: [...]}]，x/z 取该树第一个原木坐标（代表柱）。
        """
        trees = []
        for b in logs:
            placed = False
            for t in trees:
                if abs(b["x"] - t["x"]) <= CLUSTER_DIST and abs(b["z"] - t["z"]) <= CLUSTER_DIST:
                    t["logs"].append(b)
                    t["min_y"] = min(t["min_y"], b["y"])
                    placed = True
                    break
            if not placed:
                trees.append({"x": b["x"], "z": b["z"], "min_y": b["y"], "logs": [b]})
        return trees

    @staticmethod
    def _dist(tree, pos):
        """目标到玩家水平距离。"""
        return (tree["x"] - pos["x"]) ** 2 + (tree["z"] - pos["z"]) ** 2

    # ── 接近与工具 ────────────────────────────────────────────
    def _reach_target(self, ctx, target) -> bool:
        """走到目标旁可站立点，让目标进入挖掘范围。"""
        if self._in_reach(ctx, target):
            return True
        if not self._goto(ctx, target):
            return False
        return self._in_reach(ctx, target)

    def _switch_tool(self, ctx, state, kind):
        """按目标类型切工具：wood→斧；ore/block→镐。"""
        if kind == "wood":
            self._switch_slot(ctx, state, "_axe", "斧头")
        else:
            self._switch_slot(ctx, state, "_pickaxe", "镐子")

    def _switch_slot(self, ctx, state, suffix, label):
        """切工具：快捷栏有则直接切；背包有则移到快捷栏 0；没有空手。"""
        slot = find_slot(state, suffix)
        if slot is None:
            log.info("没有%s，空手挖（慢/可能不掉落）", label)
            return
        if slot < 9:
            ctx.ok("hotbar", {"slot": slot})
        else:
            ctx.ok("move_item", {"from_slot": slot, "to_slot": 0})
            ctx.ok("hotbar", {"slot": 0})
        log.info("已切换%s（槽位 %d）", label, slot)

    def _goto(self, ctx, target) -> bool:
        """走到目标附近的可站立点（够得着就行）。"""
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

        高度范围**围绕目标 y** 展开（而不是只玩家高度 ±2）——树根/矿在高处时
        也能站到其旁边的高度够得着。优先离目标近（保证可挖），其次离玩家近。
        """
        blocks = ctx.ok("get_blocks", {"radius": 8, "max": 512})
        solid = {(b["x"], b["y"], b["z"]) for b in blocks.get("blocks", [])}
        best, best_score = None, 1e9
        px, py, pz = int(player_pos["x"]), int(player_pos["y"]), int(player_pos["z"])
        tx, tz, ty = target["x"], target["z"], target["y"]
        y_lo = min(py - 2, ty - 2)
        y_hi = ty + 3
        for x in range(tx - 3, tx + 4):
            for z in range(tz - 3, tz + 4):
                for y in range(y_lo, y_hi + 1):
                    if (x, y, z) not in solid and (x, y + 1, z) not in solid and (x, y - 1, z) in solid:
                        d_target = abs(x - tx) + abs(z - tz) + abs(y - ty)
                        d_player = (x - px) ** 2 + (z - pz) ** 2 + (y - py) ** 2
                        score = d_target * 10 + d_player  # 优先离目标近（可挖），其次离玩家近
                        if score < best_score:
                            best_score, best = score, {"x": x, "y": y, "z": z}
        return best

    # ── 挖掘 ──────────────────────────────────────────────────
    def _mine_blocks(self, ctx, targets, max_mine, pick_suffix) -> int:
        """逐块挖掘 targets（低处优先），每挖 PICKUP_EVERY 块阶段拾取一次。"""
        mined = 0
        # 低处优先：按 y 升序；同 y 按水平近树心/目标
        ordered = sorted(targets, key=lambda b: (b["y"], abs(b["x"] - targets[0]["x"]) + abs(b["z"] - targets[0]["z"])))
        for target in ordered:
            if mined >= max_mine:
                break
            ctx.checkpoint()
            if not self._in_reach(ctx, target):
                log.warning("目标 %s 超出挖掘范围，尝试靠近",
                            (target["x"], target["y"], target["z"]))
                self._goto(ctx, target)
                if not self._in_reach(ctx, target):
                    continue
            if not self._mine_block(ctx, target):
                # 被挡未挖掉：_mine_block 已清挡路，重试一次；仍失败跳过（防死循环）
                log.info("目标 %s 被挡未挖掉，清理后重试一次",
                         (target["x"], target["y"], target["z"]))
                if not self._in_reach(ctx, target):
                    self._goto(ctx, target)
                self._mine_block(ctx, target)
                continue
            mined += 1
            # 阶段拾取：每 PICKUP_EVERY 块捡一次，避免掉落物积压/跑远（问题2）
            if mined % PICKUP_EVERY == 0:
                log.info("已挖 %d 块，阶段拾取", mined)
                ctx.pickup_nearby(want_suffix=pick_suffix)
        return mined

    @staticmethod
    def _in_reach(ctx, target) -> bool:
        """目标方块中心是否在挖掘范围内（水平 ≤4.0 且纵向 ≤4）。"""
        pos = ctx.ok("get_state")["player"]["position"]
        dx = target["x"] + 0.5 - pos["x"]
        dy = target["y"] + 0.5 - pos["y"]
        dz = target["z"] + 0.5 - pos["z"]
        return dx * dx + dz * dz <= 4.0 * 4.0 and abs(dy) <= 4.0

    def _blocking_between(self, ctx, target, blocks):
        """玩家眼睛 → 目标中心 的视线上，第一个挡路方块（非空气/非目标）。"""
        pos = player_pos(ctx.ok("get_state"))
        ex, ey, ez = pos["x"], pos["y"] + 1.62, pos["z"]
        dx = target["x"] + 0.5 - ex
        dy = target["y"] + 0.5 - ey
        dz = target["z"] + 0.5 - ez
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        if dist < 0.01:
            return None
        steps = max(1, int(dist * 4))
        by_key = {(b.get("x"), b.get("y"), b.get("z")): b
                  for b in blocks.get("blocks", [])}
        own_keys = {(pos["x"], pos["y"], pos["z"])}
        last_key = None
        for i in range(1, steps):
            t = i / steps
            x = ex + dx * t
            y = ey + dy * t
            z = ez + dz * t
            key = (int(math.floor(x)), int(math.floor(y)), int(math.floor(z)))
            if key == last_key:
                continue
            last_key = key
            if key == (target["x"], target["y"], target["z"]):
                continue
            if key in own_keys:
                continue
            b = by_key.get(key)
            if b is None:
                continue
            bid = b.get("id", "")
            if bid and bid != "minecraft:air" and "water" not in bid:
                return b
        return None

    def _mine_block(self, ctx, target) -> bool:
        """对准目标方块中心挖掘，等 mine_done 后验证；被挡则挖掉挡路方块。"""
        ctx.ok("look_at", {"x": target["x"] + 0.5, "y": target["y"] + 0.5, "z": target["z"] + 0.5})
        ctx.ok("attack", {"mode": "hold"})
        try:
            name, _ = ctx.wait_event(("mine_done",), timeout=90, interruptible=False)
            if name != "mine_done":
                return False
        finally:
            ctx.ok("attack", {"mode": "release"})
            ctx.ok("look_at", {})  # 解除视角锁定

        blocks = ctx.ok("get_blocks", {"radius": 3, "max": 128})
        still = next((b for b in blocks.get("blocks", [])
                      if b.get("x") == target["x"] and b.get("y") == target["y"] and b.get("z") == target["z"]),
                     None)
        if still is None:
            return True
        log.info("挖掘后目标 %s 仍是 %s（射线被挡），挖掉视线挡路方块",
                 (target["x"], target["y"], target["z"]), still.get("id"))
        blocker = self._blocking_between(ctx, target, blocks)
        if blocker is not None and self._in_reach(ctx, blocker):
            log.info("挡路方块: %s %s", blocker.get("id"),
                     (blocker["x"], blocker["y"], blocker["z"]))
            self._mine_block_simple(ctx, blocker)
        return False

    def _mine_block_simple(self, ctx, target) -> bool:
        """直接瞄准 target 中心挖掘一次（不验证），用于清理挡路方块。"""
        ctx.ok("look_at", {"x": target["x"] + 0.5, "y": target["y"] + 0.5, "z": target["z"] + 0.5})
        ctx.ok("attack", {"mode": "hold"})
        try:
            name, _ = ctx.wait_event(("mine_done",), timeout=30, interruptible=False)
            return name == "mine_done"
        finally:
            ctx.ok("attack", {"mode": "release"})
            ctx.ok("look_at", {})


skill = MineSkill()
