"""LLM 决策输出解析与校验。

把模型返回的文本解析成结构化动作（Decision），并用 `actions.build_action`
做参数校验，拦截幻觉/非法动作（抛 ParseError，供 Agent 反馈给模型）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..mc.actions import build_action
from ..mc.errors import ParamError


class ParseError(ValueError):
    """LLM 输出无法解析或动作非法。"""


@dataclass
class Decision:
    """一次解析出的决策。action 为 None 表示「无需动作」（等待/观察）。"""

    action: str | None
    params: dict[str, Any] = field(default_factory=dict)
    think: str = ""
    raw: str = ""


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S | re.I)


def strip_fence(text: str) -> str:
    """去掉可选的三反引号代码块包裹。"""
    match = _FENCE.match(text)
    return match.group(1) if match else text.strip()


def parse_decision(text: str) -> Decision:
    """解析模型输出为 Decision；非法时抛 ParseError。"""
    cleaned = strip_fence(text)
    if not cleaned:
        raise ParseError("空输出")
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ParseError(f"非法 JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ParseError("输出必须是 JSON 对象")

    think = str(obj.get("think", ""))
    action = obj.get("action")
    if action is None:
        # 无需动作
        return Decision(None, {}, think=think, raw=text)

    params = obj.get("params") or {}
    if not isinstance(params, dict):
        raise ParseError("params 必须是对象")
    try:
        validated = build_action(action, params)
    except ParamError as exc:
        raise ParseError(f"动作参数校验失败: {exc}") from exc

    return Decision(validated["action"], validated["params"], think=think, raw=text)
