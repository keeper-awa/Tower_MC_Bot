"""Mod 返回错误与客户端本地错误。"""
from __future__ import annotations


class ModError(Exception):
    """Mod 返回 `ok:false` 时的错误。code 对应协议 §8。

    code 为 `None` 表示非 Mod 错误（如本地超时/断线）。
    """

    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:  # pragma: no cover - 仅展示
        if self.code is not None:
            return f"ModError({self.code}): {self.message}"
        return f"ModError: {self.message}"


class ParamError(ValueError):
    """动作参数校验失败（本地侧，发送前）。"""


class NotConnectedError(ConnectionError):
    """尚未连接到 Mod。"""
