#!/usr/bin/env python3
"""实机验收 craft_chain 合成链技能：连真实 Tower，直接运行技能（不经 LLM）。

用法：python verify_craft_chain.py [target] [count]
  缺省 target=crafting_table（工作台：验证 2x2 背包合成 + 缺木板自动砍树）

前提：游戏已进世界（Tower 24778 连接可用）。
流程：连 Tower → 建 Toolset/PlanExecutor（构造 SkillContext）→ skills.run("craft_chain")
      → 打印技能汇报 + 关键步骤日志。
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # h:\Tower_MC_Bot
sys.path.insert(0, str(ROOT / "brain"))
sys.path.insert(0, str(ROOT / "brain" / "skills"))

from skills import SkillManager
from skills._base import SkillContext
from tower_client import TowerClient
from tools import Toolset
from executor import PlanExecutor
from memory import MemoryManager

log = logging.getLogger("verify")


def load_token() -> str:
    """读 brain/config.yaml 的 game_dir → config/tower.json 的 token。"""
    import yaml
    cfg_path = ROOT / "brain" / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    game_dir = Path(data["connection"]["game_dir"])
    return json.loads((game_dir / "config" / "tower.json").read_text(encoding="utf-8"))["token"]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="实机验收 craft_chain 技能")
    parser.add_argument("target", nargs="?", default="crafting_table", help="合成目标（缺省 crafting_table）")
    parser.add_argument("count", nargs="?", default="1", help="数量（缺省 1）")
    args = parser.parse_args()

    token = load_token()
    log.info("连接 Tower 24778 ...")
    # 前置链路需要时间重建（每次连接断开触发归零+断开前置）：带重试
    client = None
    for attempt in range(5):
        try:
            c = TowerClient(token, host="127.0.0.1", port=24778)
            if c.hello.get("prereq") == "connected":
                client = c
                break
            log.info("前置链路未就绪（prereq=%s），%ds 后重连", c.hello.get("prereq"), 3)
            c.close()
        except Exception as e:
            log.info("连接失败: %s，重试", e)
        time.sleep(3)
    if client is None:
        print("FAIL: 无法连接 Tower（前置链路未就绪）")
        return 1
    log.info("已连接: protocol=%s prereq=%s", client.hello.get("protocol"), client.hello.get("prereq"))

    cfg = {"default_wait_timeout": 60, "safety_check_interval": 5, "safety_patrol_interval": 30}
    memory = MemoryManager(ROOT / "brain")
    tools = Toolset(client, cfg, memory)
    skills = SkillManager(ROOT / "brain" / "skills")
    print(f"技能库: {skills.names()}")

    # 连个假 executor（提供 wait_event/checkpoint，真跑会等事件超时——craft_chain 需要）
    # 直接用 PlanExecutor（缺 llm 不影响 craft_chain：不依赖 look）
    ex = PlanExecutor(client, tools, skills, cfg, llm=None)
    ctx = ex._ctx

    print(f"\n===== 执行技能: craft_chain {{target: {args.target}, count: {args.count}}} =====")
    t0 = time.time()
    result = skills.run("craft_chain", ctx, {"target": args.target, "count": int(args.count)})
    el = time.time() - t0
    print(f"\n[技能返回] ({el:.1f}s)\n{result}")

    # 执行记录
    if ex.results:
        print("\n[执行记录]")
        for step, r in ex.results:
            print(f"  {step['name']}: {r}")

    client.close()
    ok = result.startswith("完成")
    print(f"\n===== {'✅ 验收通过' if ok else '❌ 验收失败'} =====")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
