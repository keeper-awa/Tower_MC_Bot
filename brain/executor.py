#!/usr/bin/env python3
"""计划执行器：LLM 输出工作流（plan 工具）→ 确定性逐步执行（技能/工具）。

执行阶段 LLM 不在循环里。步骤按序执行，游戏事件由 wait_event 消费推进：
- 命中目标事件（path_reached/mine_done 等）→ 步骤继续
- damage/death、巡检危险（低血/岩浆）→ PlanInterrupt("urgent") → 紧急重排
- 玩家聊天 → 缓存 pending_chat；interruptible 等待时立即抛 PlanInterrupt("chat")，
  否则在步骤边界检查 pending_chat
"""

import json
import logging
import time

from skills._base import SkillContext

log = logging.getLogger("brain.executor")


class PlanInterrupt(Exception):
    """计划中断：kind ∈ urgent（damage/death/巡检危险）、chat（玩家新消息）。"""

    def __init__(self, kind: str, reason: str = ""):
        super().__init__(f"PlanInterrupt({kind}): {reason}")
        self.kind = kind
        self.reason = reason


class ConnectionLost(Exception):
    """与 Tower 的连接已失效（健康检查连续失败）——冒泡到主循环重建连接。"""


def _s(**props):
    return {"type": "object", "properties": props}


# LLM 排工作流的唯一工具（plan 步骤校验见 validate_plan）
PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "plan",
        "description": (
            "输出结构化任务计划：goal（一句话目标）+ steps（步骤序列，最多 8 个）+ accept（验收条件）。"
            "步骤 type=skill 引用下方技能清单里的技能（技能内部已处理细节，如砍树/合成/渡河）；"
            "type=tool 直接调用工具动作（move_to/get_state 等）。"
            "上下文附有【环境预扫描】坐标清单（树/水/岩浆/矿石/怪物/动物）和当前状态——"
            "直接引用坐标即可，不要调用任何查询工具（查询全部由大脑代码完成）。"
            "如砍树可给 mine_wood 传 x/y/z 指定砍哪棵；渡河可给 cross_water 传对岸坐标；"
            "移动用 move_to{x,y,z}。步骤不含坐标也能执行（技能会自己找目标），但给了更精确。"
            "大脑会严格按顺序执行全部步骤——执行阶段不再经过你，所以必须把「验证结果」写成显式步骤"
            "（如最后 get_state 检查背包）。某步骤失败时会暂停，请你重新规划（可跳过/换方案/说明无法完成）。"
        ),
        "parameters": _s(
            goal={"type": "string", "description": "一句话任务目标"},
            steps={"type": "array", "description": "步骤序列（严格按序执行）",
                   "items": {"type": "object",
                             "properties": {
                                 "type": {"type": "string", "enum": ["skill", "tool"]},
                                 "name": {"type": "string", "description": "技能名或工具动作名"},
                                 "args": {"type": "object", "description": "参数（可选）"},
                             },
                             "required": ["type", "name"], "additionalProperties": False}},
            accept={"type": "string", "description": "验收条件（如：背包 oak_log ≥ 3）"},
            on_fail={"type": "string", "enum": ["report", "stop"],
                     "description": "report=某步失败跳过继续（默认）；stop=失败即中止任务"},
        ),
        "required": ["goal", "steps", "accept"],
        "additionalProperties": False,
    },
}

# 复杂任务的总大纲工具（几十步长任务：LLM 只输出抽象步骤，逐级执行）
OUTLINE_TOOL = {
    "type": "function",
    "function": {
        "name": "outline",
        "description": (
            "输出复杂任务的总大纲：title（任务名）+ steps（抽象步骤列表，可几十步）。"
            "大脑会存入临时文件并逐级执行——每步会再次请你排具体执行计划，"
            "所以这里只写阶段级步骤（如「获得原木」「制作工作台和木镐」），不要写工具调用细节。"
        ),
        "parameters": _s(
            title={"type": "string", "description": "任务名（一句话）"},
            steps={"type": "array", "description": "阶段步骤（每步一句话，按序执行，最多 50 步）",
                   "items": {"type": "string"}},
        ),
        "required": ["title", "steps"],
        "additionalProperties": False,
    },
}

# 安全收尾归零序列（对应协议 §2.4 主动归零，brain 动作子集；单条失败不阻断）
SAFE_STOP_SEQ = [
    ("attack", {"mode": "release"}),   # 先放挖掘/攻击保持态
    ("move", {}),
    ("jump", {"value": False}),
    ("sneak", {"value": False}),
    ("look_at", {}),
    ("sprint", {"value": False}),
    ("swim", {"value": False}),
    ("move_to", {"cancel": True}),     # 最后取消导航
]


def validate_plan(raw, skill_names, tool_names, max_steps=8):
    """校验 LLM 的 plan 输出。返回 (plan|None, problems)。

    完全不可用（无 goal 或全部步骤非法）→ plan=None（调用方重试）；
    部分非法步骤 → 剔除并警告，其余照常执行。
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None, "plan 参数不是合法 JSON"
    if not isinstance(raw, dict):
        return None, "plan 参数不是对象"
    problems = []
    goal = str(raw.get("goal", "")).strip()
    if not goal:
        problems.append("goal 为空")
    steps = raw.get("steps")
    if not isinstance(steps, list) or not steps:
        return None, "steps 必须是非空数组"
    valid = []
    for s in steps[:max_steps]:
        if not isinstance(s, dict):
            problems.append("存在非法步骤（非对象）已剔除")
            continue
        stype, name = s.get("type"), s.get("name")
        if stype not in ("skill", "tool"):
            problems.append(f"步骤「{name}」的 type 非法（{stype}）已剔除")
            continue
        if stype == "skill" and name not in skill_names:
            problems.append(f"未知技能「{name}」已剔除")
            continue
        if stype == "tool" and name not in tool_names:
            problems.append(f"未知工具「{name}」已剔除")
            continue
        args = s.get("args")
        valid.append({"type": stype, "name": name, "args": args if isinstance(args, dict) else {}})
    if len(steps) > max_steps:
        problems.append(f"步骤超上限（{len(steps)}>{max_steps}）已截断")
    if not valid:
        return None, "; ".join(problems) or "所有步骤均非法"
    on_fail = raw.get("on_fail")
    return {
        "goal": goal or "(未命名目标)",
        "steps": valid,
        "accept": str(raw.get("accept", "")).strip(),
        "on_fail": on_fail if on_fail in ("report", "stop") else "report",
    }, problems


class PlanExecutor:
    """步骤状态机执行器：pending → running → done/failed/interrupted。"""

    def __init__(self, client, tools, skills, cfg, patrol_cb=None, llm=None):
        self.client = client
        self.tools = tools
        self.skills = skills
        self.llm = llm  # M5.3 视觉管线：传给技能上下文
        self.patrol_cb = patrol_cb          # 纯代码安全巡检回调，返回警告列表
        self.wait_timeout = cfg.get("default_wait_timeout", 60)
        self.check_interval = cfg.get("safety_check_interval", 5)
        self.patrol_interval = cfg.get("safety_patrol_interval", 30)
        self.plan = None
        self.step_i = 0
        self.results = []                   # [(step, result_text)]
        self.pending_chat = []              # 执行期间缓存的玩家消息
        self._ctx = SkillContext(client, tools, executor=self, skills=skills, llm=llm)
        self._last_check = 0.0
        self._last_patrol = 0.0
        self._conn_fails = 0

    # ── 状态与查询 ──────────────────────────────────────────────
    def reset(self):
        self.plan = None
        self.step_i = 0
        self.results = []
        self.pending_chat = []

    def status_text(self) -> str:
        """当前任务状态（注入 LLM 上下文/UI 面板；plan 未就绪时返回占位）。"""
        if self.plan is None:
            return "（空闲）"
        lines = [f"目标：{self.plan['goal']}",
                 f"进度：第 {self.step_i + 1}/{len(self.plan['steps'])} 步（on_fail={self.plan.get('on_fail')}）"]
        for idx, (step, result) in enumerate(self.results):
            lines.append(f"  {idx + 1}. {step['name']} → {result[:80]}")
        return "\n".join(lines)

    def summary(self) -> str:
        """任务执行记录（完成汇报用）。"""
        lines = [f"目标：{self.plan['goal']}（验收：{self.plan.get('accept') or '无'}）"]
        for idx, (step, result) in enumerate(self.results):
            lines.append(f"{idx + 1}. {step['name']}: {result}")
        return "\n".join(lines)

    def drain_pending_chat(self) -> list:
        chats, self.pending_chat = self.pending_chat, []
        return chats

    # ── 执行 ────────────────────────────────────────────────────
    def run(self, plan, resume=False):
        """按序执行全部步骤。返回: done / chat_interrupt / urgent:<原因> / failed_stop:<步骤>。

        resume=True：从上次中断位置继续（技能步骤幂等，可重跑）。
        """
        if not resume:
            self.plan = plan
            self.step_i = 0
            self.results = []
        for i in range(self.step_i, len(plan["steps"])):
            step = plan["steps"][i]
            self.step_i = i
            self.pending_chat.clear()
            try:
                result = self._execute_step(step)
            except PlanInterrupt as pi:
                if pi.kind == "chat":
                    return "chat_interrupt"          # step_i 未前进 → resume 重跑本步
                return f"urgent:{pi.reason}"
            self.results.append((step, result))
            if result.startswith("失败"):
                # 步骤失败：暂停计划，由大脑让 LLM 自适应重规划（不盲目跳过下一步）
                self.safe_stop()
                return f"step_failed:{step['name']}:{result[:150]}"
            # 步骤边界：等待期间（interruptible=False）缓存的玩家消息
            if self.pending_chat:
                self.step_i = i + 1                  # 本步已完成，resume 从下一步开始
                return "chat_interrupt"
        return "done"

    def _execute_step(self, step) -> str:
        if step["type"] == "skill":
            return self.skills.run(step["name"], self._ctx, step["args"])
        log.info("工具步骤: %s %s", step["name"], json.dumps(step["args"], ensure_ascii=False)[:120])
        return self.tools.execute(step["name"], step["args"])

    # ── 事件等待 ────────────────────────────────────────────────
    def wait_event(self, names, timeout=60, interruptible=True):
        """等待目标事件之一；期间消费并分派其他事件。返回 (name, data) 或 (None, None)。

        - damage/death → 抛 PlanInterrupt("urgent")
        - chat → 缓存 pending_chat；interruptible=True 时抛 PlanInterrupt("chat")
        - 周期健康/巡检检查 → 危险抛 PlanInterrupt("urgent")
        """
        deadline = time.time() + timeout
        while True:
            for e in self.client.drain_events():
                name = e.get("event")
                if name in names:
                    return name, e.get("data", {})
                if name in ("damage", "death"):
                    raise PlanInterrupt("urgent", f"收到 {name} 事件")
                if name == "chat":
                    if self.tools.is_echo(e.get("data", {})):
                        continue  # AI 自己刚发的消息回显（文本匹配），忽略
                    self.pending_chat.append(e.get("data", {}))
                    if interruptible:
                        raise PlanInterrupt("chat", "等待期间收到玩家消息")
            self._safety_tick()
            now = time.time()
            if now >= deadline:
                return None, None
            time.sleep(min(0.2, deadline - now))

    def _safety_tick(self):
        """周期检查：低血（每 check_interval）+ 全量巡检（每 patrol_interval）。

        水的警告不视为紧急（渡河技能内部已处理游泳）。"""
        now = time.time()
        if now - self._last_check < self.check_interval:
            return
        self._last_check = now
        try:
            state = self.client.ok("get_state")
            self._conn_fails = 0
        except Exception as e:
            # 连续失败判定连接失效（websockets keepalive 断线不会到主线程）
            self._conn_fails += 1
            log.debug("健康检查失败（第 %d 次）: %s", self._conn_fails, e)
            if self._conn_fails >= 2:
                raise ConnectionLost(f"健康检查连续失败：{e}")
            return
        health = state.get("player", {}).get("health", 20)
        if health < 6:
            raise PlanInterrupt("urgent", f"血量仅 {health:.0f}/20，必须停止任务立即撤退")
        if now - self._last_patrol >= self.patrol_interval and self.patrol_cb:
            self._last_patrol = now
            warnings = self.patrol_cb()
            urgent = [w for w in warnings if "岩浆" in w or "血量" in w]
            if urgent:
                raise PlanInterrupt("urgent", "; ".join(urgent))

    def safety_checkpoint(self):
        """技能步骤间安全检查点（供 SkillContext.checkpoint 调用）。"""
        self._safety_tick()

    # ── 安全收尾 ────────────────────────────────────────────────
    def safe_stop(self):
        """归零序列：释放持续状态 + 取消导航，单条失败不阻断。"""
        log.info("安全收尾：执行归零序列")
        for name, args in SAFE_STOP_SEQ:
            try:
                self.tools.execute(name, args)
            except Exception as e:
                log.debug("归零动作 %s 失败（忽略）: %s", name, e)
