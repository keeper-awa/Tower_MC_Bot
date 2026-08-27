# Tower 控制台：KeeperMC 前后端完整移植（大脑嵌入 daemon 单进程）

## 概述

将 KeeperMC 的完整前后端（FastAPI daemon + Vue3 玻璃拟态 webui + Agent 决策循环）移植进 Tower 项目，形成 **Tower 控制台**：大脑（Brain）以单进程嵌入 daemon，由 daemon 托管 webui 并通过 WebSocket 实时推送状态，实现「游戏管理 → AI 决策 → 玩家状态可视化」的完整闭环。

> 前端交互 → daemon（REST/WS）→ 大脑 Brain（单进程线程）→ Tower mod（ws 24778，token 鉴权）。

---

## 改动内容

### 1. KeeperMC 前后端移植（`keeper/` 全新增）

- **daemon**（`keeper/daemon/`）：FastAPI 托管 webui 静态资源 + REST/WS 实时推送
  - `tower_manager.py`：`TowerManager` 嵌入 `Brain`（后台线程跑 `run`），`_ClientProxy` 只读视图、`_BrainLogHandler` 大脑日志→前端日志面板
  - `app.py`：REST（status/connect/disconnect/state/agent 控制/chat）+ WS 广播 + 托管 `webui/dist`
  - `settings.py` / `mc_icons.py`：settings.json 模型管理持久化 / MC 图标运行时提取
- **webui**（`keeper/webui/`，Vue3 + Vite + TS 玻璃拟态）：玩家状态、工作流面板、日志、目标/聊天输入、设置、模型管理
- **cli**（`keeper/cli.py`）：`run-daemon [--gui]`（pywebview 桌面壳）/ `run-agent` / `status`

### 2. 大脑嵌入 daemon（单进程）

- `TowerManager._ensure_brain()`：创建 `Brain(config.yaml)` 起后台线程，`status()`/`state()` 从 `brain.ui` 实时映射
- Agent 控制（start/stop/pause/resume）在 Manager 层封装，不改 `brain.py` 主循环
- 玩家实时数据：`/api/state` 用 `asyncio.to_thread(client.ok, "get_state")` 绕 5s 缓存

### 3. 模型管理（对话 / 视觉独立角色）

- `settings.py`：`LLMModel` + `active_model_id`（对话）+ `vision_model_id`（视觉，空=跟随对话）
- `brain/llm.py`：`update_config()` 支持独立视觉模型（不同 base_url/api_key/model + 独立 `vision_client`）、`clear_vision` 跟随对话
- webui「管理模型」：模型列表 + 对话/视觉角色分配（参照 LingChat 的 chat/vision 槽位），热切换无需重启

### 4. MC 图标系统

- `mc_icons.py`：从游戏 jar 运行时提取物品（582）/ 方块（928）图标 → `keeper/mc-icons/`
- HUD 图标（心/鸡腿）：手工裁剪随仓库提交（`keeper/daemon/hud-icons/`），运行时同步——不依赖 jar
- webui：物品栏显示真实 MC 图标网格；生命/饥饿按 MC ceil 逻辑渲染 9×9 HUD 图标；维度中文名

### 5. 工作流面板（GitHub Actions 风格）

- `tower_manager._workflow_state()`：从 `executor.plan` / `outline` 提取步骤与状态（done/running/pending）
- webui：时间线竖线连接 + spinner 旋转 + 绿色 ✓ + 流动进度条

### 6. 配置容错（clone 后首次使用）

- `game_dir` 缺失 / `tower.json` 不存在时**不再 500 崩溃**：`_ensure_brain` 捕获 RuntimeError 记入 `config_error`，`status()` 返回降级状态
- webui 顶部显示「⚠ 配置未完成」引导横幅；connect/state/chat/agent 全接口容错

---

## 验证

- ✅ **空配置场景**（clone 后 game_dir 未填）：webui 正常打开显示引导横幅，全部接口不崩溃
- ✅ **真机连接**：daemon → Brain → Tower mod（protocol=1）正常握手
- ✅ **模型管理**：对话/视觉角色热切换（设置 vision→跟随对话 验证通过）
- ✅ **工作流**：多步骤 plan 渲染 done/running/pending + spinner/连接线/流动进度条
- ✅ **MC 图标**：物品网格 + 9×9 HUD 心/鸡腿正确显示
- ✅ **语法**：全部改动 Python `py_compile` 通过；webui `npm run build` 通过

## 涉及文件（新增为主）

- `keeper/`（新增）：daemon / webui（Vue 源码 + dist 打包产物）/ cli / settings / mc_icons / agent / launcher / llm
- `brain/llm.py`、`brain/brain.py`、`brain/tower_client.py`（视觉角色、`_manual_disconnect`、`_recv_lock`）
- `.gitignore`（keeper 缓存 / `mc-icons/` 运行时生成物 / `settings.json` 忽略）
- `wallpaper/bg.png`（软件默认壁纸）
- 文档：`开发目录/改造方案-Tower大脑嵌入Keeper前后端.md`、`开发目录/开发日志.md`

## 部署说明（clone 后）

1. `pip install -r brain/requirements.txt`（fastapi/uvicorn/httpx/pywebview 等）
2. `brain/config.yaml` 填 `connection.game_dir`（指向含 Tower mod 的游戏版本目录）+ `brain/api_key.json` 填 key
3. `python -m keeper.cli run-daemon --auto-connect --gui`（或 `run-daemon` 仅服务）
4. 打开 `http://127.0.0.1:8090/`（默认端口；`TOWER_DAEMON_HOST/PORT` 可覆盖）

> 机器特定路径（`game_dir`）不入仓库：提交版为占位空值，按部署机器填写；`brain/api_key.json`、`settings.json` 已 gitignore。
