#!/usr/bin/env python3
"""Tower M1.3 断线归零测试（协议 §2.4）：大脑断线 → 玩家停止移动。

原理：大脑（Tower 客户端）按下前进键 1s → 玩家开始移动 → 大脑直接断开
（模拟异常掉线，不发送 close 帧）→ Tower 检测断线 → 主动归零序列 + 断开前置
（双保险）→ 玩家应停止移动。
验证：直接连接前置（keyboard.json token）查 get_state 两次（间隔 1s），
位置应不再变化（未移动 = 归零生效）。

注意：直连前置会收到 player_ready/pos 等事件，需事件感知接收。

用法：python zeroing_test.py [--game-dir D]（需要已进世界、前方有可走空间）
"""

import argparse
import json
import sys
import time
from pathlib import Path

from websockets.sync.client import connect

from tower_client import TowerClient
from _game_dir import default_game_dir


def recv_response(ws, timeout=10):
    """接收响应（跳过事件/pong），返回消息。"""
    while True:
        msg = json.loads(ws.recv(timeout=timeout))
        if msg.get("type") == "event" or msg.get("type") == "pong":
            continue
        return msg


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Tower M1.3 断线归零测试")
    parser.add_argument("--game-dir", default=None, help="游戏目录（缺省读 brain/config.yaml 绝对路径）")
    args = parser.parse_args()
    cfg = (Path(args.game_dir) if args.game_dir else default_game_dir()) / "config"
    tower_cfg = json.loads((cfg / "tower.json").read_text(encoding="utf-8"))
    keyboard_cfg = json.loads((cfg / "keyboard.json").read_text(encoding="utf-8"))
    ok = True

    # ── 1. 大脑连接 Tower，等待链路就绪，按下前进 1s ──────────────────
    brain = None
    for i in range(30):
        try:
            brain = TowerClient(tower_cfg["token"])
            if brain.hello.get("prereq") == "connected":
                break
            print(f"==> 前置链路未就绪，1s 后重连（{i + 1}/30）")
            brain.close()
            brain = None
        except Exception as e:
            print(f"==> 连接失败: {e}，1s 后重试（{i + 1}/30）")
        time.sleep(1)
    if brain is None:
        raise SystemExit("前置链路等待超时")
    print("[1] ✅ 大脑已连接，前置链路就绪")

    resp = brain.req("move", {"forward": 1})
    assert resp["ok"], f"move 失败: {resp}"
    print("[1] ✅ 大脑已按下前进键（经 Tower 转发）")
    time.sleep(1.0)
    # 断开大脑连接（close 帧与异常掉线走同一条 channelInactive → 归零路径）
    brain.close()
    print("[2] ✅ 大脑已断开")

    # ── 2. 等归零执行完成（Tower 归零序列 → 断开前置 → 前置自身归零）──
    time.sleep(1.5)

    # ── 3. 直连前置验证玩家已停止移动 ────────────────────────────────
    with connect(f"ws://127.0.0.1:24777/?token={keyboard_cfg['token']}") as kb:
        assert recv_response(kb)["ok"], "前置 hello 异常"

        def pos():
            kb.send(json.dumps({"type": "request", "id": 100, "action": "get_state", "params": {}}))
            resp = recv_response(kb)
            p = resp["result"]["player"]["position"]
            return p["x"], p["z"]

        x1, z1 = pos()
        time.sleep(1.0)
        x2, z2 = pos()
        dist = ((x2 - x1) ** 2 + (z2 - z1) ** 2) ** 0.5
        print(f"[3] 位置对比: ({x1:.2f}, {z1:.2f}) → ({x2:.2f}, {z2:.2f})  位移 {dist:.3f} 格")
        if dist < 0.05:
            print("[3] ✅ 玩家已停止（断线归零生效）")
        else:
            print("[3] ❌ 玩家仍在移动（归零未生效？）")
            ok = False

    print("=== 结果:", "全部通过 ✅" if ok else "存在失败 ❌", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
