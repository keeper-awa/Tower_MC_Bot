"""常驻编排：launcher + client + provider + agent 生命周期、状态与广播。

`Manager` 是 daemon 的核心对象：
- 解析 token/port（走 GameLauncher），创建 KeyboardClient / LLMProvider / AgentLoop
- 维护决策记录、事件缓冲
- 提供 agent 启停/暂停/目标控制
- 向 WebSocket 订阅者广播 status/decision/event
"""
from __future__ import annotations

import asyncio
import httpx
import json
import logging
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from ..agent.loop import AgentLoop, DecisionRecord
from ..agent.memory import GoalManager
from ..config import Config
from ..launcher.mc_launch import GameLauncher, probe_port
from ..llm.provider import LLMProvider
from ..mc.client import KeyboardClient
from ..mc.events import Event
from .settings import LLMModel, Settings, load_settings, mask_api_key, save_settings

logger = logging.getLogger(__name__)


def _record_to_dict(record: DecisionRecord) -> dict[str, Any]:
    return {
        "ts": record.ts,
        "observation": record.observation,
        "llm_output": record.llm_output,
        "think": record.think,
        "action": record.action,
        "params": record.params,
        "result": record.result,
        "error": record.error,
        "latency": record.latency,
    }


class Manager:
    def __init__(self, config: Config) -> None:
        self._cfg = config
        self._launcher = GameLauncher(config.mod, config.launcher)
        client_cfg = config.mod.model_copy(
            update={"token": self._launcher.resolve_token(), "port": self._launcher.resolve_port()}
        )
        self._client = KeyboardClient(client_cfg)
        self._settings = load_settings()
        self._migrate_legacy_llm()
        self._provider = LLMProvider(self._active_llm_config(config.llm))
        self._goal = GoalManager()
        self._loop: AgentLoop | None = None
        self._agent_task: asyncio.Task | None = None
        self._decisions: deque[DecisionRecord] = deque(maxlen=config.daemon.log_keep)
        self._events: deque[dict[str, Any]] = deque(maxlen=200)
        self._subs: set[asyncio.Queue] = set()
        self._client.on_event(self._on_event)
        self._command_log: deque[dict[str, Any]] = deque(maxlen=config.daemon.log_keep)
        self.player_name = ""
        self._started = time.time()

    # ------------------------------------------------------------ 只读
    @property
    def client(self) -> KeyboardClient:
        return self._client

    @property
    def provider(self) -> LLMProvider:
        return self._provider

    @property
    def goal(self) -> str:
        return self._goal.goal

    @property
    def agent_running(self) -> bool:
        return self._agent_task is not None and not self._agent_task.done()

    @staticmethod
    def _merge_llm_config(llm_cfg: Any, settings: Settings) -> Any:
        """settings 中已填写的字段覆盖 .env 默认值。"""
        overrides: dict[str, Any] = {}
        for src, dst in (
            (settings.llm_base_url, "base_url"),
            (settings.llm_api_key, "api_key"),
            (settings.llm_model, "model"),
        ):
            if src:
                overrides[dst] = src
        if settings.llm_temperature:
            overrides["temperature"] = settings.llm_temperature
        if settings.llm_max_tokens:
            overrides["max_tokens"] = settings.llm_max_tokens
        return llm_cfg.model_copy(update=overrides) if overrides else llm_cfg

    # ------------------------------------------------------------ 模型管理
    def _migrate_legacy_llm(self) -> None:
        """旧版顶层 llm_* 字段 → 一次性迁移为模型列表并激活。"""
        if self._settings.models:
            return
        legacy = any(
            (self._settings.llm_base_url, self._settings.llm_api_key, self._settings.llm_model)
        )
        if not legacy:
            return
        self._settings.models = [
            LLMModel(
                id="default",
                label=self._settings.llm_model or "默认模型",
                provider="openai",
                base_url=self._settings.llm_base_url,
                api_key=self._settings.llm_api_key,
                model=self._settings.llm_model,
                temperature=self._settings.llm_temperature,
                max_tokens=self._settings.llm_max_tokens,
            )
        ]
        self._settings.active_model_id = "default"
        save_settings(self._settings)
        logger.info("已把旧版 llm_* 设置迁移为模型: %s", self._settings.models[0].model)

    def _find_model(self, model_id: str) -> LLMModel:
        for m in self._settings.models:
            if m.id == model_id:
                return m
        raise KeyError(model_id)

    def _active_model(self) -> LLMModel | None:
        for m in self._settings.models:
            if m.id == self._settings.active_model_id:
                return m
        return None

    def _active_llm_config(self, llm_cfg: Any) -> Any:
        """active 模型覆盖 .env 默认；无 active 时回退旧字段/.env。"""
        model = self._active_model()
        if model is None:
            return self._merge_llm_config(llm_cfg, self._settings)
        overrides: dict[str, Any] = {}
        if model.base_url:
            overrides["base_url"] = model.base_url
        if model.api_key:
            overrides["api_key"] = model.api_key
        if model.model:
            overrides["model"] = model.model
        overrides["temperature"] = model.temperature
        overrides["max_tokens"] = model.max_tokens
        if model.top_p is not None:
            overrides["top_p"] = model.top_p
        overrides["enable_thinking"] = model.enable_thinking
        if model.reasoning_effort:
            overrides["reasoning_effort"] = model.reasoning_effort
        return llm_cfg.model_copy(update=overrides)

    def _apply_active_model(self) -> None:
        """把当前 active 模型的配置热应用到 provider。"""
        cfg = self._active_llm_config(self._cfg.llm)
        self._provider.update_config(
            base_url=cfg.base_url or None,
            api_key=cfg.api_key or None,
            model=cfg.model or None,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            top_p=cfg.top_p,
            enable_thinking=cfg.enable_thinking,
            reasoning_effort=cfg.reasoning_effort,
        )

    def _provider_for_model(self, m: LLMModel) -> LLMProvider:
        """按某个模型条目的配置构造独立 provider（用于测试，不影响当前生效）。"""
        base = self._cfg.llm
        cfg = base.model_copy(
            update={
                "base_url": m.base_url or base.base_url,
                "api_key": m.api_key or base.api_key,
                "model": m.model,
                "temperature": m.temperature if m.temperature is not None else base.temperature,
                "max_tokens": m.max_tokens,
                "top_p": m.top_p,
                "enable_thinking": m.enable_thinking,
                "reasoning_effort": m.reasoning_effort,
            }
        )
        return LLMProvider(cfg)

    def models(self) -> dict[str, Any]:
        """模型列表（api_key 脱敏）+ 当前激活 id。"""
        return {
            "models": [
                m.model_copy(update={"api_key": mask_api_key(m.api_key)}).model_dump()
                for m in self._settings.models
            ],
            "active_model_id": self._settings.active_model_id,
        }

    def create_model(self, body: dict[str, Any]) -> dict[str, Any]:
        """新建模型；若当前无激活项则自动激活。"""
        entry = LLMModel(
            id=uuid.uuid4().hex[:8],
            label=(body.get("label") or body.get("name") or "").strip(),
            provider=(body.get("provider") or "openai").strip(),
            base_url=(body.get("base_url") or "").strip(),
            api_key=(body.get("api_key") or "").strip(),
            model=(body.get("model") or "").strip(),
            temperature=float(body["temperature"])
            if body.get("temperature") is not None
            else 0.7,
            top_p=float(body["top_p"]) if body.get("top_p") is not None else None,
            enable_thinking=bool(body.get("enable_thinking", False)),
            reasoning_effort=(body.get("reasoning_effort") or "").strip() or None,
            max_tokens=int(body.get("max_tokens") or 1024),
        )
        if not entry.model:
            raise ValueError("模型名称（model）不能为空")
        if not entry.label:
            entry.label = entry.model
        self._settings.models.append(entry)
        if not self._settings.active_model_id:
            self._settings.active_model_id = entry.id
            self._apply_active_model()
        save_settings(self._settings)
        logger.info("新建模型: %s (%s)", entry.label, entry.model)
        return self.models()

    def update_model(self, model_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """更新已有模型（字段可选，未提供则保留原值；前端回传脱敏 key 时保留原 key）。"""
        m = self._find_model(model_id)
        if body.get("model") is not None and not str(body.get("model") or "").strip():
            raise ValueError("模型名称（model）不能为空")

        def _pick(name: str, current: Any) -> Any:
            return body[name] if body.get(name) is not None else current

        api_key = str(_pick("api_key", m.api_key))
        # 前端回传脱敏 key：含 … 或全 * 时保留原值
        if "…" in api_key or (api_key and set(api_key) <= {"*"}):
            api_key = m.api_key
        updates = {
            "label": str(_pick("label", m.label)).strip(),
            "provider": str(_pick("provider", m.provider)).strip() or "openai",
            "model": str(_pick("model", m.model)).strip(),
            "api_key": api_key.strip(),
            "base_url": str(_pick("base_url", m.base_url)).strip(),
            "temperature": (
                float(body["temperature"]) if body.get("temperature") is not None else m.temperature
            ),
            "top_p": (float(body["top_p"]) if body.get("top_p") is not None else m.top_p),
            "enable_thinking": bool(_pick("enable_thinking", m.enable_thinking)),
            "reasoning_effort": (
                (body.get("reasoning_effort") or "").strip() or None
                if body.get("reasoning_effort") is not None
                else m.reasoning_effort
            ),
            "max_tokens": (
                int(body["max_tokens"]) if body.get("max_tokens") is not None else m.max_tokens
            ),
        }
        if not updates["label"]:
            updates["label"] = updates["model"]
        entry = m.model_copy(update=updates)
        self._settings.models = [entry if x.id == model_id else x for x in self._settings.models]
        if entry.id == self._settings.active_model_id:
            self._apply_active_model()
        save_settings(self._settings)
        logger.info("更新模型: %s (%s)", entry.label, entry.model)
        return self.models()

    def delete_model(self, model_id: str) -> dict[str, Any]:
        """删除模型；若删除的是激活项，则回退 .env 默认。"""
        before = len(self._settings.models)
        self._settings.models = [m for m in self._settings.models if m.id != model_id]
        if len(self._settings.models) == before:
            raise KeyError(model_id)
        if self._settings.active_model_id == model_id:
            self._settings.active_model_id = ""
            self._apply_active_model()
        save_settings(self._settings)
        logger.info("删除模型: %s", model_id)
        return self.models()

    def activate_model(self, model_id: str) -> dict[str, Any]:
        """设为当前生效模型。"""
        self._find_model(model_id)
        self._settings.active_model_id = model_id
        self._apply_active_model()
        save_settings(self._settings)
        logger.info("切换模型: %s", model_id)
        return self.models()

    async def list_models_for(self, model_id: str) -> dict[str, Any]:
        """调用该模型条目的 base_url /models 拉取可用模型列表。"""
        m = self._find_model(model_id)
        base = (m.base_url or self._cfg.llm.base_url or "").rstrip("/")
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
            {
                "id": item.get("id") or "",
                "display_name": item.get("display_name"),
                "context_length": item.get("context_length"),
            }
            for item in items
            if item.get("id")
        ]
        return {"models": models}

    async def test_model(self, model_id: str) -> dict[str, Any]:
        """用某个模型条目的配置做一次极小调用，验证连通性（不影响当前生效）。"""
        m = self._find_model(model_id)
        try:
            prov = self._provider_for_model(m)
            text = await prov.chat([{"role": "user", "content": "回复 OK"}], max_tokens=10)
            return {"ok": True, "reply": text or "（空回复）"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    async def status(self) -> dict[str, Any]:
        mod_port = self._launcher.resolve_port()
        return {
            "mod_host": self._cfg.mod.host,
            "mod_port": mod_port,
            "mod_up": await probe_port(self._cfg.mod.host, mod_port),
            "connected": self._client.connected,
            "protocol": self._client.protocol,
            "llm_ready": self._provider.ready,
            "llm": self._provider.describe(),
            "active_model_label": (
                self._active_model().label if self._active_model() is not None else None
            ),
            "agent_running": self.agent_running,
            "agent_paused": bool(self._loop and self._loop.safety.paused),
            "goal": self._goal.goal,
            "decisions_count": len(self._decisions),
            "events_count": len(self._events),
            "launch_cmd": self._cfg.launcher.launch_cmd or "",
            "token_configured": bool(self._launcher.resolve_token()),
            "player_name": self.player_name,
            "log_count": len(self._command_log),
            "daemon_started": self._started,
        }

    def decisions(self, limit: int = 100) -> list[dict[str, Any]]:
        return [_record_to_dict(r) for r in list(self._decisions)[-limit:]]

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        return list(self._events)[-limit:]

    # ------------------------------------------------------------ 连接
    async def connect(self, launch: bool = True, timeout: float = 15.0) -> bool:
        """启动（可选）并连接 mod。返回是否连接成功。"""
        if launch:
            self._launcher.launch()
        await self._client.start()
        ok = await self._client.wait_connected(timeout=timeout)
        if ok:
            logger.info("已连接 mod（protocol=%s）", self._client.protocol)
            self._log("system", "connect", f"已连接 mod（protocol={self._client.protocol}）")
        return ok

    async def close(self) -> None:
        await self.stop_agent()
        await self._client.close()

    async def disconnect(self) -> None:
        """断开与 mod 的连接（daemon 保持运行）。"""
        await self.stop_agent()
        await self._client.close()
        self._log("system", "disconnect", "已断开与 mod 的连接")

    # ------------------------------------------------------------ agent 控制
    def start_agent(self, goal: str = "") -> None:
        if self.agent_running:
            logger.warning("agent 已在运行，忽略 start_agent")
            return
        if goal:
            self._goal.set_goal(goal)
        self._loop = AgentLoop(
            self._client, self._provider, self._cfg.agent,
            goal=self._goal.goal, on_decision=self._on_decision,
        )
        self._agent_task = asyncio.create_task(self._loop.run())
        logger.info("agent 启动，目标=%r", self._goal.goal)
        self._log("system", "agent", f"AI 启动，目标={self._goal.goal or '（观察模式）'}")

    async def stop_agent(self) -> None:
        """急停：停止决策循环 + 重置玩家持续输入（让玩家停下）。"""
        if self._loop is not None:
            self._loop.stop()
        if self._agent_task is not None:
            self._agent_task.cancel()
        self._agent_task = None
        self._loop = None
        await self._reset_controls()
        logger.info("agent 已停止（持续输入已归零）")
        self._log("system", "agent", "AI 已停止（急停）")

    async def _reset_controls(self) -> None:
        """把 mod 的持续输入归零（move/jump/sneak/sprint/look_at），否则玩家会一直保持最后指令。"""
        if not self._client.connected:
            return
        resets = [
            ("move", {"forward": 0.0, "backward": 0.0, "left": 0.0, "right": 0.0}),
            ("jump", {"value": False}),
            ("sneak", {"value": False}),
            ("sprint", {"value": False}),
            ("look_at", {}),
        ]
        for action, params in resets:
            try:
                await self._client.request(action, params, timeout=5)
            except Exception:  # noqa: BLE001 - 尽力而为
                logger.debug("重置 %s 失败（忽略）", action)

    async def pause_agent(self) -> None:
        """暂停：停止决策 + 重置玩家持续输入（玩家停下，恢复后由 AI 重新决策）。"""
        if self._loop is not None:
            self._loop.pause()
        await self._reset_controls()
        logger.info("agent 已暂停（持续输入已归零）")
        self._log("system", "agent", "AI 已暂停")

    def resume_agent(self) -> None:
        if self._loop is not None:
            self._loop.resume()
            self._log("system", "agent", "AI 已恢复")

    def set_goal(self, goal: str) -> None:
        self._goal.set_goal(goal)
        if self._loop is not None:
            self._loop.set_goal(goal)
        if goal:
            self._log("player", "goal", f"玩家设定目标：{goal}")

    async def send_chat(self, message: str) -> dict[str, Any]:
        """面板直发聊天消息（不经决策循环）。"""
        from ..mc.actions import build_chat

        req = build_chat(message)
        result = await self._client.request(req["action"], req["params"])
        self._log("player", "chat", f"控制台发送：{message}")
        return {"sent": bool(result.get("sent")) if isinstance(result, dict) else True}

    # ------------------------------------------------------------ 广播
    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def _broadcast(self, payload: dict[str, Any]) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:  # noqa: PERF203
                pass

    async def broadcast_status(self) -> None:
        self._broadcast({"type": "status", "data": await self.status()})

    def _log(self, origin: str, kind: str, text: str) -> None:
        entry = {"ts": time.time(), "origin": origin, "kind": kind, "text": text}
        self._command_log.append(entry)
        self._broadcast({"type": "log", "data": entry})

    def command_log(self, limit: int = 200) -> list[dict[str, Any]]:
        return list(self._command_log)[-limit:]

    def _on_decision(self, record: DecisionRecord) -> None:
        self._decisions.append(record)
        data = _record_to_dict(record)
        self._broadcast({"type": "decision", "data": data})
        self._write_log("decisions", data)
        action_txt = f"{record.action} {record.params}" if record.action else "（无动作）"
        if record.error:
            action_txt += f" ✗{record.error}"
        self._log("ai", "decision", f"[{record.think or '…'}] → {action_txt}")

    def _on_event(self, event: Event) -> None:
        entry = {"name": event.name, "data": event.data, "ts": time.time()}
        self._events.append(entry)
        self._broadcast({"type": "event", "data": entry})
        self._write_log("events", entry)
        if event.name == "chat":
            data = event.data or {}
            sender = str(data.get("sender", ""))
            if data.get("self"):
                self.player_name = sender
            self._log("player", "chat", f"{sender}: {data.get('message')}")

    # ------------------------------------------------------------ 设置 / 日志
    def get_settings_public(self) -> dict[str, Any]:
        """返回设置；LLM 相关已迁移到模型管理（/api/models）。"""
        s = self._settings
        return {
            "log_enabled": s.log_enabled,
            "log_dir": s.log_dir or "logs",
            "token_configured": bool(self._launcher.resolve_token()),
            "mod_port": self._launcher.resolve_port(),
        }

    async def apply_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        """保存日志设置；LLM 配置由 /api/models 管理。"""
        current = self._settings
        merged = current.model_copy(
            update={
                "log_enabled": bool(body.get("log_enabled", current.log_enabled)),
                "log_dir": (body.get("log_dir") or current.log_dir).strip(),
            }
        )
        save_settings(merged)
        self._settings = merged
        await self.broadcast_status()
        return self.get_settings_public()

    async def test_llm(self) -> dict[str, Any]:
        """用当前 LLM 配置做一次极小调用，验证连通性。"""
        try:
            text = await self._provider.chat([{"role": "user", "content": "回复 OK"}], max_tokens=10)
            return {"ok": True, "reply": text or "（空回复）"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def export_logs(self) -> dict[str, Any]:
        """把当前决策/事件缓冲导出为单个 JSON 文件。"""
        ts = time.strftime("%Y%m%d-%H%M%S")
        log_dir = Path(self._settings.log_dir or "logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        file = log_dir / f"keeper-{ts}.json"
        payload = {
            "exported_at": time.time(),
            "status": {"goal": self._goal.goal, "agent_running": self.agent_running},
            "decisions": self.decisions(1000),
            "events": self.events(1000),
        }
        file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("日志已导出: %s", file)
        return {"path": str(file), "count": len(self._decisions)}

    def _write_log(self, kind: str, data: dict[str, Any]) -> None:
        if not self._settings.log_enabled:
            return
        try:
            log_dir = Path(self._settings.log_dir or "logs")
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_dir / f"{kind}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            logger.debug("写入日志失败（忽略）")
