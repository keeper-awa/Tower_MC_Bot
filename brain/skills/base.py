#!/usr/bin/env python3
"""技能基类：技能 = 确定性 Python 代码（执行阶段不经 LLM）。

- Skill.run(ctx, args) 返回汇报文本：成功以"完成"开头，失败以"失败"开头
- SkillContext：client 访问 / 等事件 / 安全检查点 / 组合调用其他技能
  （由 PlanExecutor 构建注入，技能内部不直接依赖 executor 实现）
"""


class Skill:
    name = ""            # 注册名（LLM plan 步骤引用）
    description = ""     # 注入 LLM 的能力说明

    def run(self, ctx, args: dict) -> str:
        raise NotImplementedError


class SkillContext:
    """技能运行上下文（由 PlanExecutor 构建并注入）。"""

    def __init__(self, client, tools, executor=None, skills=None):
        self.client = client
        self.tools = tools
        self.executor = executor
        self.skills = skills

    def ok(self, action, params=None):
        """调用 Tower 动作，失败抛异常（同 TowerClient.ok）。"""
        return self.client.ok(action, params)

    def tools_execute(self, name, args=None):
        """执行工具动作（含 2000 字符截断），返回文本。"""
        return self.tools.execute(name, args or {})

    def wait_event(self, names, timeout=60, interruptible=True):
        """等待事件之一（超时返回 (None, None)；紧急/聊天中断抛 PlanInterrupt）。"""
        return self.executor.wait_event(names, timeout, interruptible)

    def checkpoint(self):
        """安全检查点：低血/岩浆危险抛 PlanInterrupt("urgent")。"""
        self.executor.safety_checkpoint()

    def run_skill(self, name, args=None):
        """组合调用其他技能（如做工作台内部复用砍树）。"""
        return self.skills.run(name, self, args or {})
