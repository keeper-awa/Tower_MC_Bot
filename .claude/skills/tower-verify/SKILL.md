---
name: tower-verify
description: Tower mod（Minecraft AI 大脑协议桥）的构建、部署、实机验收与诊断流程。每次改代码后的完整验收闭环：编译→部署 jar→等游戏重启→进世界→跑验收脚本→查日志→更新文档。
---

# Tower 开发验收流程

Tower = Minecraft 1.20.1 NeoForge 客户端 mod（AI 协议桥：Python 大脑 ⇄ Tower WS :24778 ⇄ 前置 Keyboard mod WS :24777）。里程碑：M1 工程/转发（✅）M2 感知（✅）M3 寻路（✅）M4 截图（✅）M5 大脑（🚧）M6 人设 M7 打包。详见 `开发目录\` 下《开发日志.md》《功能列表.md》。

## 1. 构建与部署

```bash
cd d:/ClaudeCode/Tower_1.20.1
./gradlew build -x test --console=plain    # 编译（~2s，配置缓存）
cp build/libs/tower-0.1.0.jar "D:/整合包/.minecraft/versions/1.20.1-NeoForge_47.1.106/mods/tower-0.1.0.jar"
```

- **改 mod 代码后必须重启游戏才能生效**（Forge 不热加载）；改 tools/brain 的 Python 代码无需重启
- 依赖网络慢时 Gradle 已配腾讯镜像；pip 用 `-i https://mirrors.cloud.tencent.com/pypi/simple`

## 2. 等待游戏重启（关键：进程创建时间判断）

轮询端口会**错过旧→新实例切换**（旧退出+新绑定发生在 3s 轮询间隔内）。正确方式：

```bash
PID=$(netstat -ano | grep ":24778.*LISTENING" | awk '{print $NF}' | head -1)
CREATED=$(wmic process where "ProcessId=$PID" get CreationDate 2>/dev/null | grep -o "[0-9]\{14\}" | head -1)
# CREATED 形如 20260824134100（2026-08-24 13:41:00）；与部署时间戳比较判断是否为新实例
```

## 3. 等进世界

```bash
cd tools/ai_client_example && python wait_in_world.py   # 收到 player_ready 即已进世界
```

## 4. 验收脚本矩阵（tools/ai_client_example/）

| 里程碑 | 脚本 | 说明 |
|---|---|---|
| M1.2 WS 服务器 | `ws_smoke_test.py` | hello/prereq/心跳/挤连接/错 token（`--test-idle` 追加 60s 空闲用例） |
| M1.3 转发链路 | `forward_test.py` `zeroing_test.py` | 动作转发/错误透传/pos 事件中继；断线归零行为 |
| M2 感知 | `m2_perception_test.py` | 快照 17 字段/raycast/get_blocks/get_entities/参数校验 |
| M3 寻路 | `m3_nav_test.py` | waypoints/auto 到达/cancel/304/校验（需开阔平地） |
| M4 截图 | `m4_screenshot_test.py` | 独立文件夹/PNG 校验/溢出清理 |
| M5 大脑 | `brain/brain.py --dry-run` 后真实运行 | 闭环验证（零花费）；真实运行需 config.yaml 填 key |

运行前先 `python wait_in_world.py`；脚本自带 `connect_until_ready`（等待前置链路恢复——大脑断开会触发协议 §2.4 归零+断前置）。

## 5. 诊断手册（踩坑速查）

| 现象 | 根因/检查 |
|---|---|
| 游戏日志 | `D:/整合包/.minecraft/versions/1.20.1-NeoForge_47.1.106/logs/latest.log`（grep `Tower:\|Keyboard:`） |
| 测试脚本无限卡死 | 事件接收必须用**迭代式总超时**（`tower_client.py` 的 recv 已修）；不要用递归 recv（事件流会重置超时） |
| move_to 立即"到达" | `PathFinder.findPath` 第 5 参数 depth=**曼哈顿到达半径**（传 1，勿传大值） |
| 截图连拍只存 1 张 | 时间戳精度到秒会重名——文件名带自增序号 |
| 前置一转发就断链 | 客户端帧必须置**掩码位**（0x80，服务端侧不用） |
| 读截图文件读到坏内容 | 异步写入——等文件**大小稳定**再读 |
| LLM 报 `role 'tool' must follow 'tool_calls'` | 回喂序列缺 assistant 消息（含 tool_calls）——LLM.chat 返回 `assistant_msg` 原样放回 |
| 大脑频繁断线重连 | LLM 错误误触连接重置——LLMError 在 decide 内捕获，勿置 client=None |
| 控制台中文乱码 | Windows GBK——所有 Python 脚本 `sys.stdout.reconfigure(encoding="utf-8")` |
| 1.20.1 API 名称 | 反编译缓存：`~/.gradle/caches/neoformruntime/intermediate_results/decompile_*_output.jar`（MCP 中间名）+ `build/moddev/artifacts/forge-*-merged.jar`（javap 查官方名） |

## 6. 文档更新惯例（验收通过后必做）

- `开发目录/开发日志.md`：新增会话段落（完成内容/验收结果/抓到的 bug），更新"会话总结"与"下次任务清单"
- `开发目录/功能列表.md`：对应功能/里程碑状态改 ✅（🚧 进行中 / ⏳ 待规划）
- 错误码/协议变更需同步 `Tower协议.md`（v1.0 定稿，破坏性变更才动）

## 7. 大脑运行

```bash
cd brain
python brain.py --dry-run    # 零花费闭环验证（FakeLLM）
python brain.py              # 真实模式（config.yaml 需 api.api_key）
```

- 模型：`deepseek-v4-flash`（决策）；视觉 `deepseek-v4-flash-vision-exp`（M5.3 起）
- 决策格式：OpenAI 工具调用；费用统计在日志（LLM 用量行）
- 大脑是交互式进程：验收用 `timeout 90 python -u brain.py` 观察窗
