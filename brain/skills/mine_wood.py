#!/usr/bin/env python3
"""砍树技能（mine_wood）：mine 挖掘大类的 wood 薄包装，保留注册名兼容。

- craft_chain / LLM 仍可用 mine_wood {count, x/y/z} 砍树
- 实际逻辑在 mine.py（MineSkill），统一挖掘大类（放宽树识别 + 阶段拾取）
"""

from ._base import Skill
from .mine import MineSkill


class MineWoodSkill(Skill):
    name = "mine_wood"
    description = ("砍树（mine 挖掘大类的 wood 用法）。可选参数x/y/z指定树（扫描坐标直接用），"
                   "count/max_count=最多砍几块（缺省8）。自动聚合成整树砍倒，每挖几块捡掉落")

    def run(self, ctx, args):
        # 把 max_count 归一为 count，what 固定 wood，转发给 mine 大类
        a = dict(args or {})
        if "max_count" in a and "count" not in a:
            a["count"] = a["max_count"]
        a["what"] = "wood"
        return MineSkill().run(ctx, a)


skill = MineWoodSkill()
