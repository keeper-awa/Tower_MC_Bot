"""KeeperMC CLI 入口。

子命令：
- `run-agent --goal "目标"`  连接 mod + LLM，启动 Agent 决策循环
- `run-daemon [--gui]`       启动常驻服务 + 管理接口（可选 WebView2 桌面壳）
- `status`                   检查 mod 服务与 LLM 配置状态
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import logging

logger = logging.getLogger(__name__)


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _print_decision(record) -> None:
    """把一轮决策打印到终端（后续面板会用结构化数据）。"""
    print(f"[决策] {record.think or ''}")
    if record.action:
        print(f"  → {record.action} {record.params}")
    if record.result is not None:
        print(f"  ← {record.result}")
    if record.error:
        print(f"  ✗ {record.error}")


async def _cmd_run_agent(args: argparse.Namespace) -> int:
    from .agent.loop import AgentLoop
    from .config import load_config
    from .launcher.mc_launch import GameLauncher
    from .llm.provider import LLMProvider
    from .mc.client import KeyboardClient

    cfg = load_config()
    launcher = GameLauncher(cfg.mod, cfg.launcher)
    token = launcher.resolve_token()
    port = launcher.resolve_port()
    client = KeyboardClient(cfg.mod.model_copy(update={"token": token, "port": port}))
    provider = LLMProvider(cfg.llm)
    loop = AgentLoop(client, provider, cfg.agent, goal=args.goal, on_decision=_print_decision)

    print(f"LLM 配置: {provider.describe()}")
    if not provider.ready:
        print("错误：请先在 .env 填写 LLM_BASE_URL / LLM_MODEL（可加 LLM_API_KEY）")
        return 2
    if not token:
        print("错误：未配置 token（.env 设 KEEPER_MOD_TOKEN 或 KEEPER_MOD_CONFIG）")
        return 2

    await client.start()
    ok = await client.wait_connected(timeout=args.timeout)
    if not ok:
        print(f"未能连接到 mod（{cfg.mod.host}:{port}）。请先启动游戏。")
        await client.close()
        return 1
    print(f"已连接 mod（protocol={client.protocol}），目标={loop.goal or '(空，观察模式)'}")

    if args.max_cycles and args.max_cycles > 0:
        for _ in range(args.max_cycles):
            if loop.safety.stopped:
                break
            await loop.run_once()
            await asyncio.sleep(cfg.agent.loop_interval_s)
    else:
        task = asyncio.create_task(loop.run())
        try:
            await task
        except asyncio.CancelledError:
            loop.stop()

    await client.close()
    return 0


def _cmd_run_daemon(args: argparse.Namespace) -> int:
    """启动常驻 daemon + 管理接口（Tower 大脑嵌入）；`--gui` 打开浏览器（Phase 4 改 pywebview）。"""
    import os

    from .daemon.app import create_app
    from .daemon.tower_manager import TowerManager

    host = os.environ.get("TOWER_DAEMON_HOST", "127.0.0.1")
    port = int(os.environ.get("TOWER_DAEMON_PORT", "8090"))
    manager = TowerManager()
    app = create_app(manager)

    async def _serve(auto_connect: bool) -> None:
        import uvicorn

        if auto_connect:
            asyncio.create_task(manager.connect(launch=False, timeout=60))
        server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
        await server.serve()

    def _backend() -> None:
        asyncio.run(_serve(args.auto_connect))

    print(f"管理接口: http://{host}:{port}/  (Ctrl+C 退出)")
    if args.gui:
        import threading
        import time

        threading.Thread(target=_backend, daemon=True).start()
        time.sleep(1.5)  # 等服务器就绪
        import webview

        webview.create_window(
            "Tower AI 大脑",
            f"http://{host}:{port}/",
            width=1024,
            height=720,
            min_size=(820, 560),
        )
        webview.start()
    else:
        _backend()
    return 0


async def _cmd_status(args: argparse.Namespace) -> int:
    from .config import load_config
    from .launcher.mc_launch import GameLauncher, probe_port
    from .llm.provider import LLMProvider

    cfg = load_config()
    launcher = GameLauncher(cfg.mod, cfg.launcher)
    provider = LLMProvider(cfg.llm)
    token = launcher.resolve_token()
    port = launcher.resolve_port()
    up = await probe_port(cfg.mod.host, port)
    print(f"mod 服务: {'在线' if up else '离线'} ({cfg.mod.host}:{port})")
    print(f"LLM: {provider.describe()}")
    print(f"token 已配置: {'是' if token else '否（.env 设 KEEPER_MOD_TOKEN 或 KEEPER_MOD_CONFIG）'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="keeper", description="KeeperMC 后方 AI 管控")
    parser.add_argument("--log-level", default="INFO", help="日志级别（DEBUG/INFO/WARNING）")
    sub = parser.add_subparsers(dest="command", required=True)

    run_agent = sub.add_parser("run-agent", help="启动 Agent 决策循环")
    run_agent.add_argument("--goal", default="", help="自然语言目标（可空=观察模式）")
    run_agent.add_argument("--timeout", type=float, default=15.0, help="等待 mod 连接超时（秒）")
    run_agent.add_argument("--max-cycles", type=int, default=0, help="最大决策轮数（0=不限）")
    run_agent.set_defaults(func=_cmd_run_agent)

    status = sub.add_parser("status", help="检查 mod 与 LLM 状态")
    status.set_defaults(func=_cmd_status)

    run_daemon = sub.add_parser("run-daemon", help="启动常驻 daemon + 管理接口")
    run_daemon.add_argument("--gui", action="store_true", help="打开 WebView2 桌面壳")
    run_daemon.add_argument("--auto-connect", action="store_true", help="启动后自动连接 mod（含尝试启动游戏）")
    run_daemon.set_defaults(func=_cmd_run_daemon)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.log_level)
    result = args.func(args)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
