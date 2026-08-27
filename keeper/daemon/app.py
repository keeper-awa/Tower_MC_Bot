"""FastAPI 管理接口：REST + WebSocket 实时推送 + 托管前端。

- /api/*            REST 控制与查询
- /ws               实时推送 status / decision / event
- /                 托管 webui/dist（存在时）
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .manager import Manager

logger = logging.getLogger(__name__)

WEBUI_DIST = Path(__file__).resolve().parent.parent / "webui" / "dist"
WALLPAPER_DIR = Path(__file__).resolve().parents[2] / "wallpaper"
ALLOWED_IMG = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class ConnectBody(BaseModel):
    launch: bool = False
    timeout: float = 15.0


class StartAgentBody(BaseModel):
    goal: str = ""


class ChatBody(BaseModel):
    message: str = ""


class SettingsBody(BaseModel):
    log_enabled: bool = False
    log_dir: str = "logs"


class ModelBody(BaseModel):
    """新建/更新模型；字段 None 表示「未提供」（更新时保留原值）。"""

    label: str | None = None
    name: str | None = None  # 兼容旧字段
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    enable_thinking: bool | None = None
    reasoning_effort: str | None = None
    max_tokens: int | None = None


class GoalBody(BaseModel):
    goal: str = ""


def create_app(manager: Manager) -> FastAPI:
    app = FastAPI(title="KeeperMC 管理接口", version="0.1.0")

    # ------------------------------------------------------------ REST
    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        return await manager.status()

    @app.post("/api/connect")
    async def api_connect(body: ConnectBody) -> dict[str, Any]:
        ok = await manager.connect(launch=body.launch, timeout=body.timeout)
        await manager.broadcast_status()
        return {"ok": ok}

    @app.post("/api/disconnect")
    async def api_disconnect() -> dict[str, Any]:
        await manager.disconnect()
        await manager.broadcast_status()
        return await manager.status()

    @app.get("/api/log")
    async def api_log(limit: int = 200) -> list[dict[str, Any]]:
        """命令日志（由谁发起的命令记录：玩家 / AI / 系统）。"""
        return manager.command_log(limit)

    @app.post("/api/agent/start")
    async def api_agent_start(body: StartAgentBody) -> dict[str, Any]:
        manager.start_agent(body.goal)
        await manager.broadcast_status()
        return await manager.status()

    @app.post("/api/agent/stop")
    async def api_agent_stop() -> dict[str, Any]:
        await manager.stop_agent()
        await manager.broadcast_status()
        return await manager.status()

    @app.post("/api/agent/pause")
    async def api_agent_pause() -> dict[str, Any]:
        await manager.pause_agent()
        await manager.broadcast_status()
        return await manager.status()

    @app.post("/api/agent/resume")
    async def api_agent_resume() -> dict[str, Any]:
        manager.resume_agent()
        await manager.broadcast_status()
        return await manager.status()

    @app.post("/api/goal")
    async def api_set_goal(body: GoalBody) -> dict[str, Any]:
        manager.set_goal(body.goal)
        await manager.broadcast_status()
        return await manager.status()

    @app.post("/api/chat")
    async def api_chat(body: ChatBody) -> dict[str, Any]:
        """面板直接发送聊天消息（不进决策循环）。"""
        if not manager.client.connected:
            raise HTTPException(status_code=409, detail="未连接到 mod")
        return await manager.send_chat(body.message)

    @app.get("/api/decisions")
    async def api_decisions(limit: int = 100) -> list[dict[str, Any]]:
        return manager.decisions(limit)

    @app.get("/api/events")
    async def api_events(limit: int = 100) -> list[dict[str, Any]]:
        return manager.events(limit)

    @app.get("/api/state")
    async def api_state() -> dict[str, Any]:
        """实时玩家状态快照（需已连接 mod）。"""
        if not manager.client.connected:
            raise HTTPException(status_code=409, detail="未连接到 mod")
        return await manager.client.get_state()

    # ------------------------------------------------------------ 设置 / 日志
    @app.get("/api/settings")
    async def api_get_settings() -> dict[str, Any]:
        return manager.get_settings_public()

    @app.post("/api/settings")
    async def api_save_settings(body: SettingsBody) -> dict[str, Any]:
        return await manager.apply_settings(body.model_dump())

    # ------------------------------------------------------------ 模型管理
    @app.get("/api/models")
    async def api_models() -> dict[str, Any]:
        return manager.models()

    @app.post("/api/models")
    async def api_create_model(body: ModelBody) -> dict[str, Any]:
        try:
            data = manager.create_model(body.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await manager.broadcast_status()
        return data

    @app.put("/api/models/{model_id}")
    async def api_update_model(model_id: str, body: ModelBody) -> dict[str, Any]:
        try:
            data = manager.update_model(model_id, body.model_dump())
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="模型不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await manager.broadcast_status()
        return data

    @app.delete("/api/models/{model_id}")
    async def api_delete_model(model_id: str) -> dict[str, Any]:
        try:
            data = manager.delete_model(model_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="模型不存在") from exc
        await manager.broadcast_status()
        return data

    @app.post("/api/models/{model_id}/activate")
    async def api_activate_model(model_id: str) -> dict[str, Any]:
        try:
            data = manager.activate_model(model_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="模型不存在") from exc
        await manager.broadcast_status()
        return data

    @app.post("/api/models/{model_id}/activate-vision")
    async def api_activate_vision(model_id: str) -> dict[str, Any]:
        try:
            data = manager.set_vision_model(model_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="模型不存在") from exc
        await manager.broadcast_status()
        return data

    @app.post("/api/models/vision/clear")
    async def api_clear_vision() -> dict[str, Any]:
        data = manager.set_vision_model(None)
        await manager.broadcast_status()
        return data

    @app.post("/api/models/{model_id}/fetch-models")
    async def api_fetch_models(model_id: str) -> dict[str, Any]:
        try:
            return await manager.list_models_for(model_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="模型不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/llm/test")
    async def api_llm_test() -> dict[str, Any]:
        return await manager.test_llm()

    @app.post("/api/logs/export")
    async def api_logs_export() -> dict[str, Any]:
        return manager.export_logs()

    # ------------------------------------------------------------ 壁纸
    def _wallpaper_info() -> dict[str, Any]:
        for f in sorted(WALLPAPER_DIR.glob("bg.*")):
            return {"url": f"/wallpaper/{f.name}", "ts": int(f.stat().st_mtime)}
        return {"url": None}

    @app.get("/api/wallpaper")
    async def api_get_wallpaper() -> dict[str, Any]:
        return _wallpaper_info()

    @app.post("/api/wallpaper")
    async def api_upload_wallpaper(file: UploadFile = File(...)) -> dict[str, Any]:
        ext = Path(file.filename or "").suffix.lower()
        if ext not in ALLOWED_IMG:
            raise HTTPException(status_code=400, detail="仅支持 jpg/png/webp/gif 图片")
        WALLPAPER_DIR.mkdir(parents=True, exist_ok=True)
        for f in WALLPAPER_DIR.glob("bg.*"):
            f.unlink()
        dest = WALLPAPER_DIR / f"bg{ext}"
        with dest.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        logger.info("壁纸已更新: %s", dest)
        return _wallpaper_info()

    @app.delete("/api/wallpaper")
    async def api_remove_wallpaper() -> dict[str, Any]:
        removed = False
        for f in WALLPAPER_DIR.glob("bg.*"):
            f.unlink()
            removed = True
        if removed:
            logger.info("壁纸已移除")
        return _wallpaper_info()

    # ------------------------------------------------------------ WebSocket
    @app.websocket("/ws")
    async def ws_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = await manager.subscribe()
        try:
            await websocket.send_json({"type": "status", "data": await manager.status()})
            while True:
                payload = await queue.get()
                await websocket.send_json(payload)
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            logger.exception("WS 流异常")
        finally:
            manager.unsubscribe(queue)

    # ------------------------------------------------------------ 前端托管
    WALLPAPER_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/wallpaper", StaticFiles(directory=WALLPAPER_DIR), name="wallpaper")
    # MC 物品/方块图标（从游戏 jar 提取；缺失时为空目录，前端降级为文本）
    from .mc_icons import ICONS_DIR, ensure_icons  # noqa: PLC0415

    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/mc-icons", StaticFiles(directory=ICONS_DIR), name="mc-icons")
    try:
        # 直接从 BRAIN_CONFIG 读 game_dir（brain 可能未初始化：clone 后配置缺失）
        import yaml as _yaml  # noqa: PLC0415

        from .tower_manager import BRAIN_CONFIG  # noqa: PLC0415

        _cfg_game_dir = ""
        if BRAIN_CONFIG.exists():
            _cfg_game_dir = str(
                ((_yaml.safe_load(BRAIN_CONFIG.read_text(encoding="utf-8")) or {}).get("connection") or {}).get("game_dir", "") or ""
            )
        if _cfg_game_dir:
            ensure_icons(_cfg_game_dir)
    except Exception:  # noqa: BLE001
        logger.warning("MC 图标提取失败（前端将显示物品文本）", exc_info=True)
    if WEBUI_DIST.exists():
        assets_dir = WEBUI_DIST / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            # index.html 无 hash，禁用缓存避免用户看到旧构建
            return FileResponse(
                WEBUI_DIST / "index.html",
                headers={"Cache-Control": "no-cache"},
            )
    else:
        @app.get("/", include_in_schema=False)
        async def index_placeholder() -> JSONResponse:
            return JSONResponse(
                {
                    "name": "KeeperMC 管理接口",
                    "message": "前端尚未构建（keeper/webui/dist 不存在）。构建后刷新本页。",
                    "api": "/api/status",
                }
            )

    return app
