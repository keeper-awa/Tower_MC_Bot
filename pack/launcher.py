#!/usr/bin/env python3
"""大脑 exe 入口：等价 `python -m keeper.cli run-daemon --gui`（双击即开）。

PyInstaller 打包入口（pack/TowerAI.spec）。frozen 后 sys.argv 是 exe 路径，
显式注入 run-daemon --gui 子命令。异常写 exe 旁 debug.log（windowed 无 console，
排查问题用）。
"""
import os
import sys
import traceback
from pathlib import Path

# PyInstaller --noconsole 下 sys.stdout/stderr 为 None → uvicorn 日志配置
# （sys.stderr.isatty()）崩溃。给 None 的流一个 devnull 文件对象（有 isatty）。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from keeper.cli import main


def _write_debug(text: str) -> None:
    try:
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        (base / "debug.log").write_text(text, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    # 支持 `TowerAI.exe --no-gui`（仅服务）/ `--auto-connect`（自动连 mod）
    try:
        args = ["run-daemon"]
        argv = sys.argv[1:]
        if "--no-gui" not in argv:
            args.append("--gui")
        if "--auto-connect" in argv:
            args.append("--auto-connect")
        sys.exit(main(args))
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        _write_debug(traceback.format_exc())
        raise

