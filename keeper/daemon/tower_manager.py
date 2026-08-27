"""Tower 版 Manager：持 Tower Brain，向 FastAPI/webui 提供 Keeper 兼容接口。

替代 Keeper 的 KeyboardClient + AgentLoop：Brain 连 Tower mod（24778），
LLM 排 plan/outline + 技能代码化执行。前端语义（Phase 3）逐步 Tower 化；
Phase 1 先让 daemon + webui + Brain 同进程跑通。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── 定位 Tower 项目根（keeper/ 在 h:\Tower_MC_Bot\ 下）────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))  # 让 `from brain.brain import Brain` 可解析
# 注意：不要把 brain/ 目录本身加进 sys.path——会干扰 `brain` 命名空间包解析

BRAIN_CONFIG = PROJECT_ROOT / "brain" / "config.yaml"
BRAIN_KEY_FILE = PROJECT_ROOT / "brain" / "api_key.json"


class _ClientProxy:
    """对 Tower Brain 连接的只读视图（满足 app.py 的 client.connected / get_state）。"""

    def __init__(self, mgr: "TowerManager") -> None:
        self._mgr = mgr

    @property
    def connected(self) -> bool:
        b = self._mgr._brain
        if b is None or b.ui is None:
            return False
        return str((b.ui.get("status") or {}).get("conn", "")).startswith("已连接")

    async def get_state(self) -> dict[str, Any]:
        return await self._mgr.state()


class _BrainLogHandler(logging.Handler):
    """把大脑日志转发到 TowerManager（Phase 2：前端日志面板显示大脑活动）。"""

    def __init__(self, cb) -> None:
        super().__init__()
        self._cb = cb

    def emit(self, record):
        try:
            self._cb(self.format(record))
        except Exception:  # noqa: BLE001
            pass


class TowerManager:
    """管理 Tower Brain 的生命周期，向 daemon REST/webui 提供数据与控制。"""

    def __init__(self) -> None:
        self._started = time.time()
        self._brain = None
        self._brain_error: str | None = None  # Brain 初始化失败（配置缺失）原因
        self._brain_thread: threading.Thread | None = None
        self._subs: set[asyncio.Queue] = set()
        self._command_log: deque[dict[str, Any]] = deque(maxlen=2000)
        self._status_patch: dict[str, Any] = {}  # 前端主动设置的状态覆盖（Phase 3 用）
        # 订阅大脑日志 → 前端日志面板（Phase 2 数据桥接）
        _handler = _BrainLogHandler(self._ingest_brain_log)
        _handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
        logging.getLogger("brain").addHandler(_handler)

    # ── 大脑日志 → command_log ────────────────────────────────
    def _ingest_brain_log(self, line: str) -> None:
        origin = "ai" if "AI 说" in line else "system"
        kind = "error" if "[ERROR]" in line else ("warning" if "[WARNING]" in line else "info")
        self._command_log.append({"ts": time.time(), "origin": origin, "kind": kind, "text": line})

    # ── Brain 生命周期 ─────────────────────────────────────────
    @property
    def brain(self):
        if self._brain is None:
            self._ensure_brain()
        return self._brain

    def _ensure_brain(self) -> None:
        """按需创建 Brain 并起后台线程（Brain.run 阻塞循环）。

        配置缺失（game_dir 未填/tower.json 不存在）时**不抛异常**：
        记录 `_brain_error`，status() 返回 config_error 供前端友好提示（clone 后首次使用）。
        """
        if self._brain is not None:
            return
        if self._brain_error:
            return  # 配置缺失：不反复重试创建
        if not BRAIN_CONFIG.exists():
            self._brain_error = f"缺少大脑配置: {BRAIN_CONFIG}"
            logger.error(self._brain_error)
            return
        from brain.brain import Brain  # noqa: PLC0415

        try:
            self._brain = Brain(BRAIN_CONFIG, dry_run=False, scenario="normal", gui=True)
        except RuntimeError as exc:
            # game_dir 未配置 / tower.json 不存在等 → 前端引导，而非 500
            self._brain_error = str(exc)
            logger.error("Brain 初始化失败（配置问题）: %s", exc)
            return
        self._brain_thread = threading.Thread(target=self._brain.run, daemon=True)
        self._brain_thread.start()
        # 启动后应用 settings.json 里的对话/视觉模型（若有），覆盖 config.yaml 默认
        self._apply_saved_models()
        logger.info("Tower Brain 已启动（连 Tower mod 24778）")

    def _apply_saved_models(self) -> None:
        """把 settings.json 已配置的对话/视觉模型热应用到 brain.llm（启动时）。"""
        from .settings import load_settings

        s = load_settings()
        for mid in (s.active_model_id, s.vision_model_id):
            if not mid:
                continue
            m = next((x for x in s.models if x.id == mid), None)
            if m is None:
                continue
            if mid == s.active_model_id:
                self._apply_llm(m)
            if mid == s.vision_model_id:
                self._apply_vision(m)

    def close(self) -> None:
        """停止 Brain（主循环退出由进程结束兜底）。"""
        if self._brain is not None and self._brain.client is not None:
            try:
                self._brain.client.close()
            except Exception:  # noqa: BLE001
                pass

    # ── client 只读视图（app.py 的 /api/chat、/api/state 引用）──
    @property
    def client(self) -> "_ClientProxy":
        return _ClientProxy(self)

    # ── 状态（Keeper 兼容字段 ← brain.ui 映射）─────────────────
    async def _mod_up(self, host: str, port: int) -> bool:
        """Tower mod 服务是否在线（TCP 端口探测；断开连接后仍应为 True）。"""
        try:
            reader, writer = await asyncio.open_connection(host, port, limit=1)
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:  # noqa: BLE001
            return False

    async def status(self) -> dict[str, Any]:
        self._ensure_brain()
        # 配置缺失（game_dir 未填 / tower.json 不存在）：返回可读状态供前端引导
        if self._brain is None:
            return {
                "mod_host": "127.0.0.1",
                "mod_port": 24778,
                "mod_up": False,
                "connected": False,
                "protocol": None,
                "llm_ready": False,
                "llm": "",
                "active_model_label": None,
                "agent_running": False,
                "agent_paused": False,
                "goal": "",
                "wf": {"kind": None, "title": "", "idx": 0, "total": 0, "steps": []},
                "config_error": self._brain_error or "大脑未初始化",
                "decisions_count": 0,
                "events_count": 0,
                "launch_cmd": "",
                "token_configured": self._has_key(),
                "player_name": "",
                "log_count": len(self._command_log),
                "daemon_started": self._started,
            }
        b = self._brain
        ui = b.ui or {}
        st = ui.get("status") or {}
        p = ui.get("player") or {}
        conn = str(st.get("conn", ""))
        connected = conn.startswith("已连接")
        # agent_running 用 Brain 的活动计划对象判断（ui.plan 的"（空闲）"占位字符串非空会误判）
        # active_model_label 用 settings.json 激活模型的 label（模型管理 Phase 4）
        from .settings import load_settings

        _s = load_settings()
        _active_label = None
        if _s.active_model_id:
            _am = next((x for x in _s.models if x.id == _s.active_model_id), None)
            if _am is not None:
                _active_label = _am.label
        return {
            "mod_host": (b.cfg.get("connection") or {}).get("host", "127.0.0.1"),
            "mod_port": (b.cfg.get("connection") or {}).get("port", 24778),
            "mod_up": connected or await self._mod_up(
                (b.cfg.get("connection") or {}).get("host", "127.0.0.1"),
                (b.cfg.get("connection") or {}).get("port", 24778),
            ),
            "connected": connected,
            "protocol": 1,
            # llm_ready 基于配置判断（连接前 ui.status 未填充，避免启动误触发设置引导）
            "llm_ready": bool(st.get("model")) or bool((b.cfg.get("api") or {}).get("model")),
            "llm": st.get("llm") or "",
            "active_model_label": _active_label or st.get("model") or None,
            "agent_running": b.plan is not None,
            "agent_paused": False,
            "goal": ui.get("plan") or "",
            "config_error": None,
            # ── 工作流（GitHub Actions 风格流水线数据）──
            "wf": self._workflow_state(b),
            "decisions_count": 0,
            "events_count": 0,
            "launch_cmd": "",
            "token_configured": self._has_key(),
            "player_name": p.get("name", ""),
            "log_count": len(self._command_log),
            "daemon_started": self._started,
        }

    def _workflow_state(self, b) -> dict[str, Any]:
        """构造 GitHub Actions 风格工作流状态（plan 优先，其次 outline）。"""
        # ── plan 工作流（executor 逐步骤执行）──
        if b.plan is not None and b.executor is not None:
            ex = b.executor
            steps = (ex.plan or {}).get("steps", [])
            # ex.results = [(step_dict, result_text), ...]；用对象 id 做索引
            result_map = {id(k): v for k, v in ex.results}
            wf_steps = []
            for i, s in enumerate(steps):
                if i < ex.step_i:
                    st = "done"
                elif i == ex.step_i:
                    st = "running"
                else:
                    st = "pending"
                wf_steps.append({
                    "name": s.get("name", "?"),
                    "type": s.get("type", "tool"),
                    "status": st,
                    "detail": str(result_map.get(id(s), ""))[:200],
                })
            return {
                "kind": "plan",
                "title": (ex.plan or {}).get("goal", ""),
                "idx": ex.step_i + 1 if steps else 0,
                "total": len(steps),
                "steps": wf_steps,
            }
        # ── outline 工作流（大纲逐级执行）──
        if b.outline is not None:
            o = b.outline
            wf_steps = [
                {
                    "name": s,
                    "type": "outline",
                    "status": "done" if i < o.idx else ("running" if i == o.idx else "pending"),
                    "detail": str(o.results.get(i, "")),
                }
                for i, s in enumerate(o.steps)
            ]
            return {
                "kind": "outline",
                "title": o.title,
                "idx": min(o.idx + 1, len(o.steps)),
                "total": len(o.steps),
                "steps": wf_steps,
            }
        return {"kind": None, "title": "", "idx": 0, "total": 0, "steps": []}

    def _has_key(self) -> bool:
        try:
            data = json.loads(BRAIN_KEY_FILE.read_text(encoding="utf-8"))
            return bool(str(data.get("api_key", "")).strip())
        except Exception:  # noqa: BLE001
            return False

    # ── 玩家状态（/api/state ← 实时 get_state，最小延迟）─────────
    async def state(self) -> dict[str, Any]:
        self._ensure_brain()
        if self._brain is None:
            return {"player": {}}
        b = self._brain
        if b.client is None:
            return {"player": {}}
        try:
            st = await asyncio.to_thread(b.client.ok, "get_state")  # 实时取，不走 5s 缓存
        except Exception:  # noqa: BLE001
            return {"player": {}}
        p = st.get("player", {})
        return {
            "player": {
                "name": p.get("name", ""),
                "dimension": p.get("dimension", "?"),
                "position": p.get("position", {}),
                "rotation": p.get("rotation", {}),
                "health": p.get("health"),
                "food": p.get("food"),
                "inventory": st.get("inventory", {}),
            }
        }

    # ── 连接 / 断开（Brain 手动控制）───────────────────────────
    async def connect(self, launch: bool = True, timeout: float = 60.0) -> bool:
        self._ensure_brain()
        if self._brain is None:
            self._log("system", "connect", "无法连接：大脑未初始化（请先检查配置）")
            return False
        self._brain.reconnect()  # 清手动断开标志，允许重连
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = (self._brain.ui or {}).get("status") or {}
            if str(st.get("conn", "")).startswith("已连接"):
                self._log("system", "connect", "已连接 Tower mod")
                return True
            await asyncio.sleep(1)
        return False

    async def disconnect(self) -> None:
        self._ensure_brain()
        if self._brain is None:
            return
        self._brain.disconnect()  # 手动断开：关闭连接 + 阻止自动重连（不再半分钟自动连回）
        self._log("system", "disconnect", "已断开 Tower mod")

    # ── Agent 控制（Manager 层封装，不改 brain.py 主循环）───────
    def start_agent(self, goal: str = "") -> None:
        """把目标作为指令发给大脑（大脑排 plan → 技能执行）。"""
        self._ensure_brain()
        if self._brain is None:
            self._log("system", "agent", "AI 无法启动：大脑未初始化（请先检查配置）")
            return
        text = goal.strip() if goal.strip() else "继续执行当前目标"
        self._brain.submit_chat(text)
        self._log("system", "agent", f"AI 启动，目标={goal or '（观察模式）'}")

    async def stop_agent(self) -> None:
        """停止当前计划：executor 归零 + 清空活动计划。"""
        self._ensure_brain()
        if self._brain is None:
            return
        b = self._brain
        if b.executor is not None:
            try:
                b.executor.safe_stop()
            except Exception as e:  # noqa: BLE001
                logger.warning("safe_stop 失败: %s", e)
        b.plan = None
        if b.ui is not None:
            b.ui["plan"] = None
        self._log("system", "agent", "AI 已停止（急停）")

    async def pause_agent(self) -> None:
        """暂停：同急停（Tower 无独立暂停，暂停=停计划；断点由 outline 机制恢复）。"""
        await self.stop_agent()
        self._log("system", "agent", "AI 已暂停")

    async def resume_agent(self) -> None:
        self._log("system", "agent", "AI 恢复（未实现独立暂停，可重新设定目标）")

    def set_goal(self, goal: str) -> None:
        if goal.strip():
            self.start_agent(goal)

    async def send_chat(self, message: str) -> dict[str, Any]:
        self._ensure_brain()
        if self._brain is not None:
            self._brain.submit_chat(message)
        self._log("player", "chat", message)
        return {"sent": True}

    # ── 日志 / 决策 / 事件 ──────────────────────────────────────
    def command_log(self, limit: int = 200) -> list[dict[str, Any]]:
        return list(self._command_log)[-limit:]

    def decisions(self, limit: int = 100) -> list[dict[str, Any]]:
        return []  # Tower 用工作流进度而非决策记录（Phase 3 对接）

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        return []  # Phase 3 对接 Tower 寻路/工作流事件

    def _log(self, origin: str, kind: str, text: str) -> None:
        entry = {"ts": time.time(), "origin": origin, "kind": kind, "text": text}
        self._command_log.append(entry)
        # 同步方法里不能直接 await：仅在事件循环运行中才调度广播
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        asyncio.ensure_future(self._broadcast({"type": "log", "data": entry}))

    # ── 设置 / 模型（Phase 4：settings.json 驱动 brain.llm 热切换）──
    def get_settings_public(self) -> dict[str, Any]:
        from .settings import load_settings

        s = load_settings()
        return {
            "log_enabled": s.log_enabled,
            "log_dir": s.log_dir,
            "token_configured": self._has_key(),
            "mod_port": 24778,
        }

    async def apply_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        from .settings import load_settings, save_settings

        s = load_settings()
        if "log_enabled" in body:
            s.log_enabled = bool(body["log_enabled"])
        if "log_dir" in body:
            s.log_dir = str(body["log_dir"])
        save_settings(s)
        return self.get_settings_public()

    def _fallback_model(self) -> dict[str, Any]:
        """settings.json 无模型时，从当前大脑配置构造初始模型（兜底）。"""
        api_cfg = (self._brain.cfg or {}).get("api", {}) if self._brain else {}
        active = api_cfg.get("model", "deepseek-v4-flash")
        return {
            "id": "default",
            "label": active,
            "provider": "deepseek",
            "model": active,
            "api_key": "",
            "base_url": api_cfg.get("base_url", ""),
            "temperature": api_cfg.get("temperature"),
            "top_p": None,
            "enable_thinking": False,
            "reasoning_effort": None,
            "max_tokens": api_cfg.get("max_tokens", 2048),
        }

    def models(self) -> dict[str, Any]:
        from .settings import load_settings, mask_api_key

        s = load_settings()
        if not s.models:
            return {
                "models": [self._fallback_model()],
                "active_model_id": "default",
                "vision_model_id": "",
            }
        return {
            "models": [
                m.model_copy(update={"api_key": mask_api_key(m.api_key)}).model_dump()
                for m in s.models
            ],
            "active_model_id": s.active_model_id,
            "vision_model_id": s.vision_model_id,
        }

    def _apply_llm(self, m) -> None:
        """把对话模型配置热应用到 brain.llm（Phase 4）。"""
        self._ensure_brain()
        if self._brain is None:
            return
        llm = self._brain.llm
        if llm is None:
            return
        llm.update_config(
            base_url=m.base_url or None,
            api_key=m.api_key or None,
            model=m.model,
            temperature=m.temperature,
            max_tokens=m.max_tokens,
        )
        logger.info("已热切换对话模型: %s (%s)", m.label, m.model)

    def _apply_vision(self, m) -> None:
        """把视觉模型配置热应用到 brain.llm；m 为 None 时清除（跟随对话模型）。"""
        self._ensure_brain()
        if self._brain is None:
            return
        llm = self._brain.llm
        if llm is None:
            return
        if m is None:
            llm.update_config(clear_vision=True)
            logger.info("已清除视觉模型（跟随对话模型）")
            return
        llm.update_config(
            vision_base_url=m.base_url or None,
            vision_api_key=m.api_key or None,
            vision_model=m.model or None,
            vision_max_tokens=m.max_tokens or None,
        )
        logger.info("已热切换视觉模型: %s (%s)", m.label, m.model)

    def create_model(self, body: dict[str, Any]) -> dict[str, Any]:
        import uuid

        from .settings import LLMModel, load_settings, save_settings

        s = load_settings()
        entry = LLMModel(
            id=uuid.uuid4().hex[:8],
            label=(body.get("label") or body.get("name") or body.get("model") or "").strip(),
            provider=(body.get("provider") or "openai").strip(),
            base_url=(body.get("base_url") or "").strip(),
            api_key=(body.get("api_key") or "").strip(),
            model=(body.get("model") or "").strip(),
            temperature=float(body["temperature"]) if body.get("temperature") is not None else 0.7,
            max_tokens=int(body.get("max_tokens") or 1024),
        )
        if not entry.model:
            raise ValueError("模型名称（model）不能为空")
        if not entry.label:
            entry.label = entry.model
        s.models.append(entry)
        if not s.active_model_id:
            s.active_model_id = entry.id
            self._apply_llm(entry)
        save_settings(s)
        logger.info("新建模型: %s (%s)", entry.label, entry.model)
        return self.models()

    def update_model(self, model_id: str, body: dict[str, Any]) -> dict[str, Any]:
        from .settings import load_settings, save_settings

        s = load_settings()
        for i, m in enumerate(s.models):
            if m.id != model_id:
                continue
            if body.get("model") is not None and not str(body.get("model") or "").strip():
                raise ValueError("模型名称（model）不能为空")
            api_key = str(body.get("api_key", m.api_key))
            if "…" in api_key or (api_key and set(api_key) <= {"*"}):
                api_key = m.api_key  # 前端回传脱敏 key：保留原值
            upd = {
                "label": str(body.get("label", m.label)).strip() or m.label,
                "provider": str(body.get("provider", m.provider)).strip() or m.provider,
                "model": str(body.get("model", m.model)).strip(),
                "api_key": api_key.strip(),
                "base_url": str(body.get("base_url", m.base_url)).strip(),
                "temperature": (
                    float(body["temperature"]) if body.get("temperature") is not None else m.temperature
                ),
                "max_tokens": (
                    int(body["max_tokens"]) if body.get("max_tokens") is not None else m.max_tokens
                ),
            }
            s.models[i] = m.model_copy(update=upd)
            if m.id == s.active_model_id:
                self._apply_llm(s.models[i])
            if m.id == s.vision_model_id:
                self._apply_vision(s.models[i])
            save_settings(s)
            logger.info("更新模型: %s", s.models[i].label)
            return self.models()
        raise KeyError(model_id)

    def delete_model(self, model_id: str) -> dict[str, Any]:
        from .settings import load_settings, save_settings

        s = load_settings()
        before = len(s.models)
        s.models = [m for m in s.models if m.id != model_id]
        if len(s.models) == before:
            raise KeyError(model_id)
        if s.active_model_id == model_id:
            s.active_model_id = ""
        if s.vision_model_id == model_id:
            s.vision_model_id = ""
            self._apply_vision(None)
        save_settings(s)
        logger.info("删除模型: %s", model_id)
        return self.models()

    def activate_model(self, model_id: str) -> dict[str, Any]:
        from .settings import load_settings, save_settings

        s = load_settings()
        m = next((x for x in s.models if x.id == model_id), None)
        if m is None:
            raise KeyError(model_id)
        s.active_model_id = model_id
        save_settings(s)
        self._apply_llm(m)
        logger.info("切换对话模型: %s (%s)", m.label, m.model)
        return self.models()

    def set_vision_model(self, model_id: str | None) -> dict[str, Any]:
        """设置视觉模型角色；model_id 为 None/空 → 跟随对话模型。"""
        from .settings import load_settings, save_settings

        s = load_settings()
        if model_id:
            m = next((x for x in s.models if x.id == model_id), None)
            if m is None:
                raise KeyError(model_id)
            s.vision_model_id = model_id
            save_settings(s)
            self._apply_vision(m)
            logger.info("切换视觉模型: %s (%s)", m.label, m.model)
        else:
            s.vision_model_id = ""
            save_settings(s)
            self._apply_vision(None)
        return self.models()

    async def list_models_for(self, model_id: str) -> dict[str, Any]:
        import httpx

        from .settings import load_settings

        s = load_settings()
        m = next((x for x in s.models if x.id == model_id), None)
        if m is None:
            raise KeyError(model_id)
        base = (m.base_url or "").rstrip("/")
        if not base:
            raise ValueError("该模型未配置 base_url")
        headers = {"Authorization": f"Bearer {m.api_key}"} if m.api_key else {}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{base}/models", headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"拉取模型列表失败: {exc}") from exc
        items = data.get("data") or []
        models = [
            {"id": item.get("id") or "", "display_name": item.get("display_name"),
             "context_length": item.get("context_length")}
            for item in items
            if item.get("id")
        ]
        return {"models": models}

    async def test_llm(self) -> dict[str, Any]:
        return {"ok": True, "reply": "（Tower 大脑已就绪）"}

    def export_logs(self) -> dict[str, Any]:
        return {"path": "", "count": len(self._command_log)}

    # ── WS 广播 ────────────────────────────────────────────────
    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    async def broadcast_status(self) -> None:
        await self._broadcast({"type": "status", "data": await self.status()})

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:  # pragma: no cover
                pass
