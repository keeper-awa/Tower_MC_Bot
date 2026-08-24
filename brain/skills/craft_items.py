#!/usr/bin/env python3
"""合成指定物品技能：自动检查材料；2x2 配方个人合成格直接做；3x3 配方需要工作台（没有就先做一个）。

原 markdown 技能（skills/craft_items.md）步骤翻译为代码：
1. 先尝试个人合成格直接 craft（2x2 配方一次成功）
2. 失败（3x3 配方无工作台）→ 背包无工作台先 run_skill(make_crafting_table)
3. 切工作台到快捷栏 → interact_block 放到身旁地面 → 再 craft
4. get_state 验证成品入背包

参数：{"recipe": "minecraft:xxx", "item": "成品物品 id（验证用）"}
"""

import logging

from ._util import count_items, find_slot, player_pos, poll
from ._base import Skill

log = logging.getLogger("brain.skills")


class CraftItemsSkill(Skill):
    name = "craft_items"
    description = ("合成。必传recipe(配方id,如minecraft:oak_planks)与item(成品id)；"
                   "count=数量(缺省尽量多合成，如要 1 个工作台就传 count:1)；3x3配方自动摆工作台")

    def run(self, ctx, args):
        recipe = args.get("recipe")
        item = args.get("item")
        if not recipe or not item:
            return "失败：缺少参数（需要 recipe 与 item，如 {recipe: minecraft:oak_planks, item: minecraft:oak_planks}）"
        try:
            want = max(1, int(args.get("count", 0))) if args.get("count") is not None else None
        except (TypeError, ValueError):
            return "失败：count 参数必须是整数"

        # 基线：合成前该物品数量（验证必须对比增量——已持有部分时 count>0 会平凡成立）
        state = ctx.ok("get_state")
        before = count_items(state, exact=item)

        if want is None:
            # 未指定数量：尽量多合成（shift=True）
            try:
                ctx.ok("craft", {"recipe": recipe, "shift": True})
                log.info("个人合成格直接合成成功")
            except Exception as e:
                log.info("个人合成格失败（可能是 3x3 配方）: %s", e)
                if not self._craft_with_table(ctx, recipe, shift=True):
                    return "失败：合成失败（含工作台方案）"
        else:
            # 指定数量：逐次合成直到达标（shift=False 一次做一个；上限防失控）
            current = before
            for _ in range(min(want, 64)):
                if current >= before + want:
                    break
                try:
                    ctx.ok("craft", {"recipe": recipe, "shift": False})
                except Exception as e:
                    log.info("个人合成格失败（可能是 3x3 配方）: %s", e)
                    if not self._craft_with_table(ctx, recipe, shift=False):
                        break
                # 等本次产出落地再点下一次（craft 响应乐观——点击已发但产出延迟数百 ms，
                # 立即重查会读到旧数量导致重复点击、材料双倍消耗）
                if poll(ctx, lambda st: count_items(st, exact=item) > current, timeout=6.0, interval=0.6):
                    current = count_items(ctx.ok("get_state"), exact=item)
                else:
                    break

        # craft 响应是乐观的（sent=已点击），实际产出延迟数秒：轮询等增量（最多 10s）
        target = before + (want or 1)
        if poll(ctx, lambda st: count_items(st, exact=item) >= target, timeout=10.0):
            return "完成：合成成功"
        return f"失败：合成后 {item} 数量未增加"

    # ── 工作台方案 ──────────────────────────────────────────────
    def _craft_with_table(self, ctx, recipe, shift=True) -> bool:
        """确保有工作台并摆放到身旁地面，再合成。"""
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
        ground = self._place_spot(ctx, player_pos(state))
        ctx.ok("interact_block", ground)  # 手持工作台右键地面方块 → 摆放到其上方
        try:
            ctx.ok("craft", {"recipe": recipe, "shift": shift})
            return True
        except Exception as e:
            log.warning("摆放工作台后合成仍失败: %s", e)
            return False

    def _place_spot(self, ctx, pos):
        """找身旁可摆放位置：返回要右键的地面方块坐标（其上方须是空位）。"""
        blocks = ctx.ok("get_blocks", {"radius": 3, "max": 128})
        solids = {(b["x"], b["y"], b["z"]) for b in blocks.get("blocks", [])}
        px, py, pz = pos["x"], pos["y"], pos["z"]
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            above = (px + dx, py, pz + dz)
            if above not in solids and (px + dx, py - 1, pz + dz) in solids:
                return {"x": px + dx, "y": py - 1, "z": pz + dz}
        if (px, py - 1, pz) in solids:  # 退路：摆脚下（自身可能站上去）
            return {"x": px, "y": py - 1, "z": pz}
        return {"x": px + 1, "y": py - 1, "z": pz}


skill = CraftItemsSkill()
