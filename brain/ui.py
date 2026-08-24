#!/usr/bin/env python3
"""Tower AI 大脑图形界面（exe 交付用，tkinter 内置库）。

五大面板：
- 状态：连接/前置链路/模型/LLM 调用统计
- 玩家：位置/血量/饥饿/生物群系/手持（每 5s 由大脑线程刷新）
- 工作流：当前计划目标/步骤进度/步骤结果
- 日志：大脑日志（滚动，替代控制台）
- 会话：输入消息发给 LLM（等价于游戏内聊天，AI 回复会出现在日志与游戏内）

线程模型：大脑逻辑在后台线程，本界面只轮询大脑暴露的 ui 状态字典
（brain.ui）+ 日志队列，全部更新在主线程（tkinter 线程安全要求）。
"""

import logging
import os
import queue
import tkinter as tk
from tkinter import scrolledtext, ttk


class UILogHandler(logging.Handler):
    """日志转发到队列（由主线程 after() 轮询渲染到日志面板）。"""

    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record):
        try:
            self.q.put(self.format(record))
        except Exception:
            pass


class BrainUI:
    def __init__(self, root: tk.Tk, brain):
        self.root = root
        self.brain = brain
        self.ui = brain.ui
        self.log_q = queue.Queue(maxsize=2000)

        root.title("Tower AI 大脑")
        root.geometry("760x640")
        root.minsize(600, 480)

        self._build()
        self._attach_log_handler()
        root.after(400, self._refresh)

    # ── 界面构建 ────────────────────────────────────────────────
    def _build(self):
        pad = {"padx": 8, "pady": 4}
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=1)

        # 顶部：状态 + 玩家（左右两栏）
        top = ttk.Frame(outer)
        top.pack(fill=tk.X)
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)

        self.status_var = tk.StringVar(value="连接：初始化中…")
        self.player_var = tk.StringVar(value="玩家：等待数据…")
        ttk.Label(top, textvariable=self.status_var, justify=tk.LEFT,
                  font=("Microsoft YaHei UI", 9), foreground="#444").grid(row=0, column=0, sticky="w", **pad)
        ttk.Label(top, textvariable=self.player_var, justify=tk.LEFT,
                  font=("Microsoft YaHei UI", 9), foreground="#444").grid(row=0, column=1, sticky="w", **pad)

        # 工作流
        plan_frame = ttk.LabelFrame(outer, text="当前工作流", padding=6)
        plan_frame.pack(fill=tk.X)
        self.plan_var = tk.StringVar(value="（空闲）")
        ttk.Label(plan_frame, textvariable=self.plan_var, justify=tk.LEFT,
                  font=("Microsoft YaHei UI", 9), wraplength=700).pack(fill=tk.X)

        # 日志
        log_frame = ttk.LabelFrame(outer, text="日志", padding=6)
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=14, state=tk.DISABLED,
                                                  font=("Consolas", 9), wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 会话输入
        chat = ttk.Frame(outer)
        chat.pack(fill=tk.X, pady=(6, 0))
        self.input_var = tk.StringVar()
        entry = ttk.Entry(chat, textvariable=self.input_var, font=("Microsoft YaHei UI", 10))
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        entry.bind("<Return>", self._send)
        ttk.Button(chat, text="发送给 AI", command=self._send).pack(side=tk.RIGHT)
        ttk.Label(outer, text="提示：在输入框跟 AI 说话（等价于游戏内聊天）；AI 的回复会出现在日志和游戏里",
                  foreground="#888", font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(4, 0))

    def _attach_log_handler(self):
        handler = UILogHandler(self.log_q)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
        logging.getLogger().addHandler(handler)
        self._log_handler = handler

    # ── 刷新（主线程轮询）────────────────────────────────────────
    def _refresh(self):
        try:
            st = self.ui.get("status") or {}
            conn = st.get("conn", "初始化中")
            parts = [f"连接：{conn}"]
            if st.get("model"):
                parts.append(f"模型：{st['model']}")
            if st.get("llm"):
                parts.append(f"LLM：{st['llm']}")
            self.status_var.set("　".join(parts))

            p = self.ui.get("player")
            if p:
                pos = p.get("position") or {}
                inv = p.get("inventory") or {}
                hand = inv.get("held") or {}
                self.player_var.set(
                    f"位置：({pos.get('x', '?')}, {pos.get('y', '?')}, {pos.get('z', '?')})　"
                    f"血量：{p.get('health', '?')}/20　饥饿：{p.get('food', '?')}/20　"
                    f"生物群系：{(p.get('biome') or '?').split(':')[-1]}　"
                    f"手持：{hand.get('id', '无')}")
            else:
                self.player_var.set("玩家：等待数据…")

            plan = self.ui.get("plan")
            self.plan_var.set(plan if plan else "（空闲）")

            while True:
                try:
                    line = self.log_q.get_nowait()
                except queue.Empty:
                    break
                self.log_text.configure(state=tk.NORMAL)
                self.log_text.insert(tk.END, line + "\n")
                self.log_text.configure(state=tk.DISABLED)
            self.log_text.see(tk.END)
        except Exception as e:
            # UI 刷新异常不致命：记入日志面板兜底
            try:
                self.log_text.configure(state=tk.NORMAL)
                self.log_text.insert(tk.END, f"UI 刷新异常: {e}\n")
                self.log_text.configure(state=tk.DISABLED)
            except Exception:
                pass
        self.root.after(400, self._refresh)

    # ── 会话发送 ────────────────────────────────────────────────
    def _send(self, event=None):
        text = self.input_var.get().strip()
        if not text:
            return
        self.input_var.set("")
        self.brain.submit_chat(text)

    def on_close(self):
        try:
            logging.getLogger().removeHandler(self._log_handler)
        except Exception:
            pass
        self.root.destroy()
        os._exit(0)  # 强杀后台大脑线程与连接


def run_ui(brain) -> None:
    """启动界面（主线程）。大脑应已另起线程运行。"""
    root = tk.Tk()
    app = BrainUI(root, brain)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
