#!/usr/bin/env python3
"""合成链技能：递归配方追溯 + 确定性生产流水线。

对齐 Issue #1「物品合成大类」+ 用户需求（原材料追溯）：
- LLM 只给目标物品 id（如 "minecraft:chest" / "minecraft:stone_pickaxe"）
- 内部完整递归：目标 → 材料 → 材料的材料 → … → 直到「源获取」（挖掘/采集）
- 递归到源后按拓扑序逆向执行：先采集/挖源材料 → 逐级合成 → 最终成品
- 类别等价：oak_log/spruce_log 都是原木，收集任意；木板同理

参数：{"target": "物品 id（如 minecraft:stone_pickaxe）", "count": 数量（缺省 1）}
"""

import logging
from collections import defaultdict

from . import kc
from ._util import count_items, find_slot, iter_items, player_pos, poll
from ._base import Skill

log = logging.getLogger("brain.skills")

# 源获取能力：无配方的材料如何获得（物品 → 技能/动作）
# 这些是「自然界直接获取」的物品，递归追溯到此为止
SOURCE_ACQUIRE = {
    # 木材：砍树（mine_wood 挖任意原木）
    "_log": "mine_wood",            # 任意原木
    "_wood": "mine_wood",           # 任意原木（去皮/木头块）
    # 石材：挖石头（mine_ore 待接入；先用 mine_wood 同款逻辑占位 → 由外部处理）
    "minecraft:cobblestone": "mine_ore",
    "minecraft:stone": "mine_ore",
    "minecraft:deepslate": "mine_ore",
    "minecraft:andesite": "mine_ore",
    "minecraft:diorite": "mine_ore",
    "minecraft:granite": "mine_ore",
    "minecraft:gravel": "mine_ore",
    "minecraft:sand": "mine_ore",
    "minecraft:red_sand": "mine_ore",
    # 矿物（直接挖）
    "minecraft:coal": "mine_ore",
    "minecraft:iron_ore": "mine_ore",
    "minecraft:gold_ore": "mine_ore",
    "minecraft:diamond": "mine_ore",
    "minecraft:redstone": "mine_ore",
    "minecraft:lapis_lazuli": "mine_ore",
    "minecraft:emerald": "mine_ore",
    # 冶炼产物（挖矿 + 熔炉；mine_ore/冶炼链待接入）
    "minecraft:iron_ingot": "mine_ore",
    "minecraft:gold_ingot": "mine_ore",
    "minecraft:copper_ingot": "mine_ore",
    "minecraft:netherite_ingot": "mine_ore",
    # 杂项自然物
    "minecraft:flint": "mine_ore",
    "minecraft:clay_ball": "mine_ore",
    "minecraft:string": "mine_ore",      # 杀蜘蛛得线
    "minecraft:feather": "mine_ore",     # 杀鸡得羽毛
    "minecraft:bone": "mine_ore",        # 杀骷髅得骨头
    "minecraft:leather": "mine_ore",     # 杀牛得皮革
    "minecraft:sugar_cane": "mine_ore",  # 采集甘蔗
    "minecraft:wheat": "mine_ore",       # 种/收小麦
    "minecraft:egg": "mine_ore",         # 鸡下蛋
    "minecraft:pumpkin": "mine_ore",     # 采集南瓜
    "minecraft:honeycomb": "mine_ore",   # 采蜜
    "minecraft:paper": "mine_ore",       # 甘蔗→纸（有配方，这里仅兜底）
}


class CraftChainSkill(Skill):
    name = "craft_chain"
    description = (
        "合成链：按目标物品递归追溯原材料并确定性合成（缺料自动收集/采集）。"
        "必传 target（Minecraft 物品 id，如 minecraft:stone_pickaxe / minecraft:chest / "
        "minecraft:furnace / minecraft:iron_sword）；count=数量(缺省1)。"
        "不可获取物品（命令方块/基岩/刷怪笼等）无法合成。"
    )

    def run(self, ctx, args):
        target = args.get("target")
        if not target:
            return "失败：缺少参数 target（要合成的物品 id，如 minecraft:oak_planks）"
        target = self._normalize(target)

        if kc.is_unobtainable(target):
            return f"失败：{target} 无法获取/合成（创造专属或不可获取物品），请换其他物品"
        if kc.recipe(target) is None and not self._is_source(target):
            return (f"失败：{target} 不在合成知识表中（不可合成或尚未收录）——"
                    f"可用物品举例：{self._available_preview()}")

        try:
            want = max(1, int(args.get("count", 1)))
        except (TypeError, ValueError):
            return "失败：count 参数必须是整数"

        state = ctx.ok("get_state")
        before = self._count_item(state, target)

        # ① 递归解析：生成生产计划（拓扑序：源 → 中间 → 成品）
        plan = self._resolve(ctx, target, want)
        if plan is None:
            return f"失败：{target} 的依赖链无法解析（材料缺失或不可获取）"

        # ② 执行生产计划（确定性流水线）
        if not self._execute_plan(ctx, plan):
            return f"失败：生产 {target} ×{want} 过程中某环节未完成"

        # ③ 验证
        after = self._count_item(ctx.ok("get_state"), target)
        if after < before + want:
            return f"失败：{target} 数量未达标（{before} → {after}）"
        return f"完成：合成 {target} ×{want}（背包 {before} → {after}）"

    # ── 递归解析 ────────────────────────────────────────────────
    def _resolve(self, ctx, target, want, _depth=0, _seen=None) -> list:
        """递归展开依赖树，返回生产计划（拓扑序步骤列表）。

        每步：{"kind": "acquire"/"craft", "item": 物品id, "count": 数量, "grid": "2x2"/"3x3"}
        拓扑序：先源材料后成品（后序 DFS 逆序）。
        循环（如煤↔煤块互转）→ 该物品降级为「源获取」，不再展开其配方。
        """
        if _depth > 20:
            log.warning("依赖深度超限（%s），中止", target)
            return None
        if _seen is None:
            _seen = set()
        if target in _seen:
            # 循环：如煤块↔煤互转——把该物品当源采集（不再递归其配方）
            log.info("检测到依赖循环（%s），按源获取处理", target)
            if not self._is_source(target):
                # 非自然源（如煤块）无法直接采集 → 报告失败
                return None
            return [{"kind": "acquire", "item": target, "count": want, "grid": "2x2"}]
        _seen = _seen | {target}

        steps = []

        # 已持有足够 → 不需要生产
        if self._count_item(ctx.ok("get_state"), target) >= want:
            return steps

        recipe = kc.recipe(target)
        # 源物品优先：即使是源且有「反向配方」（如羊毛可由线合成），也按采集处理——
        # 采集（剪羊毛/挖矿）远比合成划算且符合自然获取
        if self._is_source(target):
            steps.append({"kind": "acquire", "item": target, "count": want, "grid": "2x2"})
            return steps
        if recipe is None:
            # 无配方且非源：无法生产
            log.warning("%s 既无配方也非源，无法生产", target)
            return None

        # 有配方：先递归材料
        for mat, per_unit in recipe["materials"].items():
            mat_need = per_unit * want
            # 类别等价：材料若是「类别」（planks/wool/log），映射为具体物品再递归
            mat_item = self._category_to_item(ctx, mat)
            mat_steps = self._resolve(ctx, mat_item, mat_need, _depth + 1, _seen)
            if mat_steps is None:
                return None
            steps.extend(mat_steps)
        # 再合成目标
        steps.append({"kind": "craft", "item": target, "count": want,
                      "grid": recipe.get("grid", "3x3"), "yield": recipe.get("yield", 1)})
        return steps

    def _category_to_item(self, ctx, cat: str) -> str:
        """材料类别 → 具体物品 id：背包有该类物品用背包的；否则用默认代表。

        例：planks → oak_planks（或背包里已有的木板）；wool → white_wool。
        """
        if not cat.startswith("minecraft:"):
            # 类别：先看背包有没有匹配的
            info = kc.MATERIAL_CATEGORIES.get(cat)
            if info:
                suffix = info["suffix"]
                for entry in iter_items(ctx.ok("get_state")):
                    iid = entry.get("id", "")
                    if iid.endswith(suffix) and not iid.startswith("stripped_"):
                        return iid
                # 默认代表
                defaults = {"planks": "minecraft:oak_planks", "log": "minecraft:oak_log",
                            "wool": "minecraft:white_wool", "mushroom": "minecraft:red_mushroom"}
                return defaults.get(cat, f"minecraft:{cat}")
            return f"minecraft:{cat}"
        return cat

    def _is_source(self, item: str) -> bool:
        """物品是否为「源获取」（无配方，直接采集/挖掘）。"""
        if item in SOURCE_ACQUIRE:
            return True
        name = item[len("minecraft:"):] if item.startswith("minecraft:") else item
        # 原木/木头块/去皮变体 → 砍树
        if name.endswith("_log") or name.endswith("_wood") or name.startswith("stripped_"):
            return True
        # 羊毛 → 剪/杀羊（用线合成羊毛是反向配方，不采纳）
        if name.endswith("_wool") or name == "wool":
            return True
        return False

    # ── 生产计划执行 ────────────────────────────────────────────
    def _execute_plan(self, ctx, plan) -> bool:
        """按拓扑序执行生产计划：先采集源，再逐级合成。"""
        for step in plan:
            item, count = step["item"], step["count"]
            if step["kind"] == "acquire":
                if not self._acquire(ctx, item, count):
                    log.warning("采集 %s ×%d 失败", item, count)
                    return False
            else:
                if not self._craft_one(ctx, item, count, step.get("grid", "3x3")):
                    log.warning("合成 %s ×%d 失败", item, count)
                    return False
        return True

    def _acquire(self, ctx, item, count) -> bool:
        """源获取：原木→砍树；矿→挖矿（mine_ore 待接入）。"""
        name = item[len("minecraft:"):] if item.startswith("minecraft:") else item
        if name.endswith("_log") or name.endswith("_wood") or name.startswith("stripped_") \
                or item in SOURCE_ACQUIRE and SOURCE_ACQUIRE.get(item) == "mine_wood":
            # 原木：砍树（max_count 按需）
            r = ctx.run_skill("mine_wood", {"max_count": count})
            return r.startswith("完成")
        if item in SOURCE_ACQUIRE and SOURCE_ACQUIRE[item] == "mine_ore":
            # 挖矿（mine_ore 尚未实现）：报告待接入
            log.warning("需要 %s：mine_ore 技能尚未接入，暂无法自动采集", item)
            return False
        # 其他源：暂不支持
        log.warning("源获取 %s 暂不支持", item)
        return False

    def _craft_one(self, ctx, item, count, grid) -> bool:
        """合成单个物品（可被计划多次调用）。2x2 背包 / 3x3 工作台。"""
        goal = count
        if grid == "3x3":
            return self._craft_with_table(ctx, item, goal)
        # 2x2 背包直接
        for _ in range(min(count, 64)):
            if self._count_item(ctx.ok("get_state"), item) >= goal:
                return True
            try:
                ctx.ok("craft", {"recipe": item, "shift": count > 1})
            except Exception as e:
                log.info("2x2 合成失败（%s），转工作台", e)
                return self._craft_with_table(ctx, item, goal)
            if poll(ctx, lambda st: self._count_item(st, item) >= goal,
                    timeout=6.0, interval=0.6):
                return True
        return self._count_item(ctx.ok("get_state"), item) >= goal

    def _craft_with_table(self, ctx, item, goal) -> bool:
        """确保工作台摆到身旁，再 3x3 合成。"""
        state = ctx.ok("get_state")
        if count_items(state, exact="minecraft:crafting_table") == 0:
            r = ctx.run_skill("make_crafting_table")
            if not r.startswith("完成"):
                log.warning("工作台制作失败: %s", r)
        state = ctx.ok("get_state")
        slot = find_slot(state, "crafting_table")
        if slot is None:
            return False
        if slot < 9:
            ctx.ok("hotbar", {"slot": slot})
        else:
            ctx.ok("move_item", {"from_slot": slot, "to_slot": 0})
            ctx.ok("hotbar", {"slot": 0})
        ground = self._place_spot(ctx, player_pos(ctx.ok("get_state")))
        ctx.ok("interact_block", ground)
        for _ in range(min(goal, 64)):
            if self._count_item(ctx.ok("get_state"), item) >= goal:
                return True
            try:
                ctx.ok("craft", {"recipe": item, "shift": True})
            except Exception as e:
                log.warning("工作台合成失败: %s", e)
                return False
            if poll(ctx, lambda st: self._count_item(st, item) >= goal,
                    timeout=6.0, interval=0.6):
                return True
        return self._count_item(ctx.ok("get_state"), item) >= goal

    # ── 辅助 ────────────────────────────────────────────────────
    @staticmethod
    def _normalize(target: str) -> str:
        t = target.strip()
        return t if t.startswith("minecraft:") else f"minecraft:{t}"

    @staticmethod
    def _count_item(state, item) -> int:
        return count_items(state, exact=item)

    @staticmethod
    def _available_preview() -> str:
        preview = [
            "minecraft:oak_planks", "minecraft:stick", "minecraft:crafting_table",
            "minecraft:chest", "minecraft:furnace", "minecraft:torch",
            "minecraft:stone_pickaxe", "minecraft:iron_pickaxe", "minecraft:diamond_pickaxe",
            "minecraft:iron_sword", "minecraft:bed", "minecraft:bucket", "minecraft:bow",
        ]
        return "、".join(preview)

    def _place_spot(self, ctx, pos):
        """找身旁可摆放位置：返回要右键的地面方块坐标（其上方须是空位）。"""
        blocks = ctx.ok("get_blocks", {"radius": 3, "max": 128})
        solids = {(b["x"], b["y"], b["z"]) for b in blocks.get("blocks", [])}
        px, py, pz = pos["x"], pos["y"], pos["z"]
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            above = (px + dx, py, pz + dz)
            if above not in solids and (px + dx, py - 1, pz + dz) in solids:
                return {"x": px + dx, "y": py - 1, "z": pz + dz}
        if (px, py - 1, pz) in solids:
            return {"x": px, "y": py - 1, "z": pz}
        return {"x": px + 1, "y": py - 1, "z": pz}


skill = CraftChainSkill()
