#!/usr/bin/env python3
"""DeepSeek LLM 封装（OpenAI 兼容接口，工具调用）。

职责：
- chat(messages, tools)：一次对话补全，返回 {text, tool_calls} 或抛异常
- 视觉消息：vision_message(image_path) —— 读截图 → Pillow 压缩 → base64 data URL
  （M5.3 视觉管线；M5.1 先留接口）

费用：暂不设上限（用户约定：LLM 输出格式讨论时再定）；max_tokens 由 config 控制。
"""

import base64
import io
import json
import logging
import os
import time

log = logging.getLogger("brain.llm")


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        api_key = cfg.get("api_key") or os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise LLMError("未配置 API key：请填写 brain/config.yaml 的 api.api_key 或设置环境变量 DEEPSEEK_API_KEY")
        # 延迟导入：dry-run 模式不依赖 openai SDK
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=cfg.get("base_url", "https://api.deepseek.com/v1"))
        self.model = cfg.get("model", "deepseek-v4-flash")
        self.vision_model = cfg.get("vision_model", "deepseek-v4-flash-vision-exp")
        self.temperature = cfg.get("temperature", 0.7)
        self.max_tokens = cfg.get("max_tokens", 2048)

    def chat(self, messages: list, tools: list = None, vision: bool = False) -> dict:
        """一次对话补全。

        返回：{"text": str, "tool_calls": [{"id", "name", "arguments"(dict)}, ...]}
        抛 LLMError：key 无效（401）、限流（429）、网络等。
        """
        kwargs = {
            "model": self.vision_model if vision else self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except Exception as e:
            # 简单重试一次（网络抖动/限流）
            time.sleep(1.5)
            try:
                resp = self.client.chat.completions.create(**kwargs)
            except Exception as e2:
                raise LLMError(f"LLM 调用失败: {e2}") from e2

        msg = resp.choices[0].message
        text = msg.content or ""
        tool_calls = []
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append({"id": tc.id, "name": tc.function.name, "arguments": args})
        return {
            "text": text,
            "tool_calls": tool_calls,
            # 原始 assistant 消息（含 tool_calls）：回喂时须原样放回消息序列
            # （协议要求 tool 消息必须跟在带 tool_calls 的 assistant 消息之后）
            "assistant_msg": msg.model_dump(exclude_none=True),
            # token 用量（费用统计用；响应可能不含 usage）
            "usage": {"input": resp.usage.prompt_tokens if resp.usage else 0,
                      "output": resp.usage.completion_tokens if resp.usage else 0},
        }

    def vision_message(self, image_path: str, max_px: int = 800) -> dict:
        """读截图 → 压缩 → base64 data URL（供消息体 image_url 使用）。

        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
        """
        from PIL import Image

        img = Image.open(image_path)
        img.thumbnail((max_px, max_px), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
