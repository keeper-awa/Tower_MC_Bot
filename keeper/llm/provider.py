"""OpenAI 兼容 LLM 提供者。

用户通过 `.env` 自填任意兼容端点：
- LLM_BASE_URL：OpenAI / DeepSeek / 硅基流动 / 本地 Ollama(http://localhost:11434/v1) 等
- LLM_API_KEY：密钥（本地服务可留空）
- LLM_MODEL：模型名（用户自己填想用的模型）
"""
from __future__ import annotations

import logging
from typing import Any

from ..config import LLMConfig

logger = logging.getLogger(__name__)


class LLMProvider:
    """封装 OpenAI 兼容 Chat Completions 接口。"""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._client: Any | None = None

    # ------------------------------------------------------------ 客户端
    def _create_client(self) -> Any:
        """创建异步 OpenAI 客户端（可被测试覆写）。"""
        from openai import AsyncOpenAI

        return AsyncOpenAI(
            base_url=self.config.base_url or None,
            api_key=self.config.api_key or "not-needed",  # 本地服务可能无需 key
            timeout=self.config.request_timeout_s,
        )

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self._create_client()
        return self._client

    @property
    def ready(self) -> bool:
        """配置是否可发起请求（需要 base_url 与 model）。"""
        return bool(self.config.base_url and self.config.model)

    def update_config(self, **kwargs) -> None:
        """热更新配置（面板保存设置时调用），并重置客户端以便下次用新配置重建。"""
        changes = {k: v for k, v in kwargs.items() if v is not None}
        if changes:
            self.config = self.config.model_copy(update=changes)
            self._client = None

    def describe(self) -> str:
        if self.ready:
            return f"model={self.config.model} @ {self.config.base_url}"
        missing = []
        if not self.config.base_url:
            missing.append("LLM_BASE_URL")
        if not self.config.model:
            missing.append("LLM_MODEL")
        return f"未就绪，缺少 {'、'.join(missing)}（请在 .env 填写）"

    # ------------------------------------------------------------ 对话
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        enable_thinking: bool | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        """调用模型，返回助手文本内容。"""
        if not self.ready:
            raise RuntimeError(f"LLM 未配置: {self.describe()}")
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.config.max_tokens,
        }
        top = top_p if top_p is not None else self.config.top_p
        if top is not None:
            kwargs["top_p"] = top
        think = enable_thinking if enable_thinking is not None else self.config.enable_thinking
        effort = reasoning_effort if reasoning_effort is not None else self.config.reasoning_effort
        if think or effort:
            extra: dict[str, Any] = {}
            if think:
                extra["enable_thinking"] = True
            if effort:
                extra["reasoning_effort"] = effort
            kwargs["extra_body"] = extra
        response = await client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""
