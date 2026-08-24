#!/usr/bin/env python3
"""技能注册表（brain/skills/*.py）：每个文件定义一个可执行技能。

- 文件以 `_` 开头（如 _base.py）不注册
- 每个文件暴露模块级 `skill = XxxSkill()` 实例
- SkillManager.run 统一捕获异常 → 失败文本（PlanInterrupt 除外，向上传递）
"""

import importlib
import json
import logging
from pathlib import Path

log = logging.getLogger("brain.skills")


class SkillManager:
    def __init__(self, skills_dir: Path):
        self.skills = {}
        for f in sorted(Path(skills_dir).glob("*.py")):
            if f.name.startswith("_"):
                continue
            mod = importlib.import_module(f".{f.stem}", package=__package__)
            skill = getattr(mod, "skill", None)
            if skill is None or not getattr(skill, "name", ""):
                log.warning("跳过 %s：无模块级 skill 实例", f.name)
                continue
            self.skills[skill.name] = skill
        log.info("技能库加载: %d 个（%s）", len(self.skills), "、".join(self.skills) or "无")

    def names(self) -> list:
        return list(self.skills)

    def get(self, name):
        return self.skills.get(name)

    def describe(self) -> str:
        """技能清单（注入 system prompt 供 LLM 排计划时引用）。"""
        lines = [f"- {s.name}：{s.description}" for s in self.skills.values()]
        return "\n".join(lines) if lines else "（无）"

    def run(self, name: str, ctx, args: dict) -> str:
        """执行技能，返回汇报文本（异常 → "技能执行失败: ..."，不抛出）。"""
        skill = self.skills.get(name)
        if skill is None:
            return f"失败：未知技能 {name}"
        log.info("执行技能: %s %s", name, json.dumps(args or {}, ensure_ascii=False)[:120])
        try:
            return skill.run(ctx, args or {})
        except Exception as e:
            from executor import PlanInterrupt  # 函数级导入：避免包初始化顺序问题
            if isinstance(e, PlanInterrupt):
                raise  # 计划中断（urgent/chat）由执行器处理，不吞
            log.error("技能 %s 异常: %s", name, e)
            return f"技能执行失败: {e}"
