"""端到端冒烟测试：真实 LLM（DeepSeek）+ 模拟 mod 服务。

无需启动游戏即可验证整条管道：观测 → LLM → 解析 → 动作 → 回执。
用法：python -m keeper.agent.mock_demo
"""
from __future__ import annotations

import asyncio

from keeper.agent.loop import AgentLoop
from keeper.config import load_config
from keeper.llm.provider import LLMProvider
from keeper.mc.client import KeyboardClient

from tests.mock_mod import MockMod

GOAL = "在当前位置观察环境，然后尝试向前走几步"


async def main() -> int:
    cfg = load_config()
    provider = LLMProvider(cfg.llm)
    print("LLM 配置:", provider.describe())
    if not provider.ready:
        print("错误：LLM 未配置")
        return 2

    mock = MockMod(token="demo-token")
    await mock.start()
    client = KeyboardClient(cfg.mod.model_copy(update={"port": mock.port, "token": "demo-token"}))
    loop = AgentLoop(client, provider, cfg.agent, goal=GOAL)

    await client.start()
    if not await client.wait_connected(timeout=5):
        print("模拟 mod 未就绪")
        return 1

    print(f"已连接（protocol={client.protocol}），目标: {GOAL}\n")
    for i in range(4):
        print(f"===== 第 {i + 1} 轮 =====")
        record = await loop.run_once()
        print(f"思考: {record.think or ''}")
        if record.action:
            print(f"动作: {record.action} {record.params}")
        if record.result is not None:
            print(f"回执: {record.result}")
        if record.error:
            print(f"错误: {record.error}")
        await asyncio.sleep(0.3)
    print("\n冒烟测试结束")

    await client.close()
    await mock.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
