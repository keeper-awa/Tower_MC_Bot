#!/usr/bin/env python3
"""Tower AI 大脑主程序（M5.2：玩家指令驱动 + 技能代码化执行）。

LLM 只负责两件事：① 排工作流（plan 工具）② 用户聊天（闲聊/汇报）。
执行阶段由 PlanExecutor 代码化驱动技能/工具，LLM 不在循环里。
纯代码安全巡检（不调 LLM）发现危险 → 才让 LLM 排保命工作流。

主循环（0.2s 轮询）：
- 无活动计划：chat 事件 → 一次 LLM 调用（plan 工具 / 闲聊文本）
               damage/death → 仅日志（低血由巡检兜底）；无事件 → 30s 安全巡检
- 有活动计划：executor.run() 阻塞执行（技能等事件推进）
    ├─ 完成 → LLM 汇报一次（chat 工具发送）
    ├─ urgent 中断 → safe_stop → LLM 重排保命工作流
    └─ chat 中断（步骤边界）→ 回到聊天分流（闲聊继续/新指令替换）

用法：
    python brain.py                  # 正常启动（需 config.yaml 已填 API key）
    python brain.py --dry-run        # 不调 LLM：模拟固定脚本验证闭环（不花 API 钱）
    python brain.py --dry-run --scenario emergency   # dry-run 紧急中断场景
"""

import argparse
import json
import logging
import sys
import threading
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "tools" / "ai_client_example"))

from tower_client import TowerClient  # noqa: E402

from executor import PLAN_TOOL, PlanExecutor, validate_plan  # noqa: E402
from llm import LLM, LLMError  # noqa: E402
from memory import MemoryManager  # noqa: E402
from skills import SkillManager  # noqa: E402
from tools import TOOL_DEFS, Toolset  # noqa: E402

log = logging.getLogger("brain")

TOOL_NAMES = [t["function"]["name"] for t in TOOL_DEFS]


def setup_logging():
    # Windows 控制台默认 GBK，强制 UTF-8 输出
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


class FakeLLM:
    """dry-run：按调用顺序出固定脚本，验证闭环不花 API 钱。

    - normal：chat → plan(get_state + update_memory) → 执行 → 汇报文本
    - emergency：chat → plan(wait 10s) → 执行中注入 damage → 紧急中断 →
                 replan(逃跑) → 执行 → 汇报文本
    """

    def __init__(self, scenario="normal"):
        self.scenario = scenario
        self.step = 0

    def _plan_call(self, plan_args):
        return {"text": "", "tool_calls": [{"id": f"dry-{self.step}", "name": "plan",
                                            "arguments": plan_args}],
                "assistant_msg": {"role": "assistant", "content": None,
                                  "tool_calls": [{"id": f"dry-{self.step}", "type": "function",
                                                  "function": {"name": "plan",
                                                               "arguments": json.dumps(plan_args, ensure_ascii=False)}}]},
                "usage": {}}

    def chat(self, messages, tools=None, vision=False):
        self.step += 1
        if self.step == 1:
            if self.scenario == "emergency":
                # 排一个长等待步骤 → 期间注入 damage 验证紧急中断 + 重排
                return self._plan_call({"goal": "dry-run 砍树",
                                        "steps": [{"type": "skill", "name": "wait", "args": {"seconds": 10}}],
                                        "accept": "dry-run"})
            return self._plan_call({"goal": "dry-run 闭环",
                                    "steps": [{"type": "tool", "name": "get_state"},
                                              {"type": "tool", "name": "update_memory",
                                               "args": {"section": "lesson", "content": "dry-run 闭环验证通过"}}],
                                    "accept": "日志见执行记录"})
        if self.scenario == "emergency" and self.step == 2:
            # 紧急重排：逃跑计划
            return self._plan_call({"goal": "dry-run 逃生",
                                    "steps": [{"type": "tool", "name": "get_state"}],
                                    "accept": "dry-run"})
        return {"text": "（dry-run）闭环正常。", "tool_calls": [],
                "assistant_msg": {"role": "assistant", "content": "（dry-run）闭环正常。", "tool_calls": None},
                "usage": {}}


class Brain:
    def __init__(self, cfg_path: Path, dry_run: bool = False, scenario: str = "normal"):
        self.cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        self.dry_run = dry_run
        self.scenario = scenario
        brain_cfg = self.cfg["brain"]
        self.plan_retries = brain_cfg.get("plan_retries", 1)
        self.plan_max_steps = brain_cfg.get("plan_max_steps", 8)
        self.safety_patrol_interval = brain_cfg.get("safety_patrol_interval", 30)
        self.memory = MemoryManager(cfg_path.parent, brain_cfg.get("memory_max_bytes", 20000))
        self.skills = SkillManager(cfg_path.parent / "skills")
        self.llm = FakeLLM(self.scenario) if dry_run else LLM(self.cfg["api"])
        self.client = None
        self.tools = None
        self.executor = None
        self.plan = None                # 当前活动计划（执行中或等待恢复）
        self.last_safety = 0.0
        self._dry_events_scheduled = False
        # 费用统计（token 累计；金额估算待用户提供单价后接入）
        self.cost = {"calls": 0, "input_tokens": 0, "output_tokens": 0}

    # ── 连接（带重连）─────────────────────────────────────────────
    def _connect(self) -> TowerClient:
        conn = self.cfg["connection"]
        token_path = Path(conn["game_dir"]) / "config" / "tower.json"
        token = json.loads(token_path.read_text(encoding="utf-8"))["token"]
        port = conn.get("port", 24778)
        host = conn.get("host", "127.0.0.1")
        while True:
            try:
                c = TowerClient(token, host=host, port=port)
                if c.hello.get("prereq") == "connected":
                    log.info("已连接 Tower（protocol=%s prereq=%s）", c.hello.get("protocol"), c.hello.get("prereq"))
                    return c
                log.warning("前置链路未就绪（%s），重试", c.hello.get("prereq"))
                c.close()
            except Exception as e:
                log.warning("连接失败: %s，3s 后重试", e)
            time.sleep(3)

    def _ensure_connected(self):
        if self.client is None:
            self.client = self._connect()
            self.tools = Toolset(self.client, self.cfg["brain"], self.memory)
            self.executor = PlanExecutor(self.client, self.tools, self.skills,
                                         self.cfg["brain"], patrol_cb=self._safety_patrol)

    # ── 主循环 ────────────────────────────────────────────────────
    def run(self):
        log.info("大脑启动（%s）", "dry-run 模式" if self.dry_run else f"模型 {self.cfg['api']['model']}")
        while True:
            try:
                self._ensure_connected()
                if self.dry_run and not self._dry_events_scheduled:
                    self._dry_events_scheduled = True
                    self._schedule_dry_events()
                self._tick()
            except Exception as e:
                log.error("循环异常: %s", e)
                self.client = None
                self.executor = None
                self.plan = None
            time.sleep(0.2)

    def _tick(self):
        events = self.client.drain_events()
        if not events:
            self._maybe_safety_patrol()
            return
        urgent = [e for e in events if e.get("event") in ("damage", "death")]
        if urgent:
            # 有活动计划时根本走不到这里（执行中由 executor 的 wait_event 拦截）
            log.warning("收到 %s 事件（空闲状态，仅记录；低血由安全巡检兜底）", urgent[-1].get("event"))
            return
        chat = [e for e in events if e.get("event") == "chat"]
        if chat:
            self._handle_chat(chat[-1])
            return
        log.debug("忽略事件: %s", [e.get("event") for e in events])

    # ── 聊天分流（LLM 唯一入口之一）───────────────────────────────
    def _handle_chat(self, event):
        text = event.get("data", {}).get("message", "")
        log.info("玩家: %s", text)
        resp = self._llm_once(f"玩家说：{text}", attach_plan_state=True)
        if resp is None:
            return  # LLM 错误，跳过本轮
        if resp["tool_calls"]:
            call = resp["tool_calls"][0]
            if call["name"] != "plan":
                log.warning("LLM 调用了非 plan 工具 %s，忽略", call["name"])
                return
            self._accept_plan(call["arguments"])
        elif resp["text"].strip():
            self._say(resp["text"].strip())
            if self.plan:
                log.info("闲聊已回复，继续执行当前任务")
                self._run_plan(self.plan, resume=True)

    def _accept_plan(self, raw):
        """校验 plan；无效重试 plan_retries 次，仍无效告知玩家。"""
        plan, problems = validate_plan(raw, self.skills.names(), TOOL_NAMES, self.plan_max_steps)
        retries = self.plan_retries
        while plan is None and retries > 0:
            retries -= 1
            log.warning("计划无效（%s），重试 %d 次", problems, retries + 1)
            resp = self._llm_once(f"上一步给出的计划无效：{problems}。"
                                  f"请重新用 plan 工具输出可执行计划（步骤 type/name 必须来自技能与工具清单，goal 非空）")
            if not resp or not resp["tool_calls"]:
                break
            call = resp["tool_calls"][0]
            if call["name"] == "plan":
                plan, problems = validate_plan(call["arguments"], self.skills.names(), TOOL_NAMES, self.plan_max_steps)
        if plan is None:
            self._say(f"这个任务我没法规划（{problems}），换个说法试试？")
            return
        if problems:
            log.warning("计划部分步骤已剔除: %s", "; ".join(problems))
        self._run_plan(plan)

    # ── 计划执行 ──────────────────────────────────────────────────
    def _run_plan(self, plan, resume=False):
        if not resume:
            self.executor.safe_stop()       # 换计划先归零（防御性双保险）
            self.executor.reset()
        self.plan = plan
        log.info("开始执行计划: %s（%d 步）", plan["goal"], len(plan["steps"]))
        outcome = self.executor.run(plan, resume=resume)
        if outcome.startswith("urgent:"):
            reason = outcome[len("urgent:"):]
            log.warning("计划中断（紧急）: %s", reason)
            self.executor.safe_stop()       # 中断时游戏内可能残留持续状态 → 归零
            self.plan = None
            self._handle_replan(f"执行任务「{plan['goal']}」时遇到紧急情况：{reason}。"
                                f"请优先安排保命行动（撤退/远离危险/恢复），可用 plan 工具排计划，或直接回复文本提醒玩家")
        elif outcome == "chat_interrupt":
            self.executor.safe_stop()       # 等待中途被聊天打断 → 归零后再恢复/替换
            chats = self.executor.drain_pending_chat()
            if chats:
                if len(chats) > 1:
                    log.info("等待期间收到 %d 条玩家消息，处理最新一条", len(chats))
                self._handle_chat({"event": "chat", "data": chats[-1]})
            else:
                log.warning("聊天中断但无待处理消息，继续执行")
                self._run_plan(plan, resume=True)
        elif outcome == "done":
            self.plan = None
            log.info("任务完成: %s", plan["goal"])
            self._report_done(plan)
        elif outcome.startswith("failed_stop:"):
            step = outcome.split(":", 1)[1]
            log.warning("步骤 %s 失败且 on_fail=stop，任务中止", step)
            self.plan = None
            self._say(f"任务「{plan['goal']}」中止了：{step} 这一步做不了（已安全收尾）")
        else:
            self.plan = None
            log.error("未知执行结果: %s", outcome)

    def _handle_replan(self, context):
        """紧急/巡检危险 → LLM 排保命计划或提醒玩家。"""
        resp = self._llm_once(context)
        if resp is None:
            return
        if resp["tool_calls"]:
            call = resp["tool_calls"][0]
            if call["name"] == "plan":
                self._accept_plan(call["arguments"])
        elif resp["text"].strip():
            self._say(resp["text"].strip())

    def _report_done(self, plan):
        """任务完成 → LLM 生成一段汇报发给玩家。"""
        resp = self._llm_once(f"任务「{plan['goal']}」已执行完毕。执行记录：\n{self.executor.summary()}\n"
                              f"请向玩家简短汇报结果（做了什么/收获/注意事项），直接回复文本")
        if resp and resp["text"].strip():
            self._say(resp["text"].strip())
        elif resp and resp["tool_calls"]:
            log.info("完成汇报时 LLM 又输出 plan（忽略）")
        else:
            log.info("任务完成（无汇报文本）: %s", plan["goal"])

    # ── 安全巡检（纯代码，不调 LLM）───────────────────────────────
    def _maybe_safety_patrol(self):
        """空闲时周期巡检；发现危险 → LLM 排保命计划或提醒玩家。"""
        if time.time() - self.last_safety < self.safety_patrol_interval:
            return
        self.last_safety = time.time()
        warnings = self._safety_patrol()
        if warnings:
            log.warning("安全巡检发现危险: %s", "; ".join(warnings))
            self._handle_replan(f"安全巡检发现危险：{'；'.join(warnings)}。请安排保命行动或提醒玩家")

    def _safety_patrol(self) -> list:
        """纯代码巡检：低血/饥饿 + 半径 3 岩浆/水。返回警告列表。"""
        warnings = []
        try:
            state = self.client.ok("get_state")
        except Exception as e:
            log.debug("巡检 get_state 失败: %s", e)
            return []
        player = state.get("player", {})
        health = player.get("health", 20)
        food = player.get("food", 20)
        if health < 6:
            warnings.append(f"血量仅 {health:.0f}/20，必须优先撤退和恢复，禁止继续冒险")
        elif food < 10:
            warnings.append(f"饥饿（{food}/20），注意进食（use_item 吃食物）")
        try:
            blocks = self.client.ok("get_blocks", {"radius": 3, "max": 64})
            lava = [b for b in blocks.get("blocks", []) if "lava" in b.get("id", "")]
            if lava:
                nearest = min(lava, key=lambda b: abs(b["x"]) + abs(b["z"]))
                warnings.append(f"附近有岩浆（{nearest['id']} @ {nearest['x']},{nearest['y']},{nearest['z']}），立即远离！")
            water = [b for b in blocks.get("blocks", []) if "water" in b.get("id", "")]
            underfoot = blocks.get("summary", {}).get("underfoot", {})
            player_y = player.get("position", {}).get("y", 0)
            in_water = "water" in underfoot.get("id", "") or any(b["y"] >= player_y - 1 for b in water)
            if in_water:
                warnings.append("你正在水中！游泳上岸或 move_to 对岸（allow_water:true）")
        except Exception as e:
            log.debug("巡检环境检测失败: %s", e)
        return warnings

    # ── LLM 调用 ──────────────────────────────────────────────────
    def _llm_once(self, context: str, attach_plan_state: bool = False):
        """一次 LLM 调用（LLM 错误的唯一调用点）；返回 resp dict 或 None。"""
        messages = self._build_messages(context, attach_plan_state)
        try:
            resp = self.llm.chat(messages, tools=[PLAN_TOOL])
        except LLMError as e:
            log.error("LLM 调用失败，本轮跳过: %s", e)
            return None
        u = resp.get("usage", {})
        self.cost["calls"] += 1
        self.cost["input_tokens"] += u.get("input", 0)
        self.cost["output_tokens"] += u.get("output", 0)
        log.info("LLM 用量: +%d in / +%d out（累计 %d 次调用，%d in / %d out token）",
                 u.get("input", 0), u.get("output", 0), self.cost["calls"],
                 self.cost["input_tokens"], self.cost["output_tokens"])
        if resp["text"].strip():
            log.info("LLM: %s", resp["text"].replace("\n", " ")[:200])
        return resp

    def _build_messages(self, context: str, attach_plan_state: bool) -> list:
        persona = self.cfg.get("persona", {}).get("name", "未命名")
        system = (
            f"你是《我的世界》里的 AI 伙伴「{persona}」，通过协议控制玩家的合法行为。\n"
            "【工作方式】玩家给你发消息时：闲聊就直接回复文本；是任务就先理解意图、判断当前情况，"
            "然后用 plan 工具输出工作计划（goal + 步骤序列 + 验收条件）。\n"
            "【排计划要点】大脑会严格按步骤顺序执行——执行阶段不再经过你，所以：\n"
            "  · 把「获取信息」和「验证结果」都写成显式步骤（如先 get_blocks 找树，最后 get_state 检查背包）；\n"
            "  · 步骤最多 8 个，复杂任务拆成多个简单步骤；\n"
            "  · type=skill 引用下方技能清单（技能内部已处理细节），type=tool 直接调用工具动作；\n"
            "  · 需要等待（作物生长/怪物刷新）可用 wait 技能。\n"
            f"【可用技能】\n{self.skills.describe()}\n"
            "【工具动作】" + "、".join(TOOL_NAMES) + "\n"
            "【世界规则】1.20.1 原版生存。移动约定：长距离/定点移动一律用 move_to（给出目标坐标即可，"
            "寻路由游戏内完成）；跨深水 move_to 必须带 allow_water:true；交互方块用 interact_block 并给坐标。\n"
            "【行为准则】永远优先保证生存：血量低先撤退恢复；遇到岩浆立即远离；饥饿就进食。\n"
            f"{self.memory.inject()}\n"
        )
        user_parts = [f"触发：{context}"]
        if attach_plan_state and self.plan:
            user_parts.append(f"【当前任务进行中】\n{self.executor.status_text()}")
            user_parts.append("若玩家想继续/修改当前任务：返回修改后的完整新计划（会替换当前计划，"
                              "请保留尚未完成的步骤）；若只是闲聊：直接回复文本，任务会继续执行。")
        try:
            state = self.client.ok("get_state")
            state_snippet = json.dumps(state, ensure_ascii=False)
            if len(state_snippet) > 1500:
                state_snippet = state_snippet[:1500] + "...(截断)"
            user_parts.append(f"当前状态：{state_snippet}")
        except Exception as e:
            user_parts.append("当前状态：获取失败")
            log.debug("get_state 失败: %s", e)
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n".join(user_parts)},
        ]

    def _say(self, text: str):
        """在游戏聊天中说话。"""
        try:
            self.tools.execute("chat", {"message": text})
            log.info("AI 说: %s", text.replace("\n", " ")[:120])
        except Exception as e:
            log.warning("发送聊天失败: %s", e)

    # ── dry-run 事件注入 ──────────────────────────────────────────
    def _schedule_dry_events(self):
        """dry-run：注入伪造玩家消息触发全链路；emergency 场景再注入 damage 验证中断。"""

        def inject(event_name, data):
            self.client._events.append({"type": "event", "event": event_name, "data": data})

        message = "（dry-run）帮我砍一棵树" if self.scenario == "emergency" else "（dry-run）验证闭环"
        threading.Timer(1.0, inject, [{"event": "chat", "data": {"message": message}}]).start()
        if self.scenario == "emergency":
            # 第 1 次调用的 plan 是 wait 10s → 4s 时注入 damage → 紧急中断 + 重排
            threading.Timer(4.0, inject, [{"event": "damage", "data": {}}]).start()


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Tower AI 大脑")
    parser.add_argument("--config", default=str(Path(__file__).parent / "config.yaml"))
    parser.add_argument("--dry-run", action="store_true", help="模拟 LLM 验证闭环（不调 API）")
    parser.add_argument("--scenario", default="normal", choices=["normal", "emergency"],
                        help="dry-run 场景：normal=基本闭环；emergency=紧急中断+重排")
    args = parser.parse_args()
    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"配置不存在: {cfg_path}")
        return 1
    Brain(cfg_path, dry_run=args.dry_run, scenario=args.scenario).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
