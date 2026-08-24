#!/usr/bin/env python3
"""Tower M3 寻路测试：move_to waypoints/auto + 事件流 + 取消 + 304 + 参数校验。

前置条件：游戏运行中且已进世界（建议在开阔平地测试；狭窄处目标可能 304）。
用法：python m3_nav_test.py [token] [--game-dir D]
"""

import argparse
import json
import sys
import time
from pathlib import Path

from tower_client import TowerClient, connect_until_ready


def wait_event(client, names, timeout=90):
    """等待指定事件之一；返回 (event_name, data) 或 (None, None)。

    事件被 TowerClient.recv 缓存到队列，本函数轮询缓存 + 短超时 recv 结合，
    避免 recv 递归缓存导致事件永远"收不到"。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        for e in client.drain_events():
            if e.get("event") in names:
                return e["event"], e.get("data")
        try:
            msg = client.recv(timeout=min(2.0, max(0.1, deadline - time.time())))
            if msg.get("type") == "event" and msg.get("event") in names:
                return msg["event"], msg.get("data")
        except TimeoutError:
            pass
    return None, None


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Tower M3 寻路测试")
    parser.add_argument("token", nargs="?", default=None, help="连接 token（缺省读 config/tower.json）")
    parser.add_argument("--game-dir", default=r"D:\整合包\.minecraft\versions\1.20.1-NeoForge_47.1.106")
    args = parser.parse_args()
    token = args.token
    if not token:
        cfg = Path(args.game_dir) / "config" / "tower.json"
        token = json.loads(cfg.read_text(encoding="utf-8"))["token"]
    ok = True

    client = connect_until_ready(token)
    print(f"==> hello: {client.hello}")

    def pos():
        return client.ok("get_state")["player"]["position"]

    def check(name, resp, want_ok=True):
        nonlocal ok
        good = resp.get("ok") is want_ok
        print(f"[{name}] {'✅' if good else '❌'} {resp}")
        ok = ok and good
        return resp

    p = pos()
    print(f"==> 当前坐标 ({p['x']:.1f}, {p['y']:.1f}, {p['z']:.1f})")

    # ── 1. waypoints 模式：目标 +8x ──────────────────────────────────
    tx, tz = int(p["x"]) + 8, int(p["z"])
    r = check("1", client.req("move_to", {"x": tx, "y": int(p["y"]), "z": tz, "mode": "waypoints"}))
    events = client.drain_events()
    names = [e["event"] for e in events]
    wp = r.get("result", {}).get("waypoints", [])
    if r["ok"] and wp:
        print(f"    waypoints={r['result']['total']} 个，首个=({wp[0]['x']},{wp[0]['y']},{wp[0]['z']})")
    else:
        print(f"    ⚠️ 当前环境可能不可达（建议到开阔平地重试）")
    if "path_found" not in names:
        print(f"[1.1] ❌ 未收到 path_found 事件（收到: {names}）")
        ok = False
    else:
        print(f"[1.1] ✅ path_found 事件（含 waypoints）")

    # ── 2. auto 模式：目标 +10x，等 path_reached ─────────────────────
    p = pos()
    tx, tz = int(p["x"]) + 10, int(p["z"])
    r = check("2", client.req("move_to", {"x": tx, "y": int(p["y"]), "z": tz}))
    if not r["ok"]:
        print(f"    ❌ auto 启动失败（304=目标不可达，请到开阔处重试）")
        ok = False
    else:
        ev_name, ev_data = wait_event(client, ("path_reached", "path_failed"), timeout=90)
        if ev_name == "path_reached":
            print(f"[3] ✅ path_reached @ {ev_data}")
            reached = True
        elif ev_name == "path_failed":
            print(f"[3] ❌ path_failed: {ev_data}")
            reached = False
        else:
            print("[3] ❌ 超时未到达")
            reached = False
            ok = False
        if reached:
            pf = [e["data"] for e in client.drain_events() if e["event"] == "path_progress"]
            if pf:
                print(f"    ✅ path_progress 共 {len(pf)} 条（协议 §5.3 每 ~1s 一条）")
            p2 = pos()
            dist = ((p2["x"] - tx) ** 2 + (p2["z"] - tz) ** 2) ** 0.5
            print(f"    到达位置 ({p2['x']:.1f}, {p2['y']:.1f}, {p2['z']:.1f}) 距目标 {dist:.2f} 格")
            if dist < 3:
                print(f"[4] ✅ 自动驾驶到达目标（误差 {dist:.2f} 格 < 3）")
            else:
                print(f"[4] ❌ 未到目标（误差 {dist:.2f} 格）")
                ok = False
        client.req("move_to", {"cancel": True})

    # ── 3. cancel：启动后立即取消 ────────────────────────────────────
    p = pos()
    r = check("5", client.req("move_to", {"x": int(p["x"]) + 6, "y": int(p["y"]), "z": int(p["z"])}))
    time.sleep(0.8)
    r = check("6", client.req("move_to", {"cancel": True}))
    events = client.drain_events()
    names = [e["event"] for e in events]
    if "path_cancelled" in names:
        print(f"[7] ✅ path_cancelled 事件: {[e['data'] for e in events if e['event'] == 'path_cancelled']}")
    else:
        print(f"[7] ❌ 未收到 path_cancelled（收到: {names}）")
        ok = False
    time.sleep(0.5)

    # ── 4. 不可达目标（高空 y+64）→ 304 ──────────────────────────────
    p = pos()
    r = client.req("move_to", {"x": int(p["x"]), "y": int(p["y"]) + 64, "z": int(p["z"])})
    code = r.get("error", {}).get("code")
    if code == 304:
        print(f"[8] ✅ 不可达返回 304")
    else:
        print(f"[8] ❌ 期望 304 实收 {code}: {r}")
        ok = False

    # ── 5. 参数校验 ──────────────────────────────────────────────────
    cases = [
        ("缺 x", {"y": 64, "z": 0}, 102),
        ("precision 越界", {"x": 1, "y": 64, "z": 1, "precision": 0.1}, 103),
        ("mode 非法", {"x": 1, "y": 64, "z": 1, "mode": "fly"}, 103),
        ("x 非数字", {"x": "a", "y": 64, "z": 1}, 103),
    ]
    for i, (name, params, want) in enumerate(cases, 1):
        resp = client.req("move_to", params)
        code = resp.get("error", {}).get("code")
        good = code == want
        print(f"[9.{name}] {'✅' if good else '❌'} 期望 {want} 实收 {code}")
        ok = ok and good

    client.close()
    print("=== 结果:", "全部通过 ✅" if ok else "存在失败 ❌", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
