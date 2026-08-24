#!/usr/bin/env python3
"""记忆管理：memory.md（经验教训）/ goals.md（保留文件，M5.2 起不再由 AI 读写）。

- 每次 LLM 调用前读取注入 system prompt（截断到 memory_max_bytes）
- update_memory 工具追加带编号条目（写保护：超上限拒绝）
- goals.md 仅保留文件供玩家手工编辑长期偏好，不再注入/写入
"""

import logging
import re
from pathlib import Path

log = logging.getLogger("brain.memory")


class MemoryManager:
    def __init__(self, memory_dir: Path, max_bytes: int = 20000, inject_limit: int = 2000):
        self.memory_dir = Path(memory_dir)
        self.max_bytes = max_bytes
        self.inject_limit = inject_limit  # 注入 system prompt 的截断长度（省 token）
        self.goals_path = self.memory_dir / "goals.md"
        self.memory_path = self.memory_dir / "memory.md"
        self._ensure_files()

    def _ensure_files(self):
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        for p, title in ((self.goals_path, "# 长期目标（AI 可自行更新）"), (self.memory_path, "# 经验教训（AI 可自行更新）")):
            if not p.exists():
                p.write_text(title + "\n", encoding="utf-8")

    def inject(self) -> str:
        """拼装经验教训供 system prompt 使用（goals 不再注入——AI 不再自设目标）。"""
        lessons = self._read(self.memory_path)
        if len(lessons) > self.inject_limit:
            lessons = lessons[:self.inject_limit] + "\n...(经验超长已截断)"
        return f"【经验】\n{lessons}"

    def append(self, section: str, content: str) -> str:
        """追加一条带编号经验教训；返回提示文本给 LLM。"""
        if section != "lesson":
            return "参数错误：仅支持 lesson（AI 不再自行更新长期目标）"
        path = self.memory_path
        lines = [l for l in self._read(path).splitlines() if l.strip()]
        num = 1
        for line in reversed(lines):
            m = re.match(r"^\d+\.", line.strip())
            if m:
                num = int(m.group(0).rstrip(".")) + 1
                break
        entry = f"{num}. {content.strip()}\n"
        if len(self._read(path)) + len(entry) > self.max_bytes:
            return "记忆文件已达大小上限，请合并旧条目或不要新增"
        with open(path, "a", encoding="utf-8") as f:
            f.write(entry)
        log.info("记忆更新 [%s]: %s", section, content[:60])
        return f"已记录（{path.name} 第 {num} 条）"

    def _read(self, path: Path) -> str:
        try:
            text = path.read_text(encoding="utf-8")
            if len(text) > self.max_bytes:
                text = text[:self.max_bytes] + "\n...(记忆超长已截断)"
            return text
        except OSError:
            return ""
