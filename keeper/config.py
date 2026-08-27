"""KeeperMC 后端配置加载。

优先级：环境变量 > .env 文件 > 默认值。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ModConfig(BaseModel):
    """Mod WebSocket 连接配置。"""

    host: str = "127.0.0.1"
    port: int = Field(24777, ge=1, le=65535)
    token: str = ""
    heartbeat_s: float = Field(25.0, gt=0.0, le=30.0)
    reconnect_s: float = Field(3.0, gt=0.0)


class LLMConfig(BaseModel):
    """OpenAI 兼容 LLM 配置。"""

    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = ""
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(1024, ge=1)
    top_p: float | None = Field(None, ge=0.0, le=1.0)
    enable_thinking: bool = False
    reasoning_effort: str | None = None
    request_timeout_s: float = Field(90.0, gt=0.0)


class AgentConfig(BaseModel):
    """Agent 决策循环配置。"""

    loop_interval_s: float = Field(0.6, gt=0.0)
    max_actions_per_cycle: int = Field(3, ge=1)
    context_tokens: int = Field(6000, ge=256)
    history_len: int = Field(20, ge=1)
    empty_retries: int = Field(1, ge=0, le=3)  # LLM 空输出重试次数
    memory_len: int = Field(4, ge=0, le=20)  # 回填给 LLM 的历史轮次数
    max_repeat: int = Field(4, ge=1, le=20)  # 相同动作连续执行上限（护栏）
    low_health: float = Field(6.0, ge=1.0, le=20.0)  # 生命低于此值拦截移动类


class DaemonConfig(BaseModel):
    """常驻服务 / 管理接口配置。"""

    host: str = "127.0.0.1"
    port: int = Field(8090, ge=1, le=65535)
    log_keep: int = Field(2000, ge=1)


class LauncherConfig(BaseModel):
    """游戏启动器配置。"""

    mod_config_path: str = ""  # mod 生成的 config/keyboard.json 路径（自动读 token/port）
    launch_cmd: str = ""  # 启动游戏命令（可留空=手动启动）
    ready_timeout_s: float = Field(180.0, gt=0.0)


class Config(BaseModel):
    mod: ModConfig = ModConfig()
    llm: LLMConfig = LLMConfig()
    agent: AgentConfig = AgentConfig()
    daemon: DaemonConfig = DaemonConfig()
    launcher: LauncherConfig = LauncherConfig()


def _str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def load_config(env_file: Optional[Path] = None) -> Config:
    """加载配置。env_file 缺省时读取项目根目录 .env（存在才加载）。"""
    if env_file is None:
        env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        load_dotenv(env_file)

    return Config(
        mod=ModConfig(
            host=_str("KEEPER_MOD_HOST", "127.0.0.1"),
            port=_int("KEEPER_MOD_PORT", 24777),
            token=_str("KEEPER_MOD_TOKEN", ""),
            heartbeat_s=_float("KEEPER_HEARTBEAT_S", 25.0),
            reconnect_s=_float("KEEPER_RECONNECT_S", 3.0),
        ),
        llm=LLMConfig(
            base_url=_str("LLM_BASE_URL", "https://api.openai.com/v1"),
            api_key=_str("LLM_API_KEY", ""),
            model=_str("LLM_MODEL", ""),
            temperature=_float("LLM_TEMPERATURE", 0.7),
            max_tokens=_int("LLM_MAX_TOKENS", 1024),
            request_timeout_s=_float("LLM_TIMEOUT_S", 90.0),
        ),
        agent=AgentConfig(
            empty_retries=_int("KEEPER_EMPTY_RETRIES", 1),
            memory_len=_int("KEEPER_MEMORY_LEN", 4),
            max_repeat=_int("KEEPER_MAX_REPEAT", 4),
            low_health=_float("KEEPER_LOW_HEALTH", 6.0),
            loop_interval_s=_float("KEEPER_LOOP_INTERVAL_S", 0.6),
            max_actions_per_cycle=_int("KEEPER_MAX_ACTIONS_PER_CYCLE", 3),
            context_tokens=_int("KEEPER_CONTEXT_TOKENS", 6000),
            history_len=_int("KEEPER_HISTORY_LEN", 20),
        ),
        daemon=DaemonConfig(
            host=_str("KEEPER_DAEMON_HOST", "127.0.0.1"),
            port=_int("KEEPER_DAEMON_PORT", 8090),
            log_keep=_int("KEEPER_LOG_KEEP", 2000),
        ),
        launcher=LauncherConfig(
            mod_config_path=_str("KEEPER_MOD_CONFIG", ""),
            launch_cmd=_str("KEEPER_MC_LAUNCH", ""),
            ready_timeout_s=_float("KEEPER_READY_TIMEOUT_S", 180.0),
        ),
    )
