#!/usr/bin/env python3
"""Tower M1.2 冒烟测试：连接 / 鉴权 / hello（含 prereq）/ ping-pong / 单连接 / 空闲断开。

用法（游戏运行中）：
    python ws_smoke_test.py [token] [port]
    python ws_smoke_test.py --test-idle   # 追加 60s 空闲断开用例（默认跳过，耗时）

默认 token 从 config/tower.json 自动读取（路径可用 --game-dir 指定）。
"""

import argparse
import json
import sys
import time
from pathlib import Path

from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect


def default_token(game_dir: Path) -> str:
    cfg = game_dir / "config" / "tower.json"
    if cfg.exists():
        return json.loads(cfg.read_text(encoding="utf-8"))["token"]
    raise SystemExit(f"未找到 {cfg}，请传入 token 参数")


def main() -> int:
    # Windows 控制台默认 GBK，无法打印 emoji；强制 UTF-8 输出
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Tower M1.2 冒烟测试")
    parser.add_argument("token", nargs="?", default=None, help="连接 token（缺省自动从游戏 config 读取）")
    parser.add_argument("--port", type=int, default=24778)
    parser.add_argument("--game-dir", default=r"D:\整合包\.minecraft\versions\1.20.1-NeoForge_47.1.106",
                        help="游戏目录（读取 config/tower.json）")
    parser.add_argument("--test-idle", action="store_true",
                        help="追加 60s 空闲断开用例（需等待 ~70s）")
    args = parser.parse_args()
    token = args.token or default_token(Path(args.game_dir))
    uri = f"ws://127.0.0.1:{args.port}/?token={token}"
    print(f"==> 连接 {uri}")
    ok = True

    # ── 1. 正确 token：握手 + hello 宣告（协议 §2.1，含 prereq）────────────
    with connect(uri) as ws:
        hello = json.loads(ws.recv(timeout=10))
        assert hello["type"] == "hello" and hello["ok"], f"hello 异常: {hello}"
        assert hello.get("protocol") == 1, f"protocol 应为 1: {hello}"
        assert hello.get("mod") == "tower", f"mod 应为 tower: {hello}"
        assert "prereq" in hello and hello["prereq"] in ("connected", "disconnected"), \
            f"prereq 缺失或非法: {hello}"
        print(f"[1] ✅ hello: {hello}")

        # ── 2. ping -> pong ────────────────────────────────────────────
        ws.send('{"type":"ping"}')
        pong = json.loads(ws.recv(timeout=10))
        assert pong == {"type": "pong"}, f"pong 异常: {pong}"
        print("[2] ✅ ping -> pong")

        # ── 3. 单连接：第二个连接挤掉第一个（close 4001）────────────────
        with connect(uri) as ws2:
            hello2 = json.loads(ws2.recv(timeout=10))
            assert hello2["ok"], f"第二连接 hello 异常: {hello2}"
            try:
                ws.recv(timeout=10)
                print("[3] ❌ 旧连接应被关闭，实际仍收到消息")
                ok = False
            except ConnectionClosed as e:
                code = e.rcvd.code if e.rcvd else e.code
                print(f"[3] ✅ 旧连接被挤掉 (close={code})")
            ws2.send('{"type":"ping"}')
            assert json.loads(ws2.recv(timeout=10)) == {"type": "pong"}
            print("[4] ✅ 新连接正常 ping -> pong")

    # ── 4. 错误 token：hello{ok:false} 后断开 ──────────────────────────
    bad_uri = uri.replace(token, "wrong-token")
    try:
        with connect(bad_uri) as ws:
            hello = json.loads(ws.recv(timeout=10))
            assert hello["ok"] is False and hello["error"] == "auth_failed", f"失败 hello 异常: {hello}"
            print(f"[5] ✅ 错误 token 收到: {hello}")
            try:
                ws.recv(timeout=10)
                print("[5] ⚠️ 预期断开未发生（稍后由空闲超时断开）")
            except ConnectionClosed as e:
                code = e.rcvd.code if e.rcvd else e.code
                print(f"[5] ✅ 鉴权失败连接随后被断开 (close={code})")
    except InvalidStatus as e:
        print(f"[5] ❌ 握手被 HTTP 拒绝: {e.response.status_code}")
        ok = False

    # ── 5. 空闲断开（协议 §2.4：60s 无消息，可选，等待 ~75s）─────────────
    if args.test_idle:
        t0 = time.time()
        with connect(uri) as ws:
            assert json.loads(ws.recv(timeout=10))["ok"], "hello 异常"
            try:
                ws.recv(timeout=90)
                print("[6] ❌ 60s 空闲后应被服务端断开，实际仍收到消息")
                ok = False
            except ConnectionClosed:
                elapsed = time.time() - t0
                ok_good = 55 <= elapsed <= 80
                print(f"[6] {'✅' if ok_good else '⚠️'} 空闲连接被断开 (elapsed={elapsed:.0f}s)")
                ok = ok and ok_good
    else:
        print("[6] ⏭️ 空闲断开用例已跳过（加 --test-idle 运行，耗时 ~75s）")

    print("=== 结果:", "全部通过 ✅" if ok else "存在失败 ❌", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
