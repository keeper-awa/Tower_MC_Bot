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
import time

log = logging.getLogger("brain.llm")


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: dict, api_key: str = None):
        self.cfg = cfg
        # key 唯一来源：大脑传入的 config/api_key.json（无其他兜底）
        if not api_key:
            raise LLMError("未配置 API key：请填写 config/api_key.json 中的 api_key 后重启大脑")
        # 延迟导入：dry-run 模式不依赖 openai SDK
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=cfg.get("base_url", "https://api.deepseek.com/v1"))
        self.model = cfg.get("model", "deepseek-v4-flash")
        self.vision_model = cfg.get("vision_model", "deepseek-v4-flash-vision-exp")
        self.temperature = cfg.get("temperature", 0.7)
        self.max_tokens = cfg.get("max_tokens", 2048)
        # vision 是推理型模型：会先输出大量 reasoning 再输出 content，
        # 单独给更大的上限防止 reasoning 占满后 content 为空（finish=length text_len=0）
        self.vision_max_tokens = cfg.get("vision_max_tokens", 4096)

    def chat(self, messages: list, tools: list = None, vision: bool = False) -> dict:
        """一次对话补全。

        返回：{"text": str, "tool_calls": [{"id", "name", "arguments"(dict)}, ...]}
        抛 LLMError：key 无效（401）、限流（429）、网络等。
        """
        kwargs = {
            "model": self.vision_model if vision else self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.vision_max_tokens if vision else self.max_tokens,
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
        # 诊断：响应形态（finish_reason 为 length 说明被 max_tokens 截断）
        log.info("LLM 响应: finish=%s text_len=%d tool_calls=%d",
                 getattr(resp.choices[0], "finish_reason", "?"), len(text), len(tool_calls))
        for tc in tool_calls:
            log.info("LLM 工具调用: %s %s", tc["name"],
                     json.dumps(tc["arguments"], ensure_ascii=False)[:300])
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

    def look(self, image_path: str, prompt: str = "", max_px: int = 800) -> str:
        """看图（M5.3 视觉管线）：读截图 → 压缩 → vision 模型描述场景。

        返回模型描述文本；失败抛 LLMError。
        """
        user_content = [
            {"type": "text", "text": prompt or "用中文简要描述当前画面：环境、可见的重要方块/生物/建筑。"},
            self.vision_message(image_path, max_px),
        ]
        resp = self.chat([{"role": "user", "content": user_content}], vision=True)
        return resp["text"].strip() or "（模型无文本回复）"
