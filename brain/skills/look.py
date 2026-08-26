#!/usr/bin/env python3
"""看图技能（M5.3 视觉管线）：截图 → 等文件就绪 → 压缩 → vision 模型描述场景。

LLM 可调 look{prompt} 评估画面（环境/方块/生物/建筑）；后续可接入「排计划前自动看一眼」。
"""

import logging
import time as _time
from pathlib import Path

from ._base import Skill

log = logging.getLogger("brain.skills")

WAIT_TIMEOUT = 6.0    # 等截图文件就绪的最长时间（秒）


class LookSkill(Skill):
    name = "look"
    description = (
        "查看当前画面：截图并用视觉模型描述场景（环境/方块/生物/建筑）。"
        "可选 prompt 指定关注点"
    )

    def run(self, ctx, args):
        prompt = (args.get("prompt") or "").strip() or \
            "用中文简要描述当前画面：环境、可见的重要方块/生物/建筑。"
        if ctx.llm is None:
            return "失败：未接入视觉模型（llm 未注入）"
        shot = ctx.ok("screenshot")
        path = (shot or {}).get("path", "")
        if not path:
            return "失败：截图未返回文件路径"
        p = Path(path)
        # Tower 截图异步写入：等文件存在且大小稳定再读（SKILL 诊断手册）
        if not self._wait_ready(p):
            return f"失败：截图文件未就绪 {path}"
        try:
            desc = ctx.llm.look(str(p), prompt)
        except Exception as e:
            log.error("看图失败: %s", e)
            return f"失败：看图失败 {e}"
        return f"完成：{desc}"

    def _wait_ready(self, p: Path, timeout: float = WAIT_TIMEOUT) -> bool:
        """等截图文件就绪：存在 + 大小连续两次一致（异步写入稳定）。"""
        deadline = _time.time() + timeout
        last_size = -1
        while _time.time() < deadline:
            if p.exists():
                s = p.stat().st_size
                if s > 0 and s == last_size:
                    return True
                last_size = s
            _time.sleep(0.3)
        return False


skill = LookSkill()
