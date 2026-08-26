#!/usr/bin/env python3
"""Tower M2 感知层测试：get_state 扩展快照 / raycast / get_blocks / get_entities。

前置条件：游戏运行中且已进世界。
用法：python m2_perception_test.py [token] [--game-dir D]
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
    parser = argparse.ArgumentParser(description="Tower M2 感知层测试")
    parser.add_argument("token", nargs="?", default=None, help="连接 token（缺省读 config/tower.json）")
    parser.add_argument("--game-dir", default=None, help="游戏目录（缺省读 brain/config.yaml 绝对路径）")
    args = parser.parse_args()
    token = args.token
    if not token:
        cfg = (Path(args.game_dir) if args.game_dir else default_game_dir()) / "config" / "tower.json"
        token = json.loads(cfg.read_text(encoding="utf-8"))["token"]
    ok = True

    client = connect_until_ready(token)
    print(f"==> hello: {client.hello}")

    def check(name, resp, want_ok=True):
        nonlocal ok
        good = resp.get("ok") is want_ok
        print(f"[{name}] {'✅' if good else '❌'} {resp}")
        ok = ok and good
        return resp

    # ── 1. get_state 扩展快照（协议 §6）────────────────────────────────
    st = check("1", client.req("get_state", {}))["result"]
    required = ["protocol", "player.position.x", "player.position.y", "player.position.z",
                "player.rotation.yaw", "player.rotation.pitch", "player.health", "player.food",
                "player.dimension", "player.gamemode", "player.selected_slot",
                "inventory.slots", "inventory.armor", "xp.level", "xp.progress",
                "world.time_of_day", "world.biome"]
    missing = [p for p in required if _get_path(st, p) is None]
    if missing:
        print(f"[2] ❌ 快照缺少字段: {missing}")
        ok = False
    else:
        print(f"[2] ✅ 快照字段完整（{len(required)} 项）")
        print(f"    position=({st['player']['position']['x']:.1f}, {st['player']['position']['y']:.1f}, {st['player']['position']['z']:.1f}) "
              f"biome={st['world']['biome']} slots={len(st['inventory']['slots'])} 手持={st['inventory'].get('held', '空')}")
        # 槽位语义抽查：0-8 或 9-35 范围内
        for slot in st["inventory"]["slots"]:
            assert 0 <= slot["slot"] <= 35, f"槽位越界: {slot}"
        for slot in st["inventory"]["armor"]:
            assert 36 <= slot["slot"] <= 39, f"盔甲槽越界: {slot}"

    # ── 2. raycast（协议 §5.2）────────────────────────────────────────
    r = check("3", client.req("raycast", {}))["result"]["hit"]
    print(f"    raycast: type={r['type']} distance={r['distance']}")
    if r["type"] == "block":
        print(f"    命中: {r['block']['id']} @ ({r['block']['x']},{r['block']['y']},{r['block']['z']}) face={r['block']['face']}")
    elif r["type"] == "entity":
        print(f"    命中: {r['entity']['type']} {r['entity']['name']} hp={r['entity'].get('health')}")
    # 距离参数 + 校验
    r2 = check("4", client.req("raycast", {"distance": 4}))["result"]["hit"]
    assert r2["type"] in ("block", "entity", "none") and r2["distance"] <= 4
    for bad, msg in [({"distance": 1}, "distance<4"), ({"distance": 100}, "distance>64"), ({"distance": "x"}, "distance 非数字")]:
        resp = client.req("raycast", bad)
        code = resp.get("error", {}).get("code")
        good = code == 103
        print(f"[5.{msg}] {'✅' if good else '❌'} 参数校验 103（实收 {code}）")
        ok = ok and good

    # ── 3. get_blocks（协议 §5.2）────────────────────────────────────
    b = check("6", client.req("get_blocks", {}))["result"]
    s = b["summary"]
    print(f"    共 {len(b['blocks'])} 个方块 truncated={b['truncated']} 脚下={s['underfoot']['id']} 面前={s['front']['id']} 头上={s['head']['id']}")
    assert all(k in s for k in ("underfoot", "front", "head"))
    b2 = check("7", client.req("get_blocks", {"radius": 1, "max": 3}))["result"]
    assert len(b2["blocks"]) <= 3
    for bad, msg in [({"radius": 0}, "radius<1"), ({"radius": 20}, "radius>16"), ({"max": 0}, "max<1")]:
        resp = client.req("get_blocks", bad)
        code = resp.get("error", {}).get("code")
        good = code == 103
        print(f"[8.{msg}] {'✅' if good else '❌'} 参数校验 103（实收 {code}）")
        ok = ok and good

    # ── 4. get_entities（协议 §5.2）──────────────────────────────────
    e = check("9", client.req("get_entities", {}))["result"]
    print(f"    共 {e['count']} 个实体: {[x['type'] for x in e['entities']][:8]}")
    for ent in e["entities"]:
        assert ent["category"] in ("monster", "creature", "ambient", "water", "item", "player", "other")
        assert ent["hostile"] == (ent["category"] == "monster")
    # 类型过滤
    e2 = check("10", client.req("get_entities", {"type": "minecraft:player", "radius": 8}))["result"]
    for ent in e2["entities"]:
        assert ent["type"] == "minecraft:player"
    for bad, msg in [({"radius": 0}, "radius<1"), ({"radius": 64}, "radius>32"), ({"type": 5}, "type 非字符串")]:
        resp = client.req("get_entities", bad)
        code = resp.get("error", {}).get("code")
        good = code == 103
        print(f"[11.{msg}] {'✅' if good else '❌'} 参数校验 103（实收 {code}）")
        ok = ok and good

    client.close()
    print("=== 结果:", "全部通过 ✅" if ok else "存在失败 ❌", "===")
    return 0 if ok else 1


def _get_path(d, path):
    cur = d
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


if __name__ == "__main__":
    sys.exit(main())
