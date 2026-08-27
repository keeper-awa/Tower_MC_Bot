# 改造方案：Tower 大脑嵌入 KeeperMC 前后端

> 日期：2026-08-26 · 状态：已确认 · 依据：开发指南工作规范（文档优先）

## 1. 背景

- **KeeperMC**（`h:\KeeperMC\`）：当时以为 keyboard 是主体，围绕它做的**完整前后端**——FastAPI daemon（REST + WS 推送 + 托管 Vue webui）+ Agent 决策循环（单 LLM 每轮输出动作 → keyboard 执行）。
- **Tower**：真正的主体——Tower mod（感知/导航/视觉/背包）+ 大脑连 Tower mod（24778），LLM 排工作流，执行走**技能代码化**。

**结论**：KeeperMC 的 UI/前后端设计好，错在连到 keyboard。现在移植进 Tower 项目，**Tower 大脑嵌入 agent 框架，单一进程**，webui 显示 Tower 语义面板。

## 2. 目标架构

```
┌──────────────────────────────────────────────────────────────┐
│ Tower 单进程（h:\Tower_MC_Bot\brain\ + keeper\）                │
│                                                              │
│  FastAPI daemon（KeeperMC 移植） + pywebview 内嵌窗口           │
│  │  REST /api/* + WS 推送 + 托管 webui/dist                   │
│  ├─ Manager：生命周期/状态映射/控制桥                           │
│  │   └─ 持有 Tower Brain（嵌入，替代 Keeper AgentLoop）          │
│  │       ├─ 连 Tower mod（24778）                              │
│  │       ├─ LLM 排 plan/outline + 技能代码化执行               │
│  │       └─ ui 状态（status/player/plan）→ 映射给前端           │
│  └─ Vue webui（LingChat 玻璃，整套搬）：Tower 语义面板          │
└──────────────────────────────────────────────────────────────┘
                          │ WS（Tower 协议 v1, 24778）
                          ▼
                   Tower mod（感知/导航/视觉）
                          │ 转发（24777）
                          ▼
              Keyboard mod（执行层，不改）
```

## 3. 分阶段实施

### Phase 1 · 移植框架跑通
1. 复制 `KeeperMC/keeper/{daemon,webui,config.py,cli.py}` → `h:\Tower_MC_Bot\keeper\`
2. `Manager` 改为持有 `Brain` 实例（替换 KeyboardClient + AgentLoop），`Brain.run()` 后台线程
3. daemon 连 Tower mod（24778），读 `brain/config.yaml` 的 game_dir/tower.json token
4. 跑通：daemon 启动 → Brain 连 Tower → webui 显示 Tower 连接状态

### Phase 2 · 数据桥接
5. `status()` 从 `brain.ui` 映射（connected/goal/player_name/token_configured）
6. `command_log/decisions` 从 brain 日志队列构建
7. 控制桥：send_chat→submit_chat；stop/pause/resume/goal/connect/disconnect **Manager 层封装**（不改 brain.py 主循环；暂停=停计划+记录断点）

### Phase 3 · webui 语义适配
8. WorkflowPanel 接真实 plan/outline 进度
9. StatusPanel 接 Tower inventory/xp/effects
10. 事件面板对接工作流步骤 + 寻路事件

### Phase 4 · 模型管理 / 打包
11. 模型管理：**保留 settings.json 多模型 UI**，驱动 brain/llm.py（update_config 热切换）
12. exe 打包：PyInstaller，**pywebview 内嵌 webui 窗口**（双击即开）

## 4. 关键改造点

| 组件 | 现状（Keeper） | 改后（Tower） |
|---|---|---|
| `mc/client.py` | 连 keyboard 24777 | 换成 `brain/tower_client.py`（连 24778） |
| `agent/loop.py` | 单 LLM 每轮动作 | 换成 `brain/brain.py`（Brain 嵌入） |
| `llm/provider.py` | 文本 JSON 输出 | 复用 `brain/llm.py`（text+tool_calls+vision） |
| `daemon/manager.py` | 管 client+agent | 管 Brain + 状态映射 |
| 前端 WorkflowPanel | 「生效 Skill」占位 | 真实 plan 进度 |
| 前端 StatusPanel | 背包「待支持」 | Tower inventory 真实数据 |

## 5. 已确认决策（2026-08-26）

| # | 决策 | 选择 |
|---|---|---|
| 1 | 连接目标 | Tower mod（24778） |
| 2 | 控制接口 | **Manager 层封装**（不改 brain.py 主循环） |
| 3 | 模型管理 | **保留 settings.json 多模型**（驱动 brain/llm.py 热切换） |
| 4 | webui 访问 | **pywebview 内嵌窗口**（exe 双击即开） |
| 5 | webui 范围 | **整套搬**（模型管理/壁纸/日志导出/设置全保留） |

## 6. 风险

- 大改造，按 Phase 逐阶段验收后再进下一步
- 暂停/恢复：Keeper AgentLoop 可暂停（asyncio.Event），Tower Brain 是阻塞主循环——暂停=停 plan + 断点恢复（outline 已有断点机制）
- 模型管理 settings.json 与 config.yaml 双轨维护，需统一（settings.json 为唯一 LLM 配置源，config.yaml 只留 vision/路径）
