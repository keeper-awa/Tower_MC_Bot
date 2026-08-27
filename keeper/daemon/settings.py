"""daemon 可持久化设置：写入 `settings.json`（不入库），供管理面板配置。

优先级：环境变量/.env 作为默认值；settings.json 中已填写的字段覆盖之。
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = PROJECT_ROOT / "settings.json"


class LLMModel(BaseModel):
    """一个可管理的 LLM 模型条目（OpenAI 兼容，参照 LingChat 的 LLM Provider 结构）。

    - provider: 提供商类型（openai / deepseek / lmstudio / gemini / kimicode 等）
    - enable_thinking / reasoning_effort: DeepSeek 等模型的思考开关与档位
    """

    id: str
    label: str = ""
    provider: str = "openai"
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    temperature: float | None = Field(0.7, ge=0.0, le=2.0)
    top_p: float | None = Field(None, ge=0.0, le=1.0)
    enable_thinking: bool = False
    reasoning_effort: str | None = None
    max_tokens: int = Field(1024, ge=1)
    # 兼容旧版 name 字段（一次性迁移到 label）
    name: str = ""

    @model_validator(mode="before")
    @classmethod
    def _legacy_name(cls, data: object) -> object:
        if isinstance(data, dict) and data.get("name") and not data.get("label"):
            data = {**data, "label": data["name"]}
        return data


class Settings(BaseModel):
    """用户可在管理面板填写的设置。空字符串表示「未填写/用默认」。

    - 旧版顶层 llm_* 字段仅作一次性迁移源（见 Manager._migrate_legacy_llm），
      新逻辑由 models + active_model_id 驱动。
    """

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_temperature: float = Field(0.7, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(1024, ge=1)
    models: list[LLMModel] = []
    active_model_id: str = ""       # 对话模型（chat 角色）
    vision_model_id: str = ""       # 视觉模型（vision 角色）；空 = 跟随对话模型
    log_enabled: bool = False
    log_dir: str = "logs"


def load_settings() -> Settings:
    if SETTINGS_PATH.exists():
        try:
            return Settings.model_validate_json(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 配置损坏则回退默认
            pass
    return Settings()


def save_settings(settings: Settings) -> None:
    SETTINGS_PATH.write_text(settings.model_dump_json(indent=2), encoding="utf-8")


def mask_api_key(key: str) -> str:
    """脱敏显示 api_key：sk-abc…xyz。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:6]}…{key[-4:]}"
