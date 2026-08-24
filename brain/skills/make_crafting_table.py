#!/usr/bin/env python3
"""制作工作台技能：背包已有则跳过；否则砍树 → 合成木板 → 合成工作台。

原 markdown 技能（skills/make_crafting_table.md）步骤翻译为代码：
1. get_state 查背包是否已有工作台 → 有则直接完成
2. 无 → 先 run_skill(mine_wood) 拿原木
3. 按原木类型合成木板（1 原木 → 4 木板，个人合成格 2x2 即可）
4. 合成工作台（4 木板 2x2）
5. get_state 验证工作台入背包
"""

import logging

from ._util import count_items, iter_items, poll
from ._base import Skill

log = logging.getLogger("brain.skills")


class MakeCraftingTableSkill(Skill):
    name = "make_crafting_table"
    description = "制作一个工作台（只做 1 个）：背包已有则跳过；否则砍树 → 合成木板 → 合成工作台"

    def run(self, ctx, args):
        state = ctx.ok("get_state")
        if count_items(state, exact="minecraft:crafting_table") > 0:
            return "完成：背包已有工作台，无需制作"

        r = ctx.run_skill("mine_wood")
        if not r.startswith("完成"):
            return f"失败：砍树环节未完成（{r}）"

        state = ctx.ok("get_state")
        log_id = self._first_log_id(state)
        if log_id is None:
            return "失败：砍完树但背包没有原木"
        planks_recipe = log_id.replace("_log", "_planks")  # minecraft:oak_log → minecraft:oak_planks
        ctx.ok("craft", {"recipe": planks_recipe, "shift": True})
        # 工作台只做一个（shift=False；shift=True 会把所有木板全合成工作台）
        ctx.ok("craft", {"recipe": "minecraft:crafting_table", "shift": False})

        # craft 响应乐观、产出延迟：轮询等增量（最多 10s）
        if poll(ctx, lambda st: count_items(st, exact="minecraft:crafting_table") > 0, timeout=10.0):
            return "完成：工作台已制作"
        return "失败：合成后背包没有工作台"

    @staticmethod
    def _first_log_id(state):
        for entry in iter_items(state):
            iid = entry.get("id", "")
            if iid.endswith("_log") and not iid.startswith("stripped_"):
                return iid
        return None


skill = MakeCraftingTableSkill()
