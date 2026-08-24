#!/usr/bin/env python3
"""Tower M5.2 技能实机测试：直连 Tower 逐技能执行（不走 LLM/不依赖 Brain）。

覆盖技能：mine_wood / craft_items / make_crafting_table / cross_water（水域环境依赖）。

前置条件：游戏运行中且已进世界（开阔平地 + 附近有树最佳）。
用法：python m5_skill_test.py [token] [--game-dir D]
"""

import argparse
import json
import sys
import time
from pathlib import Path

from tower_client import TowerClient, connect_until_ready

# 引入 brain 模块（测试直接驱动技能，不走 LLM）
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "brain"))

from executor import PlanExecutor, validate_plan  # noqa: E402
from skills import SkillManager  # noqa: E402
from tools import TOOL_DEFS, Toolset  # noqa: E402

TOOL_NAMES = [t["function"]["name"] for t in TOOL_DEFS]


def count_items(state, suffix=None, exact=None) -> int:
    inv = state.get("inventory", {}) or {}
    total = 0
    for entry in inv.get("slots", []) + inv.get("armor", []):
        iid = entry.get("id", "")
        if exact and iid == exact:
            total += entry.get("count", 1)
        elif suffix and iid.endswith(suffix):
            total += entry.get("count", 1)
    return total


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Tower M5.2 技能实机测试")
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

    skills = SkillManager(Path(__file__).parent.parent.parent / "brain" / "skills")
    tools = Toolset(client, {}, None)
    executor = PlanExecutor(client, tools, skills, {}, patrol_cb=None)
    ctx = executor._ctx

    # [0] 逻辑自检：技能注册 + plan 校验（不依赖游戏环境）
    print("\n==> [0] 逻辑自检")
    names = set(skills.names())
    want = {"mine_wood", "craft_items", "make_crafting_table", "cross_water", "wait"}
    good = want <= names
    print(f"[0.1 技能注册] {'✅' if good else '❌'} 注册: {sorted(names)}")
    ok = ok and good
    plan, problems = validate_plan({"goal": "t", "steps": [{"type": "skill", "name": "mine_wood"}]},
                                   skills.names(), TOOL_NAMES)
    good = plan is not None
    print(f"[0.2 plan 校验合法] {'✅' if good else '❌'} problems={problems}")
    ok = ok and good
    plan, problems = validate_plan({"goal": "t", "steps": [{"type": "skill", "name": "nope"}]},
                                   skills.names(), TOOL_NAMES)
    good = plan is None
    print(f"[0.3 plan 校验非法技能] {'✅' if good else '❌'} plan={plan} problems={problems}")
    ok = ok and good

    # [1] mine_wood：砍树
    print("\n==> [1] mine_wood 砍树")
    state = client.ok("get_state")
    before = count_items(state, suffix="_log") + count_items(state, suffix="_wood")
    r = skills.run("mine_wood", ctx, {})
    state = client.ok("get_state")
    after = count_items(state, suffix="_log") + count_items(state, suffix="_wood")
    good = r.startswith("完成") and after > before
    print(f"[1] {'✅' if good else '❌'} {r}（原木 {before} → {after}）")
    ok = ok and good

    # [2] craft_items：合成木板（配方按背包里的原木类型推导）
    print("\n==> [2] craft_items 合成木板")
    state = client.ok("get_state")
    log_id = next((e["id"] for e in state.get("inventory", {}).get("slots", [])
                   if e["id"].endswith("_log") and not e["id"].startswith("stripped_")), None)
    if log_id is None:
        print("[2] ⚠️ 背包无原木（跳过，人工确认）")
    else:
        planks = log_id.replace("_log", "_planks")
        before = count_items(state, exact=planks)
        r = skills.run("craft_items", ctx, {"recipe": planks, "item": planks})
        state = client.ok("get_state")
        after = count_items(state, exact=planks)
        good = r.startswith("完成") and after > before
        print(f"[2] {'✅' if good else '❌'} {r}（{planks} {before} → {after}）")
        ok = ok and good

    # [3] make_crafting_table：制作工作台
    print("\n==> [3] make_crafting_table 制作工作台")
    state = client.ok("get_state")
    before = count_items(state, exact="minecraft:crafting_table")
    r = skills.run("make_crafting_table", ctx, {})
    state = client.ok("get_state")
    after = count_items(state, exact="minecraft:crafting_table")
    good = r.startswith("完成") and after >= before
    print(f"[3] {'✅' if good else '❌'} {r}（工作台 {before} → {after}）")
    ok = ok and good

    # [4] cross_water：渡河（环境依赖，无水标记人工确认）
    print("\n==> [4] cross_water 渡河")
    blocks = client.ok("get_blocks", {"radius": 16, "max": 512})
    water = [b for b in blocks.get("blocks", []) if "water" in b.get("id", "")]
    if not water:
        print("[4] ⚠️ 附近无水域（跳过，人工确认——需玩家/玩家附近有河湖时再跑）")
    else:
        near = min(water, key=lambda b: abs(b["x"]) + abs(b["z"]))
        tx, tz = near["x"] + 8, near["z"] + 8  # 对岸方向
        r = skills.run("cross_water", ctx, {"x": tx, "y": near["y"], "z": tz})
        good = r.startswith("完成")
        print(f"[4] {'✅' if good else '❌'} {r}（目标 {tx},{near['y']},{tz}）")
        ok = ok and good

    # [5] wait：等待 2 秒
    print("\n==> [5] wait 等待")
    t0 = time.time()
    r = skills.run("wait", ctx, {"seconds": 2})
    elapsed = time.time() - t0
    good = r.startswith("完成") and 1.5 <= elapsed < 10
    print(f"[5] {'✅' if good else '❌'} {r}（实际 {elapsed:.1f}s）")
    ok = ok and good

    # 收尾：归零（释放技能可能残留的持续状态）
    executor.safe_stop()
    client.close()
    print(f"\n==> {'全部通过' if ok else '存在失败'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
