#!/usr/bin/env python3
"""渡河/落水自救技能。

原 markdown 技能（skills/cross_water.md）步骤翻译为代码，两种用法：
1. 给定对岸坐标：move_to{allow_water:true} 自动驾驶渡河（等 path_reached）
2. 无坐标（落水自救）：找最近非水方向 → look_at 锁定对岸 → swim + move 前进，
   循环检查脚下直到上岸
"""

import logging
import time

from ._util import player_pos
from ._base import Skill

log = logging.getLogger("brain.skills")


class CrossWaterSkill(Skill):
    name = "cross_water"
    description = "渡河。x/y/z=对岸坐标；无参数=落水自救"

    def run(self, ctx, args):
        if args.get("x") is not None:
            return self._cross(ctx, args)
        return self._self_rescue(ctx)

    def _cross(self, ctx, args) -> str:
        ctx.ok("move_to", {
            "x": int(args["x"]), "y": int(args.get("y", 64)), "z": int(args["z"]),
            "allow_water": True,
        })
        name, _ = ctx.wait_event(("path_reached", "path_failed"), timeout=180)
        if name == "path_reached":
            return "完成：已渡河到达对岸"
        return "失败：渡河未到达（寻路失败或超时）"

    def _self_rescue(self, ctx) -> str:
        pos = player_pos(ctx.ok("get_state"))
        # 找最近的非水方向：扫描 8 格内四个方向，取陆地格最多的方向
        blocks = ctx.ok("get_blocks", {"radius": 8, "max": 512})
        water = {(b["x"], b["z"]) for b in blocks.get("blocks", [])
                 if "water" in b.get("id", "")}
        best_dir, best_score = None, -1
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            score = sum(1 for r in range(1, 7) if (pos["x"] + dx * r, pos["z"] + dz * r) not in water)
            if score > best_score:
                best_dir, best_score = (dx, dz), score
        if best_dir is None or best_score == 0:
            return "失败：四周都是水，找不到上岸方向"
        tx, tz = pos["x"] + best_dir[0] * 30, pos["z"] + best_dir[1] * 30
        ctx.ok("look_at", {"x": tx, "y": pos["y"] + 1, "z": tz})
        ctx.ok("swim", {"value": True})
        ctx.ok("move", {"forward": 1})
        try:
            for _ in range(90):  # 最多 90s
                ctx.checkpoint()
                time.sleep(1)
                bl = ctx.ok("get_blocks", {"radius": 3, "max": 128})
                under = bl.get("summary", {}).get("underfoot", {})
                if "water" not in under.get("id", ""):
                    ctx.ok("swim", {"value": False})
                    ctx.ok("move", {})
                    return "完成：已游上岸"
            return "失败：90 秒内未上岸"
        finally:
            ctx.ok("swim", {"value": False})
            ctx.ok("move", {})
            ctx.ok("look_at", {})  # 解除视角锁定


skill = CrossWaterSkill()
