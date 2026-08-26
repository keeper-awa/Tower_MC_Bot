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
import random
import sys
import threading
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "tools" / "ai_client_example"))

from tower_client import TowerClient  # noqa: E402

from executor import OUTLINE_TOOL, PLAN_TOOL, PlanExecutor, validate_plan  # noqa: E402
from llm import LLM, LLMError  # noqa: E402
from memory import MemoryManager  # noqa: E402
from outline import OutlineManager  # noqa: E402
from skills import SkillManager  # noqa: E402
from skills._util import is_log  # noqa: E402
from tools import TOOL_DEFS, Toolset  # noqa: E402

log = logging.getLogger("brain")

TOOL_NAMES = [t["function"]["name"] for t in TOOL_DEFS]

# 人设模板（首次运行创建到 config 目录；与 brain/persona.yaml 同步维护）
PERSONA_TEMPLATE = """# 人设配置（M6）：修改后无需重启大脑，下次对话自动生效
name: "小塔"                      # AI 的名字（对话中自称）
personality: "活泼、热情、乐于帮忙，偶尔开玩笑"   # 性格描述（一句话）
quirks:                          # 口癖/说话习惯（可多条）
  - "每句话尽量简洁，说人话"
  - "称呼玩家为「你」"
rules:                           # 行为准则（可多条，会追加到系统提示）
  - "执行任务前先简单说明计划"
  - "任务完成后简短汇报结果"
forbidden:                       # 禁止事项（可多条）
  - "不做危险动作（跳岩浆、高空跳落、招惹怪物群）"
  - "不抱怨、不拒绝玩家的合理指令"
"""


def _app_dirs():
    """资源目录解析：exe 打包后 config 目录在 exe 旁；dev 模式在 brain/ 下。"""
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).parent
        cfg_dir = app_dir / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        return app_dir, cfg_dir
    brain_dir = Path(__file__).parent
    return brain_dir, brain_dir


def _default_config_path() -> Path:
    """缺省配置文件路径；exe 首次运行自动从打包内复制默认配置。"""
    if getattr(sys, "frozen", False):
        app_dir, cfg_dir = _app_dirs()
        cfg = cfg_dir / "config.yaml"
        if not cfg.exists():
            import shutil
            bundled = Path(getattr(sys, "_MEIPASS", str(app_dir))) / "config.yaml"
            try:
                shutil.copy(bundled, cfg)
                log.info("已生成默认配置: %s", cfg)
            except OSError as e:
                log.error("生成默认配置失败: %s", e)
        return cfg
    return Path(__file__).parent / "config.yaml"


def setup_logging():
    handlers = []
    if sys.stdout is not None:
        # Windows 控制台默认 GBK，强制 UTF-8 输出
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        handlers.append(logging.StreamHandler(sys.stdout))
    # exe 模式：日志落盘 config/brain.log（诊断用，报 bug 直接看这个文件）
    if getattr(sys, "frozen", False):
        try:
            log_dir = Path(sys.executable).parent / "config"
            log_dir.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_dir / "brain.log", encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            handlers.append(fh)
        except OSError:
            pass
    if not handlers:
        return  # 纯 UI 模式且日志文件不可写：日志由 UI 处理器接管
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
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

    def _plan_call(self, tool_args, tool_name="plan"):
        return {"text": "", "tool_calls": [{"id": f"dry-{self.step}", "name": tool_name,
                                            "arguments": tool_args}],
                "assistant_msg": {"role": "assistant", "content": None,
                                  "tool_calls": [{"id": f"dry-{self.step}", "type": "function",
                                                  "function": {"name": tool_name,
                                                               "arguments": json.dumps(tool_args, ensure_ascii=False)}}]},
                "usage": {}}

    def look(self, image_path, prompt=""):
        """dry-run 视觉：不真调 vision 模型，返回固定画面描述。"""
        return "（dry-run）画面描述：一片森林，几棵橡树，远处有座山。"

    def chat(self, messages, tools=None, vision=False):
        self.step += 1
        if self.scenario == "outline":
            # 大纲场景：outline 工具 → 逐级 plan → 执行 → 总汇报
            if self.step == 1:
                return self._plan_call({"title": "dry-run 大纲", "steps": ["步骤A", "步骤B"]},
                                       tool_name="outline")
            if self.step == 2:
                return self._plan_call({"goal": "dry-run 步骤A",
                                        "steps": [{"type": "tool", "name": "get_state"}],
                                        "accept": "dry-run"})
            if self.step == 3:
                return self._plan_call({"goal": "dry-run 步骤B",
                                        "steps": [{"type": "tool", "name": "update_memory",
                                                   "args": {"section": "lesson",
                                                            "content": "大纲逐级执行验证通过"}}],
                                        "accept": "dry-run"})
            return {"text": "（dry-run）大纲任务全部完成。", "tool_calls": [],
                    "assistant_msg": {"role": "assistant", "content": "（dry-run）大纲任务全部完成。",
                                      "tool_calls": None},
                    "usage": {}}
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
    def __init__(self, cfg_path: Path, dry_run: bool = False, scenario: str = "normal",
                 gui: bool = False):
        self.cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        self.dry_run = dry_run
        self.scenario = scenario
        # 启动前置校验：connection.game_dir 未配置/找不到 tower.json 时明确报错退出，
        # 避免主循环反复捕获 FileNotFoundError 刷"循环异常"（用户困惑且刷屏）
        game_dir = str((self.cfg.get("connection") or {}).get("game_dir", "") or "").strip()
        if not game_dir:
            raise RuntimeError(
                "config.yaml 未配置 connection.game_dir：请填写游戏目录"
                "（如 .minecraft/versions/1.20.1-Forge_47.4.23，用于读取 config/tower.json 的 token）"
            )
        token_path = Path(game_dir) / "config" / "tower.json"
        if not token_path.exists():
            raise RuntimeError(
                f"connection.game_dir 下找不到 {token_path}："
                "请确认游戏目录填写正确，且该版本已运行 Tower mod 生成 config/tower.json"
            )
        brain_cfg = self.cfg["brain"]
        self.plan_retries = brain_cfg.get("plan_retries", 1)
        self.plan_max_steps = brain_cfg.get("plan_max_steps", 8)
        self.safety_patrol_interval = brain_cfg.get("safety_patrol_interval", 30)
        self.app_dir, self.cfg_dir = _app_dirs()
        self.memory = MemoryManager(self.cfg_dir, brain_cfg.get("memory_max_bytes", 20000))
        self.skills = SkillManager(self._skills_dir())
        api_key = self._load_api_key() if not dry_run else None
        self.llm = FakeLLM(self.scenario) if dry_run else LLM(self.cfg["api"], api_key=api_key)
        self.client = None
        self.tools = None
        self.executor = None
        self.plan = None                # 当前活动计划（执行中或等待恢复）
        self.outline = None             # 活动任务大纲（OutlineManager，执行中）
        self._replan_count = 0          # 当前任务的失败重规划次数（有界防循环）
        self._resume_tried = False
        self.last_safety = 0.0
        self._last_ping = 0.0
        self._last_ui_state = 0.0
        self._dry_events_scheduled = False
        # GUI 状态字典（ui.py 轮询；非 GUI 模式为 None 不产生开销）
        self.ui = {} if gui else None
        # 费用统计（token 累计；金额估算待用户提供单价后接入）
        self.cost = {"calls": 0, "input_tokens": 0, "output_tokens": 0}

    # ── API key（唯一位置：config/api_key.json，exe 交付放 exe 旁 config/）──
    def _load_api_key(self) -> str:
        """读取 API key（唯一来源：cfg_dir/api_key.json，无其他兜底）。

        文件缺失时创建模板并提示填写；为空返回空串（llm.py 会报错指引）。
        """
        path = self.cfg_dir / "api_key.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"api_key": ""}, indent=2, ensure_ascii=False), encoding="utf-8")
                log.warning("已创建 API key 文件模板：%s（请在其中填入 DeepSeek API key）", path)
            except OSError as e:
                log.warning("创建 API key 文件失败: %s", e)
            return ""
        except Exception as e:
            log.warning("读取 %s 失败: %s（请检查文件格式）", path, e)
            return ""
        key = str(data.get("api_key", "")).strip()
        if key:
            log.info("API key 已从 %s 读取", path)
        else:
            log.warning("%s 中 api_key 为空（请填写后重启大脑）", path)
        return key

    # ── 人设（M6，热重载）─────────────────────────────────────────
    def _skills_dir(self) -> Path:
        """技能目录：exe 模式下优先 exe 旁 skills/（可自由扩展新技能），否则用打包内建。"""
        if getattr(sys, "frozen", False):
            ext = self.app_dir / "skills"
            if ext.exists():
                sys.path.insert(0, str(self.app_dir))  # 让 import skills 解析到外部目录
                log.info("使用外部技能目录: %s（可在此添加新技能）", ext)
                return ext
            return Path(getattr(sys, "_MEIPASS", str(self.app_dir))) / "skills"
        return Path(__file__).parent / "skills"

    def _load_persona(self) -> dict:
        """读取人设（config/persona.yaml）；缺失时创建模板。每次调用读取——热重载。"""
        path = self.cfg_dir / "persona.yaml"
        if not path.exists():
            try:
                path.write_text(PERSONA_TEMPLATE, encoding="utf-8")
                log.info("已创建人设模板: %s（改它即可换人设，下次对话生效）", path)
            except OSError as e:
                log.warning("创建人设模板失败: %s", e)
                return {}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return data if isinstance(data, dict) else {}
        except Exception as e:
            log.warning("persona.yaml 读取失败: %s", e)
            return {}

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
                log.warning("连接失败: %s，重试", e)
            # 重试间隔加随机抖动：避免与 Tower 前置 3s 重连相位锁定
            # （锁定时会反复"鉴权→断开→归零"，hello 永远看到 disconnected）
            time.sleep(3 + random.random() * 2)

    def _ensure_connected(self):
        if self.client is None:
            self.client = self._connect()
            self.tools = Toolset(self.client, self.cfg["brain"], self.memory)
            self.executor = PlanExecutor(self.client, self.tools, self.skills,
                                         self.cfg["brain"], patrol_cb=self._safety_patrol,
                                         llm=self.llm)
            if self.ui is not None:
                self.ui["status"] = {
                    "conn": "已连接",
                    "model": self.cfg["api"].get("model", "?"),
                    "llm": self._llm_stat_text(),
                }

    # ── 主循环 ────────────────────────────────────────────────────
    def run(self):
        log.info("大脑启动（%s）", "dry-run 模式" if self.dry_run else f"模型 {self.cfg['api']['model']}")
        while True:
            try:
                self._ensure_connected()
                if self.dry_run and not self._dry_events_scheduled:
                    self._dry_events_scheduled = True
                    self._schedule_dry_events()
                if not self._resume_tried:
                    self._resume_tried = True
                    self._maybe_resume_outline()
                self._tick()
            except Exception as e:
                log.error("循环异常: %s", e)
                self.client = None
                self.executor = None
                self.plan = None
                if self.ui is not None:
                    self.ui["status"] = {"conn": f"重连中（{type(e).__name__}）", "llm": self._llm_stat_text()}
                    self.ui["plan"] = None
            time.sleep(0.2)

    def _tick(self):
        # 心跳探测：主循环不碰 socket（只读事件缓存），连接死在后台线程无人知晓
        # —— 周期性 ping 探测，失败立即重建（websockets keepalive 报错不会到主线程）
        now = time.time()
        if now - self._last_ping >= 15:
            self._last_ping = now
            try:
                self.client.ping()
            except Exception as e:
                log.warning("心跳探测失败（%s），重建连接", e)
                self.client = None
                self.executor = None
                self.plan = None
                return
        # GUI 玩家状态：约 5s 推一次快照（大脑线程内调用，客户端线程安全）
        if self.ui is not None and now - self._last_ui_state >= 5:
            self._last_ui_state = now
            self._push_player_state()
        events = self.client.drain_events()
        if not events:
            self._maybe_safety_patrol()
            return
        urgent = [e for e in events if e.get("event") in ("damage", "death")]
        if urgent:
            # 有活动计划时根本走不到这里（执行中由 executor 的 wait_event 拦截）
            log.warning("收到 %s 事件（空闲状态，仅记录；低血由安全巡检兜底）", urgent[-1].get("event"))
            return
        chat = [e for e in events if e.get("event") == "chat" and not self.tools.is_echo(e.get("data", {}))]
        if chat:
            self._handle_chat(chat[-1])
            return
        log.debug("忽略事件: %s", [e.get("event") for e in events])

    # ── 聊天分流（LLM 唯一入口之一）───────────────────────────────
    def _handle_chat(self, event):
        text = event.get("data", {}).get("message", "")
        log.info("玩家: %s", text)
        resp = self._llm_once(f"玩家说：{text}", attach_plan_state=True,
                              tools=[PLAN_TOOL, OUTLINE_TOOL])
        if resp is None:
            return  # LLM 错误，跳过本轮
        if resp["tool_calls"]:
            call = resp["tool_calls"][0]
            if call["name"] == "outline":
                self._accept_outline(call["arguments"])
            elif call["name"] == "plan":
                self._accept_plan(call["arguments"], player_text=text)
            elif call["name"] in self.skills.names():
                # M5.3 兜底：LLM 偶尔直接把技能名当顶层工具调用（如 look）——
                # 不拦，直接执行该技能并汇报（复用 executor 已注入 llm 的上下文）
                log.info("LLM 直接调用技能 %s，自动执行", call["name"])
                try:
                    result = self.skills.run(call["name"], self.executor._ctx,
                                             call.get("arguments") or {})
                except Exception as e:
                    log.error("技能 %s 执行异常: %s", call["name"], e)
                    result = f"执行技能 {call['name']} 失败：{e}"
                self._say(result)
                return
            else:
                log.warning("LLM 调用了未知工具 %s，忽略", call["name"])
                return
        elif resp["text"].strip():
            self._say(resp["text"].strip())
            if self.plan:
                log.info("闲聊已回复，继续执行当前任务")
                self._run_plan(self.plan, resume=True)
            elif self.outline:
                log.info("闲聊已回复，继续执行大纲任务")
                self._run_outline()

    # ── 任务大纲（复杂任务：总大纲存文件，逐级执行）────────────────
    def _abort_outline(self):
        """新任务替换大纲（大纲文件标记 aborted 保留记录）。"""
        if self.outline is not None:
            log.info("新任务替换大纲任务「%s」，大纲中止", self.outline.title)
            self.outline.abort()
            self.outline = None

    def _accept_outline(self, raw):
        """接受总大纲：校验 → 存盘 → 播报 → 逐级执行。"""
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {}
        if not isinstance(raw, dict):
            raw = {}
        title = str(raw.get("title", "")).strip()
        steps = [s.strip() for s in raw.get("steps", []) if isinstance(s, str) and s.strip()]
        problems = []
        if not title:
            problems.append("title 为空")
        if len(steps) < 2:
            problems.append("步骤少于 2 步（简单的任务直接用 plan 就行）")
        if len(steps) > OutlineManager.MAX_STEPS:
            problems.append(f"步骤超过 {OutlineManager.MAX_STEPS} 步上限，已截断")
            steps = steps[:OutlineManager.MAX_STEPS]
        if problems:
            log.warning("大纲无效: %s", "; ".join(problems))
            self._say(f"这个任务我没法规划（{'；'.join(problems)}），换个说法试试？")
            return
        self._abort_outline()
        self._replan_count = 0
        self.outline = OutlineManager(self.cfg_dir)
        self.outline.start(title, steps)
        # 播报大纲：玩家先看到全貌，可纠正
        outline_text = " → ".join(f"{i + 1}.{s}" for i, s in enumerate(steps))
        self._say(f"收到！「{title}」计划：{outline_text}。开工！")
        self._run_outline()

    def _run_outline(self):
        """逐级执行大纲：每步交给 LLM 排具体计划 → 执行 → 记录 → 下一步。"""
        outline = self.outline
        if outline is None:
            return
        while outline.idx < len(outline.steps):
            if self.outline is not outline:
                return  # 被新任务替换/中止
            step_no = outline.idx + 1
            step_text = outline.steps[outline.idx]
            log.info("大纲进度: 第 %d/%d 步「%s」", step_no, len(outline.steps), step_text)
            if self.ui is not None:
                self.ui["plan"] = outline.status_text()
            done = outline.done_list()
            resp = self._llm_once(
                f"【任务大纲】{outline.title}\n已完成：{done or '（无）'}\n"
                f"当前步骤：{step_no}. {step_text}\n请为当前这一步排一个具体执行计划"
                f"（plan 工具，通常 1~3 步）",
                attach_plan_state=False)
            if resp is None:
                log.warning("大纲第 %d 步规划失败（LLM 错误），暂停大纲", step_no)
                break
            if not resp["tool_calls"] or resp["tool_calls"][0]["name"] != "plan":
                log.warning("大纲第 %d 步未产出计划（LLM 输出文本），记录后继续", step_no)
                outline.complete_step(outline.idx, resp["text"][:200] or "未产出计划")
                continue
            plan, problems = validate_plan(resp["tool_calls"][0]["arguments"],
                                           self.skills.names(), TOOL_NAMES, self.plan_max_steps)
            if plan is None:
                log.warning("大纲第 %d 步计划无效（%s），记录后继续", step_no, problems)
                outline.complete_step(outline.idx, f"计划无效：{problems}")
                continue
            # 大纲步骤执行不触发逐步汇报（最后统一总结，省一次调用）
            self._run_plan(plan, report=False)
            if self.outline is not outline:
                return  # 执行中被新任务替换
            result = self.executor.summary() if self.executor.results else "（无执行记录）"
            outline.complete_step(outline.idx, result)
        # 全部完成 → 总汇报
        if self.outline is outline and outline.idx >= len(outline.steps):
            outline.finish()
            self.outline = None
            log.info("大纲任务完成: %s", outline.title)
            if self.ui is not None:
                self.ui["plan"] = f"【大纲完成】{outline.title}"
            self._report_done_outline(outline)

    def _report_done_outline(self, outline):
        """大纲全部完成 → LLM 总结汇报（light 模式省 token）。"""
        summary = "；".join(
            f"{i + 1}.{outline.steps[i]}→{outline.results.get(str(i), '?')[:60]}"
            for i in range(len(outline.steps)) if str(i) in outline.results)
        resp = self._llm_once(
            f"任务「{outline.title}」全部完成。各阶段结果：\n{summary}\n请向玩家简短汇报最终成果",
            light=True)
        if resp and resp["text"].strip():
            self._say(resp["text"].strip())

    def _maybe_resume_outline(self):
        """启动时恢复未完成的大纲任务（断点续做）。"""
        m = OutlineManager.load(self.cfg_dir)
        if m is None or m.status != "running":
            return
        log.info("发现未完成的大纲任务「%s」（已完成 %d/%d 步），自动继续",
                 m.title, m.idx, len(m.steps))
        self.outline = m
        self._run_outline()

    def _accept_plan(self, raw, player_text: str = ""):
        """校验 plan；无效重试 plan_retries 次，仍无效告知玩家。

        player_text：玩家原始指令（重试时必须带上，否则 LLM 丢失任务上下文会自编任务）。
        """
        self._abort_outline()  # 新任务替换大纲（大纲文件标记 aborted）
        self._replan_count = 0
        plan, problems = validate_plan(raw, self.skills.names(), TOOL_NAMES, self.plan_max_steps)
        retries = self.plan_retries
        while plan is None and retries > 0:
            retries -= 1
            log.warning("计划无效（%s），重试 %d 次", problems, retries + 1)
            ctx = f"上一步给出的计划无效：{problems}。"
            if player_text:
                ctx += f"玩家刚才说的是：「{player_text}」，请针对这个任务重新规划。"
            ctx += "请直接输出 plan 工具调用（不要输出解释文本；步骤 type/name 必须来自技能与工具清单，goal 非空）"
            resp = self._llm_once(ctx)
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
    def _run_plan(self, plan, resume=False, report=True):
        if not resume:
            self.executor.safe_stop()       # 换计划先归零（防御性双保险）
            self.executor.reset()
        self.plan = plan
        log.info("开始执行计划: %s（%d 步）", plan["goal"], len(plan["steps"]))
        if self.ui is not None:
            self.ui["plan"] = self.executor.status_text()
        outcome = self.executor.run(plan, resume=resume)
        if outcome.startswith("urgent:"):
            reason = outcome[len("urgent:"):]
            log.warning("计划中断（紧急）: %s", reason)
            self.executor.safe_stop()       # 中断时游戏内可能残留持续状态 → 归零
            self.plan = None
            if self.ui is not None:
                self.ui["plan"] = None
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
            if self.ui is not None:
                self.ui["plan"] = None
            log.info("任务完成: %s", plan["goal"])
            if report:
                self._report_done(plan)
        elif outcome.startswith("step_failed:"):
            _, step, reason = outcome.split(":", 2)
            log.warning("步骤 %s 失败，暂停规划: %s", step, reason)
            self.plan = None
            self._handle_step_failure(plan, step, reason)
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

    def _handle_step_failure(self, plan, step, reason):
        """步骤失败：LLM 自适应重规划（有界 2 次），仍失败则如实告知玩家。"""
        if self._replan_count >= 2:
            self._say(f"任务「{plan['goal']}」的「{step}」反复失败（{reason[:100]}），我暂时没法完成。"
                      f"你可以换个方式再试试，或者让我做别的。")
            return
        self._replan_count += 1
        resp = self._llm_once(
            f"任务「{plan['goal']}」执行中，步骤「{step}」失败：{reason[:150]}。\n"
            f"请根据当前情况重新排计划：可跳过该步骤、换一种方案，或直接回复文本说明无法完成。")
        if resp is None:
            return
        if resp["tool_calls"]:
            call = resp["tool_calls"][0]
            if call["name"] == "plan":
                plan2, problems = validate_plan(call["arguments"], self.skills.names(),
                                                TOOL_NAMES, self.plan_max_steps)
                if plan2:
                    log.info("步骤失败后重规划: %s", plan2["goal"])
                    self._run_plan(plan2, report=False)  # 续跑不重复汇报，完成后统一判定
                    self._report_done(plan, history=f"（补救执行记录：\n{self.executor.summary()}\n）")
                    return
        if resp["text"].strip():
            self._say(resp["text"].strip())

    def _report_done(self, plan, history: str = "", depth: int = 0):
        """完成判定 + 汇报：对照验收条件，未达成且有方案则续做（有界）。

        depth=0 允许规划补救步骤；depth≥1 只如实判定汇报，避免无限循环。
        """
        record = f"执行记录：\n{self.executor.summary()}\n" if self.executor.results else ""
        record += history
        if depth >= 1:
            resp = self._llm_once(
                f"任务「{plan['goal']}」相关记录：\n{record}\n验收条件：{plan.get('accept') or '无'}。\n"
                f"请对照判断任务是否真正完成并简短汇报（完成与否都如实说明，未完成要说明原因）",
                light=True)
            if resp and resp["text"].strip():
                self._say(resp["text"].strip())
            return
        resp = self._llm_once(
            f"任务「{plan['goal']}」执行完毕。{record}\n验收条件：{plan.get('accept') or '无'}。\n"
            f"对照验收条件判断：已达成→直接汇报成果；未达成→用 plan 工具规划补救步骤",
            light=True)
        if resp and resp["tool_calls"]:
            call = resp["tool_calls"][0]
            if call["name"] == "plan":
                plan2, problems = validate_plan(call["arguments"], self.skills.names(),
                                                TOOL_NAMES, self.plan_max_steps)
                if plan2:
                    log.info("验收未通过，继续补救: %s", plan2["goal"])
                    self._run_plan(plan2, report=False)
                    self._report_done(plan, history=record + "\n", depth=1)
                    return
        if resp and resp["text"].strip():
            self._say(resp["text"].strip())

    # ── GUI 状态（ui.py 轮询）────────────────────────────────────
    def _llm_stat_text(self) -> str:
        return f"{self.cost['calls']} 次调用 / {self.cost['input_tokens']} in / {self.cost['output_tokens']} out token"

    def _push_player_state(self):
        try:
            state = self.client.ok("get_state")
            self.ui["player"] = {
                "position": state.get("player", {}).get("position", {}),
                "health": state.get("player", {}).get("health", "?"),
                "food": state.get("player", {}).get("food", "?"),
                "biome": state.get("world", {}).get("biome", "?"),
                "inventory": state.get("inventory", {}),
            }
        except Exception as e:
            log.debug("GUI 玩家状态刷新失败: %s", e)

    def submit_chat(self, text: str):
        """UI 会话窗口：把消息注入事件流（等价于游戏内聊天）。"""
        text = text.strip()
        if not text:
            return
        if self.client is None:
            log.warning("未连接 Tower，消息未发送：%s", text[:40])
            return
        self.client._events.append({"type": "event", "event": "chat",
                                    "data": {"message": text, "self": True}})
        log.info("玩家(UI): %s", text)

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

    # ── 环境预扫描（纯代码，查询不经 LLM）────────────────────────
    POI_LIMIT = 4  # 每类 POI 最多列出的坐标数（省 token）

    def _environment_scan(self) -> str:
        """扫描周围环境，压缩成坐标摘要注入 LLM 上下文。

        查询全部由代码完成，LLM 只拿到目标坐标：
        - 方块类（get_blocks 半径 16）：树/水/岩浆/矿石
        - 实体类（get_entities 半径 16）：怪物（hostile）/动物（creature、water）
        失败返回空串（不影响主流程）。
        """
        try:
            state = self.client.ok("get_state")
            px, pz = state["player"]["position"]["x"], state["player"]["position"]["z"]
            blocks = self.client.ok("get_blocks", {"radius": 16, "max": 512})
        except Exception as e:
            log.debug("环境预扫描（方块）失败: %s", e)
            return ""
        pois = {"树": [], "水": [], "岩浆": [], "矿石": []}
        for b in blocks.get("blocks", []):
            iid = b.get("id", "")
            if is_log(iid):
                key = "树"
            elif "water" in iid:
                key = "水"
            elif "lava" in iid:
                key = "岩浆"
            elif iid.endswith("_ore") and "debris" not in iid:
                key = "矿石"
            else:
                continue
            pois[key].append((b["x"], b["y"], b["z"]))
        for key in pois:
            pois[key].sort(key=lambda c: (c[0] - px) ** 2 + (c[2] - pz) ** 2)
            pois[key] = pois[key][:self.POI_LIMIT]
        parts = []
        for key in ("树", "水", "岩浆", "矿石"):
            arr = pois[key]
            parts.append(f"{key}: " + ("、".join(f"({x},{y},{z})" for x, y, z in arr) if arr else "无"))
        try:
            ents = self.client.ok("get_entities", {"radius": 16})
            mobs, animals = [], []
            for e in ents.get("entities", []):
                cat = e.get("category", "")
                if e.get("hostile"):
                    mobs.append((e.get("x"), e.get("y"), e.get("z"), e.get("name", "?")))
                elif cat in ("creature", "water") and e.get("id") != state.get("player", {}).get("id"):
                    animals.append((e.get("x"), e.get("y"), e.get("z"), e.get("name", "?")))
            for name, arr in (("怪物", mobs), ("动物", animals)):
                arr.sort(key=lambda c: (c[0] - px) ** 2 + (c[2] - pz) ** 2)
                arr = arr[:self.POI_LIMIT]
                parts.append(f"{name}: " + ("、".join(f"{nm}({x},{y},{z})" for x, y, z, nm in arr) if arr else "无"))
        except Exception as e:
            log.debug("环境预扫描（实体）失败: %s", e)
            parts += ["怪物: 无", "动物: 无"]
        return "【环境预扫描】" + "；".join(parts)

    # ── LLM 调用 ──────────────────────────────────────────────────
    def _llm_once(self, context: str, attach_plan_state: bool = False, light: bool = False,
                  tools: list = None):
        """一次 LLM 调用（LLM 错误的唯一调用点）；返回 resp dict 或 None。

        light=True：跳过状态/扫描（省 token），仅用于完成汇报等无需感知的调用。
        tools=None → 默认 [PLAN_TOOL]；聊天分流传 [PLAN_TOOL, OUTLINE_TOOL]。
        """
        messages = self._build_messages(context, attach_plan_state, light=light)
        try:
            resp = self.llm.chat(messages, tools=tools if tools is not None else [PLAN_TOOL])
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
        if self.ui is not None and self.ui.get("status"):
            self.ui["status"]["llm"] = self._llm_stat_text()
        return resp

    @staticmethod
    def _state_summary(state) -> str:
        """状态摘要（替代原始 JSON 快照，省 token）：位置/血量/饥饿/手持/群系/背包物品计数。"""
        p = state.get("player", {})
        pos = p.get("position", {})
        inv = state.get("inventory", {}) or {}
        counts = {}
        for entry in inv.get("slots", []) + inv.get("armor", []):
            iid = entry.get("id", "")
            if iid:
                counts[iid] = counts.get(iid, 0) + entry.get("count", 1)
        off = inv.get("offhand")
        if off and off.get("id"):
            counts[off["id"]] = counts.get(off["id"], 0) + off.get("count", 1)
        top = "、".join(f"{k.split(':')[-1]}×{v}" for k, v in
                        sorted(counts.items(), key=lambda kv: -kv[1])[:8])
        held = (inv.get("held") or {}).get("id", "")
        return (f"位置({pos.get('x', 0):.0f},{pos.get('y', 0):.0f},{pos.get('z', 0):.0f}) "
                f"血{p.get('health', '?')}/20 饿{p.get('food', '?')}/20 "
                f"手持{held.split(':')[-1] if held else '无'} "
                f"群系{(state.get('world', {}).get('biome', '?') or '?').split(':')[-1]} "
                f"背包[{len(counts)}种]: {top}")

    def _build_messages(self, context: str, attach_plan_state: bool, light: bool = False) -> list:
        """构建 LLM 消息。light=True（如完成汇报）跳过状态与扫描，只带任务上下文。"""
        persona = self._load_persona()
        name = str(persona.get("name") or self.cfg.get("persona", {}).get("name") or "未命名")
        system = (
            f"你是《我的世界》里的 AI 伙伴「{name}」，通过协议控制玩家的合法行为。\n"
        )
        persona_parts = []
        if persona.get("personality"):
            persona_parts.append(f"性格：{persona['personality']}")
        if persona.get("quirks"):
            persona_parts.append("口癖/说话习惯：" + "；".join(str(q) for q in persona["quirks"]))
        if persona.get("rules"):
            persona_parts.append("行为准则：" + "；".join(str(r) for r in persona["rules"]))
        if persona.get("forbidden"):
            persona_parts.append("禁止事项：" + "；".join(str(f) for f in persona["forbidden"]))
        if persona_parts:
            system += "\n【人设】\n" + "\n".join(persona_parts) + "\n"
        system += (
            "【职责】闲聊→回文本；简单任务→plan 工具（goal+steps≤8+accept）；"
            "繁琐任务（≥3 个阶段）→outline 工具输出总大纲（title+抽象步骤，可几十步，大脑逐级执行）。\n"
            "【要点】执行不经你：验证须写成显式步骤（如最后 get_state 检查背包）；"
            "type=skill 用技能 / type=tool 用动作；坐标直接引用环境预扫描清单，不要调用查询工具；"
            "排计划时不要输出解释文本，直接调用工具（文本会浪费输出预算导致参数被截断）；"
            "为后续步骤备料时只合成所需数量（craft_items 传 count），避免把材料全部用完；"
            "合成（木板/木棍/工具等）一律用 craft_items 技能（自动处理个人合成格/工作台），"
            "不要用原始 craft 工具步骤。\n"
            f"【技能】{self.skills.describe()}\n"
            "【动作】" + "、".join(TOOL_NAMES) + "\n"
            "【规则】血量低先撤；岩浆远离；饥饿进食；深水 move_to 带 allow_water:true；"
            "聊天禁 emoji（非法字符会被踢下线）。\n"
            f"{self.memory.inject()}\n"
        )
        user_parts = [f"触发：{context}"]
        if attach_plan_state and self.plan:
            user_parts.append(f"【任务进行中】\n{self.executor.status_text()}")
            user_parts.append("继续/修改任务→返回修改后的完整新计划（保留未完成步骤）；闲聊→回文本，任务继续。")
        if not light:
            try:
                state = self.client.ok("get_state")
                user_parts.append(f"当前状态：{self._state_summary(state)}")
            except Exception as e:
                user_parts.append("当前状态：获取失败")
                log.debug("get_state 失败: %s", e)
            scan = self._environment_scan()
            if scan:
                user_parts.append(scan)
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

        def inject(evt):
            self.client._events.append({"type": "event", "event": evt["event"], "data": evt["data"]})

        message = "（dry-run）帮我砍一棵树" if self.scenario == "emergency" else "（dry-run）验证闭环"
        # 与真实玩家消息同形态（self=true，单机玩家自述恒为 true）
        threading.Timer(1.0, inject, [{"event": "chat", "data": {"message": message, "self": True}}]).start()
        if self.scenario == "emergency":
            # 第 1 次调用的 plan 是 wait 10s → 4s 时注入 damage → 紧急中断 + 重排
            threading.Timer(4.0, inject, [{"event": "damage", "data": {}}]).start()


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Tower AI 大脑")
    parser.add_argument("--config", default=None, help="配置文件路径（缺省自动定位：dev=brain/config.yaml，exe=exe旁config/）")
    parser.add_argument("--dry-run", action="store_true", help="模拟 LLM 验证闭环（不调 API）")
    parser.add_argument("--scenario", default="normal",
                        choices=["normal", "emergency", "outline"],
                        help="dry-run 场景：normal=基本闭环；emergency=紧急中断+重排；outline=大纲逐级执行")
    parser.add_argument("--gui", action="store_true", help="启动图形界面")
    parser.add_argument("--no-gui", action="store_true", help="强制控制台模式（exe 打包后默认界面）")
    args = parser.parse_args()
    cfg_path = Path(args.config) if args.config else _default_config_path()
    if not cfg_path.exists():
        log.error("配置不存在: %s", cfg_path)
        return 1
    gui = args.gui or (getattr(sys, "frozen", False) and not args.no_gui)
    try:
        brain = Brain(cfg_path, dry_run=args.dry_run, scenario=args.scenario, gui=gui)
    except Exception as e:
        log.error("大脑启动失败: %s", e)
        if gui:
            import tkinter as _tk
            from tkinter import messagebox as _mb
            root = _tk.Tk()
            root.withdraw()
            _mb.showerror("Tower AI 启动失败", str(e))
            root.destroy()
        return 1
    if gui:
        import threading as _th
        from ui import run_ui
        _th.Thread(target=brain.run, daemon=True).start()
        run_ui(brain)
        return 0
    brain.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
