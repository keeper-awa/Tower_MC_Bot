#!/usr/bin/env python3
"""任务大纲管理（M5.3 任务系统）：复杂任务的总大纲存临时文件，逐级执行。

- LLM 用 outline 工具输出 {title, steps[]}（抽象步骤，可几十步）
- 大纲持久化到 config/tasks/outline.json —— 崩溃/重启后可断点恢复
- 每完成一步更新 idx 与 results；新任务/中止标记 status
"""

import json
import logging
from pathlib import Path

log = logging.getLogger("brain.outline")


class OutlineManager:
    MAX_STEPS = 50  # 大纲步骤上限
    def __init__(self, cfg_dir: Path):
        self.path = Path(cfg_dir) / "tasks" / "outline.json"
        self.title = ""
        self.steps = []
        self.idx = 0          # 当前步骤下标（已完成 idx 步）
        self.results = {}     # {步骤下标: 执行结果文本}
        self.status = "running"  # running / done / aborted

    # ── 文件读写 ────────────────────────────────────────────────
    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            log.warning("大纲保存失败: %s", e)

    def _to_dict(self) -> dict:
        return {"title": self.title, "steps": self.steps, "idx": self.idx,
                "results": self.results, "status": self.status}

    @classmethod
    def load(cls, cfg_dir: Path):
        """从文件恢复大纲；不存在或非法返回 None。"""
        path = Path(cfg_dir) / "tasks" / "outline.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            m = cls(cfg_dir)
            m.title = str(data.get("title", ""))
            m.steps = [s for s in data.get("steps", []) if isinstance(s, str) and s.strip()]
            m.idx = int(data.get("idx", 0))
            m.results = {int(k): v for k, v in (data.get("results") or {}).items()}
            m.status = str(data.get("status", "running"))
            if not m.title or not m.steps:
                return None
            return m
        except Exception as e:
            log.warning("大纲文件解析失败（忽略）: %s", e)
            return None

    # ── 生命周期 ────────────────────────────────────────────────
    def start(self, title: str, steps: list):
        self.title = title
        self.steps = [s for s in steps if s.strip()]
        self.idx = 0
        self.results = {}
        self.status = "running"
        self.save()
        log.info("大纲已创建: %s（%d 步）", title, len(self.steps))

    def complete_step(self, index: int, result: str):
        """标记一步完成（结果文本记录，供后续步骤参考）。"""
        self.results[str(index)] = result[:200]
        self.idx = max(self.idx, index + 1)
        self.save()

    def abort(self):
        self.status = "aborted"
        self.save()

    def finish(self):
        self.status = "done"
        self.save()

    def clear(self):
        """新任务开始时清理旧大纲文件。"""
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def status_text(self) -> str:
        """大纲进度文本（UI/上下文用）。"""
        lines = [f"【大纲】{self.title}（{self.idx}/{len(self.steps)} 步）"]
        for i, s in enumerate(self.steps):
            mark = "✓" if str(i) in self.results else ("→" if i == self.idx else "·")
            lines.append(f"{mark} {i + 1}. {s}")
        return "\n".join(lines)

    def done_list(self) -> str:
        """已完成步骤摘要（注入逐级规划上下文，省 token）。"""
        return "；".join(f"{i + 1}.{self.steps[i]}" for i in range(self.idx)
                         if str(i) in self.results)
