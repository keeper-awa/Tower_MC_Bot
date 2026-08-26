#!/usr/bin/env python3
"""等待玩家进入世界：连接 Tower，直到收到 player_ready 事件（说明已进世界）。

用法：python wait_in_world.py [--game-dir D] [--timeout N]
"""

import argparse
import json
import sys
import time
from pathlib import Path

from websockets.sync.client import connect
from _game_dir import default_game_dir


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="等待进世界")
    parser.add_argument("--game-dir", default=None, help="游戏目录（缺省读 brain/config.yaml 绝对路径）")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    cfg = (Path(args.game_dir) if args.game_dir else default_game_dir()) / "config" / "tower.json"
    token = json.loads(cfg.read_text(encoding="utf-8"))["token"]

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        try:
            with connect(f"ws://127.0.0.1:24778/?token={token}") as ws:
                hello = json.loads(ws.recv(timeout=10))
                if hello["prereq"] != "connected":
                    print(f"==> 前置链路未就绪（{hello.get('prereq')}），等 5s")
                    time.sleep(5)
                    continue
                ws.send('{"type":"ping"}')
                # 等待 player_ready（大脑连接时 Tower 会重放缓存的 player_ready）
                while True:
                    msg = json.loads(ws.recv(timeout=10))
                    if msg.get("type") == "event" and msg.get("event") == "player_ready":
                        print("==> 已进世界（player_ready 收到），可以验收")
                        return 0
                    # ping/pong 期间继续等
        except Exception as e:
            print(f"==> 连接失败: {e}，等 5s")
            time.sleep(5)
    print("==> 超时未进世界")
    return 1


if __name__ == "__main__":
    sys.exit(main())
