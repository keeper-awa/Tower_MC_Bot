#!/usr/bin/env python3
"""技能基类：技能 = 确定性 Python 代码（执行阶段不经 LLM）。

- Skill.run(ctx, args) 返回汇报文本：成功以"完成"开头，失败以"失败"开头
- SkillContext：client 访问 / 等事件 / 安全检查点 / 组合调用其他技能 / 拾取引导
  （由 PlanExecutor 构建注入，技能内部不直接依赖 executor 实现）
"""

import logging
import time as _time

from ._util import player_pos

log = logging.getLogger("brain.skills")


class Skill:
    name = ""            # 注册名（LLM plan 步骤引用）
    description = ""     # 注入 LLM 的能力说明

    def run(self, ctx, args: dict) -> str:
        raise NotImplementedError


class SkillContext:
    """技能运行上下文（由 PlanExecutor 构建并注入）。"""

    def __init__(self, client, tools, executor=None, skills=None, llm=None):
        self.client = client
        self.tools = tools
        self.executor = executor
        self.skills = skills
        self.llm = llm  # M5.3 视觉管线：look 技能用 vision 模型看图

    def ok(self, action, params=None):
        """调用 Tower 动作，失败抛异常（同 TowerClient.ok）。"""
        return self.client.ok(action, params)

    def tools_execute(self, name, args=None):
        """执行工具动作（含 2000 字符截断），返回文本。"""
        return self.tools.execute(name, args or {})

    def wait_event(self, names, timeout=60, interruptible=True):
        """等待事件之一（超时返回 (None, None)；紧急/聊天中断抛 PlanInterrupt）。"""
        return self.executor.wait_event(names, timeout, interruptible)

    def checkpoint(self):
        """安全检查点：低血/岩浆危险抛 PlanInterrupt("urgent")。"""
        self.executor.safety_checkpoint()

    def run_skill(self, name, args=None):
        """组合调用其他技能（如做工作台内部复用砍树）。"""
        return self.skills.run(name, self, args or {})

    def search_find(self, find_fn, legs=5, leg_dist=60, timeout=150):
        """主动寻找材料：原地找不到时按方向逐段探索（每段 move_to + 重新检测）。

        find_fn() → 找到返回目标（真值），没找到返回 None。
        中途玩家消息/危险照常中断（由执行器处理，恢复后从新位置继续找）。
        """
        result = find_fn()
        if result:
            return result
        log.info("原地未找到目标，开始主动搜索（最多 %d 段，每段 %d 格）", legs, leg_dist)
        pos = player_pos(self.ok("get_state"))
        px, pz = pos["x"], pos["z"]
        directions = [(1, 0), (0, 1), (-1, 0), (0, -1), (1, 1), (-1, 1), (1, -1), (-1, -1)]
        for leg in range(legs):
            dx, dz = directions[leg % len(directions)]
            tx, tz = px + dx * leg_dist, pz + dz * leg_dist
            log.info("搜索第 %d/%d 段: move_to (%d, ?, %d)", leg + 1, legs, tx, tz)
            try:
                self.ok("move_to", {"x": tx, "y": pos["y"], "z": tz, "mode": "auto",
                                    "precision": 4.0, "allow_water": True})
            except Exception as e:
                log.debug("搜索移动失败（%s），换下一方向", e)
                continue
            name, _ = self.wait_event(("path_reached", "path_failed"), timeout=timeout, interruptible=True)
            if name != "path_reached":
                log.debug("搜索第 %d 段寻路失败，换下一方向", leg + 1)
                continue
            pos = player_pos(self.ok("get_state"))
            px, pz = pos["x"], pos["z"]
            result = find_fn()
            if result:
                log.info("搜索第 %d/%d 段找到目标", leg + 1, legs)
                return result
            _time.sleep(0.5)
        return None

    def pickup_nearby(self, timeout=40.0) -> str:
        """拾取引导：把附近掉落物逐个捡起（纯代码，不经 LLM）。

        挖掘后的掉落物可能散落/落水、物品会漂移——每轮重查 get_entities 找
        item 实体（category=item），move_to 靠近最近的一个直到进入拾取范围，
        循环直至捡完（连续 2 轮无新物品）或超时。中途玩家消息/危险照常中断。
        """
        import time as _time
        deadline = _time.time() + timeout
        empty_rounds = 0
        approached = 0
        while _time.time() < deadline:
            try:
                ents = self.ok("get_entities", {"radius": 12, "max": 64})
            except Exception as e:
                log.debug("拾取引导扫描失败: %s", e)
                break
            items = [e for e in ents.get("entities", []) if e.get("category") == "item"]
            if not items:
                empty_rounds += 1
                if empty_rounds >= 2:
                    break
                _time.sleep(1.0)
                continue
            empty_rounds = 0
            pos = self.ok("get_state")["player"]["position"]
            items.sort(key=lambda e: (e["x"] - pos["x"]) ** 2 + (e["z"] - pos["z"]) ** 2)
            it = items[0]
            # 已在拾取范围（约 1 格，MC 需玩家碰撞物品才拾取）：主动贴近而非干等——
            # 距离² < 1.0 才可能被吸；1~4 之间 move_to 精确贴上去（干等 2 格边缘不会拾取）
            d2 = (it["x"] - pos["x"]) ** 2 + (it["z"] - pos["z"]) ** 2 + (it["y"] - pos["y"]) ** 2
            if d2 < 1.0:
                _time.sleep(0.3)
                continue
            if it["y"] < pos["y"] - 1.5:
                # 物品在下方深处（多为落水物品）：游泳下潜靠近（move_to 走水不可靠）
                try:
                    self._swim_to_item(it)
                    approached += 1
                    continue
                except Exception as e:
                    log.debug("游泳拾取失败，改走常规移动: %s", e)
                    try:
                        self.ok("swim", {"value": False})
                        self.ok("move", {})
                    except Exception:
                        pass
            # 靠近物品：先 move_to 到物品旁的站立点（±1 格内），再 look_at + 直走贴上去
            # —— move_to 到物品整数格可能停在方块边缘（precision 1.0 允 1 格误差），
            #    达不到拾取碰撞距离；直走微调确保真正贴近物品。
            tx, ty, tz = int(it["x"]), int(it["y"]), int(it["z"])
            try:
                self.ok("move_to", {"x": tx, "y": ty, "z": tz, "mode": "auto",
                                    "precision": 1.0, "allow_water": True})
                self.wait_event(("path_reached", "path_failed"), timeout=30, interruptible=True)
            except Exception as e:
                log.debug("move_to 物品失败: %s", e)
            # 直走贴上去（最多 3s）：锁定物品，持续前进直到进入拾取距离
            self.ok("look_at", {"x": it["x"], "y": it["y"] + 0.3, "z": it["z"]})
            self.ok("move", {"forward": 1})
            try:
                for _ in range(6):
                    self.checkpoint()
                    _time.sleep(0.5)
                    p = self.ok("get_state")["player"]["position"]
                    if (it["x"] - p["x"]) ** 2 + (it["z"] - p["z"]) ** 2 + (it["y"] - p["y"]) ** 2 < 1.0:
                        break
                    # 物品已被拾取/消失
                    ents = self.ok("get_entities", {"radius": 8, "max": 64})
                    if not any(e.get("id") == it.get("id") for e in ents.get("entities", [])):
                        break
            finally:
                try:
                    self.ok("move", {})
                    self.ok("look_at", {})
                except Exception:
                    pass
            approached += 1
        return f"拾取引导结束（靠近 {approached} 个目标点）"

    def _swim_to_item(self, item) -> None:
        """深水拾取：锁定物品方向游泳靠近，直到进入拾取范围或物品消失。"""
        import time as _time
        self.ok("look_at", {"x": item["x"], "y": item["y"] + 0.3, "z": item["z"]})
        self.ok("swim", {"value": True})
        self.ok("move", {"forward": 1})
        try:
            for _ in range(24):  # 最多 ~12s
                self.checkpoint()
                _time.sleep(0.5)
                p = self.ok("get_state")["player"]["position"]
                if (item["x"] - p["x"]) ** 2 + (item["z"] - p["z"]) ** 2 + (item["y"] - p["y"]) ** 2 < 4.0:
                    return
                ents = self.ok("get_entities", {"radius": 8, "max": 64})
                if not any(e.get("id") == item.get("id") for e in ents.get("entities", [])):
                    return  # 已被拾取或消失
        finally:
            self.ok("swim", {"value": False})
            self.ok("move", {})
