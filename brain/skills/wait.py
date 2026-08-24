#!/usr/bin/env python3
"""等待技能：原地等待指定秒数（等待作物生长/怪物刷新/时间流逝等）。

等待期间仍消费游戏事件并执行安全巡检——危险（低血/岩浆）或玩家新消息
会照常中断（由执行器处理）。
"""

import time

from .base import Skill


class WaitSkill(Skill):
    name = "wait"
    description = "原地等待指定秒数（如等待作物生长、怪物刷新）；等待期间持续安全监测"
    # args: {"seconds": 5}

    def run(self, ctx, args):
        try:
            seconds = max(0.0, float(args.get("seconds", 5)))
        except (TypeError, ValueError):
            return "失败：seconds 参数必须是数字"
        deadline = time.time() + seconds
        # 用 wait_event 等一个不会出现的事件：期间事件被消费、危险可中断
        ctx.wait_event(("__none__",), timeout=seconds, interruptible=True)
        ctx.checkpoint()
        return f"完成：等待 {seconds:.0f}s"


skill = WaitSkill()
