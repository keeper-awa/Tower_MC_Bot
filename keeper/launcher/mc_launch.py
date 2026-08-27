"""游戏启动器：自动拉起 MC + 等待 mod WebSocket 就绪 + 读取 token/port。

「无头」说明：Windows 上 MC 无法真正无显示运行，推荐把窗口最小化后台运行。
启动命令由用户配置（`KEEPER_MC_LAUNCH`，如 PCL 或直接 java 命令）；不配置则跳过自动启动。
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from ..config import LauncherConfig, ModConfig

logger = logging.getLogger(__name__)


def read_mod_config(path: str | Path) -> dict[str, Any]:
    """读取 mod 生成的 `config/keyboard.json`，返回 {port, token, disconnect_reset}。"""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"找不到 mod 配置文件: {config_path}")
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return {
        "port": int(data.get("port", 24777)),
        "token": str(data.get("token", "")),
        "disconnect_reset": bool(data.get("disconnect_reset", True)),
    }


async def probe_port(host: str, port: int, timeout: float = 1.0) -> bool:
    """探测 TCP 端口是否可达。"""
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception:  # noqa: BLE001
        return False


class GameLauncher:
    """管理游戏进程生命周期，并等待 mod 就绪。"""

    def __init__(self, mod_cfg: ModConfig, launcher_cfg: LauncherConfig) -> None:
        self._mod = mod_cfg
        self._lcfg = launcher_cfg
        self._proc: subprocess.Popen | None = None

    # ------------------------------------------------------------ token
    def resolve_token(self) -> str:
        """token 优先级：ModConfig.token（env 配置）> 读取 keyboard.json。"""
        if self._mod.token:
            return self._mod.token
        if self._lcfg.mod_config_path:
            return read_mod_config(self._lcfg.mod_config_path)["token"]
        return ""

    def resolve_port(self) -> int:
        """port 优先级同 token（keyboard.json 里的 port 可能被改过）。"""
        if self._lcfg.mod_config_path and Path(self._lcfg.mod_config_path).exists():
            return read_mod_config(self._lcfg.mod_config_path)["port"]
        return self._mod.port

    # ------------------------------------------------------------ 进程
    def launch(self) -> None:
        """按 `launch_cmd` 启动游戏；未配置则提示手动启动。"""
        if not self._lcfg.launch_cmd:
            logger.warning("未配置启动命令（KEEPER_MC_LAUNCH），跳过自动启动，请手动启动游戏")
            return
        logger.info("启动游戏: %s", self._lcfg.launch_cmd)
        self._proc = subprocess.Popen(self._lcfg.launch_cmd, shell=True)

    def stop(self) -> None:
        """终止已启动的游戏进程（未由本类启动的进程不受影响）。"""
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            logger.info("已请求终止游戏进程")

    # ------------------------------------------------------------ 就绪探测
    async def is_mod_up(self) -> bool:
        return await probe_port(self._mod.host, self._mod.port)

    async def wait_ready(self, timeout: float | None = None) -> bool:
        """轮询直到 mod WS 端口就绪。返回是否就绪。"""
        timeout = timeout if timeout is not None else self._lcfg.ready_timeout_s
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if await self.is_mod_up():
                logger.info("mod 服务已就绪（%s:%s）", self._mod.host, self._mod.port)
                return True
            await asyncio.sleep(1.0)
        logger.warning("等待 mod 就绪超时（%.0fs）", timeout)
        return False
