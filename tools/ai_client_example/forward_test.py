#!/usr/bin/env python3
"""Tower M1.3 转发链路测试：经 Tower 执行前置动作 + 错误透传 + 事件中继。

前置条件：游戏运行中且已进世界。
注意：大脑断开会触发协议 §2.4 断线清理（归零+断开前置链路），重连后链路
自动恢复——本脚本连接后自动等待 prereq=connected 再开始。

用法：python forward_test.py [token] [--port P] [--game-dir D]
"""

import argparse
import json
import sys
import time
from pathlib import Path

from tower_client import TowerClient, connect_until_ready
from _game_dir import default_game_dir


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Tower M1.3 转发链路测试")
    parser.add_argument("token", nargs="?", default=None, help="连接 token（缺省读 config/tower.json）")
    parser.add_argument("--port", type=int, default=24778)
    parser.add_argument("--game-dir", default=None, help="游戏目录（缺省读 brain/config.yaml 绝对路径）")
    args = parser.parse_args()
    token = args.token
    if not token:
        cfg = (Path(args.game_dir) if args.game_dir else default_game_dir()) / "config" / "tower.json"
        token = json.loads(cfg.read_text(encoding="utf-8"))["token"]
    ok = True

    client = connect_until_ready(token, args.port)
    print(f"==> hello: {client.hello}")
    assert client.hello.get("protocol") == 1 and client.hello.get("mod") == "tower"
    print("[0] ✅ prereq=connected（前置转发链路就绪）")

    def check(name, resp, want_ok=True):
        nonlocal ok
        good = resp.get("ok") is want_ok
        print(f"[{name}] {'✅' if good else '❌'} {resp}")
        ok = ok and good

    # ── 1. 转发持续状态动作（前置执行，响应透传）──────────────────────
    check("1", client.req("move", {"forward": 1}))
    time.sleep(0.5)
    check("2", client.req("move", {}))              # 归零
    check("3", client.req("jump", {"value": False}))
    check("4", client.req("sneak", {"value": False}))
    check("5", client.req("look_at", {}))           # 解锁视角
    check("6", client.req("attack", {"mode": "release"}))
    check("7", client.req("set_push", {"pos": True}))  # 开 pos 推送（事件中继用）

    # ── 2. 错误透传：前置产生的错误码原样带回（不改写）──────────────
    resp = client.req("fly", {"value": True})
    if resp["ok"]:
        print("[8] 环境相关透传: fly -> ok")
        client.req("fly", {"value": False})
    else:
        err = resp["error"]
        print(f"[8] 环境相关透传: fly -> 错误码 {err['code']} {err['message']}")

    # ── 3. 事件中继：move 触发 pos 推送（节流 0.5s）→ 应到达大脑 ──────
    client.req("move", {"forward": 1})
    time.sleep(1.2)
    client.req("move", {})
    time.sleep(0.8)
    events = client.drain_events()
    names = [e.get("event") for e in events]
    if "pos" in names:
        print(f"[9] ✅ pos 事件中继成功（共 {len(names)} 条事件: {names}）")
    else:
        print(f"[9] ❌ 未收到 pos 事件（收到: {names}）")
        ok = False

    # ── 4. chat 转发（限速 800ms 由前置控制）─────────────────────────
    check("10", client.req("chat", {"message": "hello from tower forward test"}))

    # ── 5. 未知动作 101（Tower 注册表校验）───────────────────────────
    resp = client.req("fly_to_moon", {})
    code = resp.get("error", {}).get("code")
    good = code == 101
    print(f"[11] {'✅' if good else '❌'} 未知动作 101（实收 {code}）: {resp}")
    ok = ok and good

    client.close()
    print("=== 结果:", "全部通过 ✅" if ok else "存在失败 ❌", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
