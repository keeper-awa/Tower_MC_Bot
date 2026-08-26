# M5.3 视觉管线（look 技能）+ 游戏路径统一重构

## 概述

为 Tower AI 大脑新增 **M5.3 视觉管线**：让 AI 能够"看图"——截图后由视觉模型描述当前场景（环境 / 方块 / 生物 / 建筑），并修复实机验收中发现的 5 个问题；同时将散落各验收脚本的游戏路径硬编码统一收敛到 `brain/config.yaml`（唯一配置源）。

> 纯大脑（Python）侧增强，**不修改前置 mod 及协议**（《最终协议.md》），**不修改 Tower Java 代码**。

---

## 改动内容

### 1. M5.3 视觉管线（look 技能）

- **新增技能 `brain/skills/look.py`**：截图 → 等文件就绪（异步写入稳定）→ Pillow 压缩 800px → 视觉模型描述场景（可指定 `prompt` 关注点）
- **`brain/llm.py`**：新增 `look(image_path, prompt)` 与 `vision_message()`（Pillow thumbnail 800px + JPEG q80 + base64）
- **`brain/skills/_base.py` / `brain/executor.py`**：`SkillContext` / `PlanExecutor` 注入 `llm`（技能可直接调视觉模型）
- **`brain/brain.py`**：`FakeLLM.look()`（dry-run 零花费）；LLM 把技能名当顶层工具调用时的兜底自动执行

**效果**：玩家在游戏里发「看看周围环境」→ LLM 排 plan → look 技能截图 → 视觉模型描述画面 → 聊天框返回描述。

### 2. 实机验收修复（5 个 bug）

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| 1 | 主循环无限「循环异常」刷屏 | `config.yaml game_dir` 残留旧路径，`_connect` 读不到 `tower.json` | 指向实际游戏路径 |
| 2 | dev 模式读不到 API key | key 文件放错目录 | 明确唯一位置 `brain/api_key.json` |
| 3 | vision 返回空响应（`finish=length text_len=0`） | 视觉模型是推理型，`reasoning_content` 占满 `max_tokens` | 单独 `vision_max_tokens: 4096` |
| 4 | 游戏聊天框不显示 AI 回复 | MC 聊天消息 ≤256 字符硬限制 | `do_chat` 发送前截断 |
| 5 | LLM 直调技能名（如 `look {}`）无响应 | 顶层只认 plan/outline 工具 | `_handle_chat` 技能名兜底自动执行 |

### 3. 游戏路径统一重构

- **新增 `tools/ai_client_example/_game_dir.py`**：统一从 `brain/config.yaml` 的 `connection.game_dir` 解析绝对路径
- **9 个验收/测试脚本 + `brain/tower_client.py`**：`--game-dir` 默认值不再各自硬编码机器路径，缺省走统一解析
- **`brain/config.yaml`**：`game_dir` 提交为**占位空值**（机器特定路径不进入仓库，按部署机器填写）
- **文档同步**：`开发指南.md` / `Tower协议.md` / tower-verify `SKILL.md` 更新为实际路径

---

## 验证

- ✅ **dry-run 闭环**：连接 Tower → 注入消息 → 排计划 → 执行 → 汇报（零 API 花费）
- ✅ **真机验收**：玩家发「看看周围环境」→ look 技能 → 视觉模型描述画面 → 游戏聊天框显示
- ✅ **技能库加载**：6 个（craft_items / cross_water / look / make_crafting_table / mine_wood / wait）
- ✅ **路径解析**：`_game_dir.default_game_dir()` 返回绝对路径（`is_absolute()=True`）
- ✅ **语法**：全部改动 Python 文件 `py_compile` 通过

## 涉及文件

- `brain/skills/look.py`（新增）
- `brain/llm.py`、`brain/brain.py`、`brain/executor.py`、`brain/skills/_base.py`、`brain/tools.py`、`brain/config.yaml`
- `tools/ai_client_example/_game_dir.py`（新增）及 9 个测试脚本、`brain/tower_client.py`
- 文档：`开发指南.md`、`Tower协议.md`、`.claude/skills/tower-verify/SKILL.md`
