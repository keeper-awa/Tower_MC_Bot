"""KeeperMC 后方 AI 管控后端。

分层：
- keeper.mc      Mod WebSocket 协议连接层（控制玩家）
- keeper.llm     大模型适配层（决策）
- keeper.agent   决策循环（观测 -> LLM -> 动作）
- keeper.launcher 游戏启动器
- keeper.daemon  常驻服务 + 管理接口
- keeper.gui     WebView2 桌面壳
"""

__version__ = "0.1.0"
