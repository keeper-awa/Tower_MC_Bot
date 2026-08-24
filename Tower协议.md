# Tower 协议 v1（智能体协议）

> 版本：1.0（**定稿**，实施严格按此实现）
> 配套文档：《开发目录\开发指南.md》《开发目录\功能列表.md》
> 前置协议（唯一事实来源）：`前置mod协议\最终协议.md`（v2，不改动）

---

## 1. 概述

| 项 | 值 |
|---|---|
| 传输 | WebSocket（服务端 = Tower mod，客户端 = AI 大脑进程） |
| 地址 | `ws://127.0.0.1:24778/?token=<TOKEN>`（端口可配置） |
| 编码 | UTF-8 JSON，单条消息 ≤ 64KB |
| 会话 | 同一时刻仅一个客户端；新连接挤掉旧连接 |
| 协议版本 | `protocol: 1`（Tower 自身版本，破坏性变更时递增） |
| 执行层 | Tower mod 内置 WS 客户端连接前置 Keyboard mod（`ws://127.0.0.1:24777/`），**转发**执行动作、**中继**事件 |

**分层职责：**
- **转发动作**（§5.1）：由前置 mod 执行，Tower 只做消息透传（响应/错误原样回传）；
- **原生动作**（§5.2–§5.5）：Tower 自己实现——感知、导航、视觉、扩展快照；
- **事件**：前置 9 种事件全中继 + Tower 新增寻路事件（§7）。

---

## 2. 连接与鉴权

1. 客户端携带 token 发起连接（`?token=xxx`），token 见客户端 `config/tower.json`（首启随机生成并打印日志）。
2. 服务端立即返回握手结果：

   ```json
   // 成功
   {"type":"hello","ok":true,"protocol":1,"mod":"tower","version":"0.1.0","prereq":"connected"}
   // 失败（随后断开）
   {"type":"hello","ok":false,"error":"auth_failed"}
   ```

   - `prereq`：前置 mod 连接状态（`connected` / `disconnected`）。大脑可据此判断执行层是否就绪；`disconnected` 时转发动作返回 `501`。
3. **心跳**：客户端每 ≤30s 发送 `{"type":"ping"}`，服务端回 `{"type":"pong"}`；服务端 60s 未收到任何消息则断开。
4. **断线清理**（重要）：大脑连接断开时，Tower 依次执行——
   1. **主动归零**：向前置发送归零序列（`attack {mode:"release"}`、`move {}`、`jump {value:false}`、`sneak {value:false}`、`look_at {}`、`sprint/swim/fly/fall_fly {value:false}`）。原因：前置 v2 §2.5 断线归零**不覆盖 attack hold 的挖掘/攻击保持态**（仅清理 MoveController），必须由 Tower 主动补发，否则 AI 断线后玩家会继续挖矿/攻击；
   2. **断开前置连接**：触发前置自身断线归零兜底（move/jump/sneak/look_at 等二次确认，双保险）；
   3. **Tower 清理自身状态**：取消 move_to 导航、清空缓存；
   4. 大脑重连时，Tower 自动重新连接前置（token 不变，直接重连）；重连完成前转发动作返回 `501`。
5. **重连**：token 不变，直接重连；重连后需重新 `get_state` 同步状态。

---

## 3. 消息类型

与前置协议 v2 §3 相同：`request` / `response` / `event` / `ping` / `pong` / `hello`。

---

## 4. 请求与响应格式

与前置协议 v2 §4 完全一致：
- 请求：`{"id": 1, "action": "move_to", "params": {...}}`
- 成功响应：`{"id": 1, "ok": true, "result": {...}}`
- 失败响应：`{"id": 1, "ok": false, "error": {"code": 304, "message": "..."}}`
- `id` 客户端自增，响应原样带回；动作在游戏主线程按到达顺序串行执行；
- 转发动作的错误码/消息由前置产生，**原样透传**（如 chat 限速 303）。

---

## 5. 动作定义

### 5.1 转发动作（透传前置 mod，全部 19 个）

> 参数与语义以《前置mod协议\最终协议.md》§5 为准，Tower 不校验、不修改，原样转发。

| 动作 | 类别 | 说明 |
|---|---|---|
| move / jump / jump_once | 持续状态 | 全量覆盖四轴；jump_once 自动按压 |
| sneak / sprint / swim / fly / fall_fly | 运动状态 | 302 类错误透传 |
| look_at | 视角锁定 | 坐标/实体/解锁三分支，持续跟踪 |
| attack | 挖掘/攻击 | once/hold/release；mine/mine_done 事件中继 |
| use_item / interact_block / interact_entity | 交互 | 右键物品/方块/实体 |
| drop / hotbar | 物品 | 丢弃/切槽 |
| chat | 聊天 | 限速 800ms，303 透传 |
| equip / move_item / craft | 背包 | v2.1 槽位语义 0-40 |
| set_push | 查询 | 控制前置 pos 事件推送 |

前置未连接时转发动作 → `501`。

### 5.2 感知类（Tower 原生）

#### raycast — 准星射线扫描

```json
{"id": 1, "action": "raycast", "params": {"distance": 10}}
```

| 字段 | 必填 | 范围 | 说明 |
|---|---|---|---|
| distance | 否 | 4..64 | 射线长度，默认 10 |
| through_liquid | 否 | bool | 是否穿过液体，默认 false |

- 从玩家眼睛位置沿准星方向射线，命中方块或实体即返回。
- 响应：
  ```json
  {"hit": {"type": "block", "distance": 5.2,
           "block": {"id": "minecraft:stone", "x": 10, "y": 63, "z": -5, "face": "top"}}}
  // 或 entity
  {"hit": {"type": "entity", "distance": 3.1,
           "entity": {"id": 5, "type": "minecraft:cat", "name": "Whiskers", "health": 10.0}}}
  // 或未命中
  {"hit": {"type": "none", "distance": 10.0}}
  ```
- `face`：命中面（top/bottom/north/south/east/west），供 interact_block 使用。

#### get_blocks — 周围方块列表

```json
{"id": 2, "action": "get_blocks", "params": {"radius": 8}}
```

| 字段 | 必填 | 范围 | 说明 |
|---|---|---|---|
| radius | 否 | 1..16 | 以玩家为中心的立方体半径，默认 8 |
| max | 否 | 1..512 | 非空气方块数量上限，默认 512 |

- 返回玩家周围**非空气**方块（空气是默认值，不列，省消息空间）。
- 响应：
  ```json
  {"blocks": [{"x": 10, "y": 63, "z": -5, "id": "minecraft:log"}, ...],
   "summary": {
     "underfoot": {"id": "minecraft:grass_block", "x": 10, "y": 62, "z": -5},
     "front": {"id": "minecraft:air", "x": 11, "y": 63, "z": -5},
     "head": {"id": "minecraft:air", "x": 10, "y": 64, "z": -5}},
   "truncated": false}
  ```
- `summary`：脚下/面前（视线方向 1 格）/头上三格关键方块，快速决策用；
- 超出 `max` 截断，`truncated: true`（防止超 64KB）。

#### get_entities — 附近实体列表

```json
{"id": 3, "action": "get_entities", "params": {"radius": 16}}
```

| 字段 | 必填 | 范围 | 说明 |
|---|---|---|---|
| radius | 否 | 1..32 | 搜索半径，默认 16 |
| type | 否 | 字符串 | 过滤实体类型（如 `minecraft:zombie`），省略 = 全部 |
| max | 否 | 1..64 | 数量上限，默认 64 |

- 响应：
  ```json
  {"entities": [
     {"id": 12, "type": "minecraft:zombie", "name": "僵尸", "x": 10.0, "y": 63.0, "z": -5.0,
      "health": 20.0, "category": "monster", "hostile": true},
     {"id": 5, "type": "minecraft:cat", "name": "Whiskers", "x": 8.0, "y": 63.0, "z": -3.0,
      "health": 10.0, "category": "creature", "hostile": false}],
   "count": 2}
  ```
- `category` 映射：原版 MobCategory → `monster` / `creature` / `ambient` / `water` / `item` / `player` / `other`；`hostile = (category == "monster")`；
- `name`：实体显示名（自定义名字时显示原名）。

### 5.3 导航类（move_to）

> 寻路用**游戏内原版寻路**（`PathFinder` + `WalkNodeEvaluator`，dummy 僵尸代理，不加入世界），
> 在游戏主线程/serve tick 调度；目的：LLM 只决定「去哪」，不思考「怎么走」。

```json
{"id": 4, "action": "move_to", "params": {"x": 100, "y": 64, "z": -200, "mode": "auto"}}
```

| 字段 | 必填 | 范围 | 说明 |
|---|---|---|---|
| x / y / z | 是 | 世界坐标 | 目标位置（自动找最近的可行走地面） |
| mode | 否 | auto / waypoints | auto = 自动驾驶（默认）；waypoints = 只返回路径 |
| allow_water | 否 | bool | 是否允许路径穿越深水，默认 false |
| sprint | 否 | bool | 直线段是否疾跑，默认 false |
| precision | 否 | 0.5..4.0 | 到达判定距离（格），默认 1.5 |
| cancel | 否 | bool | `true` = 取消当前导航（其余参数忽略） |

- 目标不在当前维度 → `304`；未进世界 → `301`；路径不存在（不可达）→ 即时响应失败或 `path_failed` 事件（见下）。
- 响应：
  - `{"status":"started","mode":"auto","target":{"x":100,"y":64,"z":-200}}`
  - `{"status":"ok","waypoints":[{"x":...,"y":...,"z":...}, ...],"total":42}`（waypoints 模式；路径点 > 128 截断，`truncated: true`）
  - `{"status":"cancelled"}`（cancel）
- **自动驾驶行为**：
  1. 计算路径 → 推送 `path_found`（含路径点，供 AI 观察）；
  2. 沿路径点行走：`look_at` 对准下一节点 + `move` 全速前进；水平距离 < 1 格 → 下一节点；
  3. **自动开门**：原版寻路允许路径穿过**关着的木门**（`DOOR_WOOD_CLOSED` 为可通行路径类型）；导航控制器接近门节点时检测前方/相邻关着的木门 → 直接右键开门（Tower 原生调用游戏交互，不走协议往返；双开门点击任意一扇自动联动）→ 继续行走；
  4. 上 1 格台阶卡住 → 自动 `jump_once`；
  5. 卡住检测（40 tick 位移 < 0.1 格）→ 推送 `path_stuck`，重算路径；连续 2 次失败 → `path_failed {reason:"stuck"}`；
  6. 每 20 tick（≈1s）推送 `path_progress`；
  7. 到达 → 停止 + `path_reached`。
- **手动干预**（决策 D5）：
  - 按**前进键**（W/↑）或**左右键**（A/D）→ **暂停**：完全让出控制（移动归零 + 解除视角锁定），推送 `path_paused`；全部松手 → 恢复导航（重新锁定视角），推送 `path_resumed`；若暂停期间玩家走远（偏离路径 > 16 格）→ 自动重算路径；
  - 按**后退键**（S/↓）→ **取消**导航，推送 `path_cancelled {reason:"manual_backward"}`。
- **取消途径**：`move_to {cancel:true}`（`path_cancelled {reason:"requested"}`）、后退键、大脑断线、`path_failed` 后自动结束。
- **已知限制（v1）**：
  - **铁门/活板门**不处理（原版寻路避开 → 路径绕行或失败）；
  - **梯子/藤蔓攀爬**：v1.1 规划（自定义 NodeEvaluator 标记梯子可通行 + 攀爬控制：贴梯时前进+水平视角）；
  - **不自动搭方块搭桥**；但 AI 建筑能力不受影响——`interact_block` 转发即能放方块，AI 可自行放方块铺路后再 `move_to`（新方块自动纳入寻路）；
  - 路径被水/岩浆截断 → 不可达即失败（`allow_water` 可放行深水，岩浆永远避开）；落水后由 AI 自行决定游泳（swim 动作）。

### 5.4 视觉类（screenshot）

```json
{"id": 5, "action": "screenshot", "params": {}}
```

- 走原版 `Screenshot` 类截取当前画面，保存 PNG 到游戏 `screenshots` 目录（文件名为原版格式 `<世界名>_<时间戳>.png`），**不直接走 WS**（图片远超 64KB）。
- 响应：`{"path":"D:\\整合包\\.minecraft\\screenshots\\world_2026-08-24_14.30.05.png","width":1920,"height":1080}`
- 注：保存为异步完成，大脑读到路径后需带重试等待文件出现（一般 < 2s）；
- 大脑侧自行用 Pillow 压缩（降分辨率 + JPEG）后再发送给视觉模型，本协议不涉及。

### 5.5 查询类

#### get_state — 完整状态快照（Tower 原生构建）

```json
{"id": 6, "action": "get_state", "params": {}}
```

- **Tower 自己构建完整快照**（不依赖前置的 get_state），字段 = v2 快照 + 新增（见 §6）；
- 未进世界 → `301`。

---

## 6. 状态快照（get_state 响应）

```json
{
  "protocol": 1,
  "player": {
    "position": {"x": 100.0, "y": 64.0, "z": -200.0},
    "rotation": {"yaw": 45.2, "pitch": -12.3},
    "motion": {"x": 0.0, "y": -0.05, "z": 0.0},
    "on_ground": true,
    "health": 20.0,
    "food": 20,
    "saturation": 5.0,
    "dimension": "minecraft:overworld",
    "gamemode": "survival",
    "alive": true,
    "selected_slot": 3,
    "abilities": {"flying": false, "fly_allowed": false}
  },
  "inventory": {
    "slots": [
      {"slot": 0, "id": "minecraft:wooden_pickaxe", "count": 1},
      {"slot": 9, "id": "minecraft:oak_log", "count": 16}
    ],
    "armor": [
      {"slot": 36, "id": "minecraft:leather_boots", "count": 1}
    ],
    "offhand": {"slot": 40, "id": "minecraft:torch", "count": 8},
    "held": {"id": "minecraft:wooden_pickaxe", "count": 1}
  },
  "xp": {"level": 5, "progress": 0.3},
  "effects": [{"id": "minecraft:speed", "amplifier": 0, "duration": 200}],
  "world": {"time_of_day": 6000, "biome": "minecraft:plains"}
}
```

| 字段 | 说明 |
|---|---|
| player.* | 与前置 v2 §6 完全一致 |
| inventory.slots | **非空**槽位 0-35（0-8 工具栏、9-35 背包），`{slot,id,count}` |
| inventory.armor | 非空盔甲槽 36-39（脚/腿/胸/头） |
| inventory.offhand | 副手槽 40（非空时出现） |
| inventory.held | 手持物品（主手，非空时出现） |
| xp.level / progress | 经验等级 / 当前等级进度 0..1 |
| effects | 玩家身上的药水效果（非空时出现） |
| world.biome | 当前生物群系 id |

槽位语义与前置 v2.1 `move_item`/`equip` 完全对齐（0-8 / 9-35 / 36-39 / 40）。

---

## 7. 事件推送

统一格式：`{"type": "event", "event": "<名称>", "data": {...}}`

### 7.1 中继事件（前置 mod → Tower → 大脑，data 原样透传）

`player_ready` / `pos`（节流 0.5s）/ `chat` / `damage` / `death` / `respawn` / `game_mode` / `mine` / `mine_done`

### 7.2 Tower 新增事件（寻路）

| event | data | 触发条件 |
|---|---|---|
| `path_found` | `{"mode":"auto","target":{...},"total":42,"waypoints":[{x,y,z}...],"truncated":false}` | 路径计算成功（auto 与 waypoints 均推送） |
| `path_progress` | `{"remaining":35.2,"node_index":7,"node":{"x":...}}` | 自动驾驶中，每 20 tick |
| `path_stuck` | `{"tries":1}` | 卡住，正在重算路径 |
| `path_reached` | `{"x":100,"y":64,"z":-200}` | 到达目标 |
| `path_failed` | `{"reason":"unreachable"\|"stuck"\|"no_path"\|"invalid_target","detail":"..."}` | 寻路失败（不可达/重算仍卡/目标无效） |
| `path_cancelled` | `{"reason":"requested"\|"manual_backward"\|"disconnect"}` | 导航被取消 |
| `path_paused` / `path_resumed` | `{"reason":"manual"}` / `{}` | 手动按键暂停 / 松手恢复 |

---

## 8. 错误码

### 8.1 透传（前置产生，原样回传）

`101-105`、`201`、`301-304`、`401`（见《前置mod协议\最终协议.md》§8）

### 8.2 Tower 新增

| code | 含义 |
|---|---|
| 501 | 前置 mod 未连接（Tower 与前置的 WS 链路断开，转发动作不可用） |

### 8.3 Tower 原生动作使用的现有码

- `301` 未进世界；`304` 目标非法（不在当前维度 / 不可达 / 参数目标冲突）；`102/103` 参数校验；`401` 内部错误。

---

## 9. 速率限制（默认值，配置可调）

| 限制项 | 默认值 |
|---|---|
| 总消息 | 100 msg/s |
| chat | ≥800ms 间隔（前置限速，303 透传） |
| move/jump/look_at 等持续类 | 20 msg/s（前置限速） |
| 转发瞬时动作 | 20 msg/s（前置限速） |
| move_to / raycast / get_blocks / get_entities / screenshot | 20 msg/s |
| path_progress 推送 | 20 tick 节流（≈1s） |

---

## 10. 完整会话示例

```
大脑连接 ws://127.0.0.1:24778/?token=b2c3d4e5...

S→C: {"type":"hello","ok":true,"protocol":1,"mod":"tower","version":"0.1.0","prereq":"connected"}
S→C: {"type":"event","event":"player_ready","data":{}}   ← 中继自前置

C→S: {"id":1,"action":"get_state","params":{}}
S→C: {"id":1,"ok":true,"result":{ ...扩展快照（含 inventory）... }}

C→S: {"id":2,"action":"get_blocks","params":{"radius":8}}
S→C: {"id":2,"ok":true,"result":{"blocks":[...],"summary":{...}}}

C→S: {"id":3,"action":"move_to","params":{"x":100,"y":64,"z":-200}}
S→C: {"id":3,"ok":true,"result":{"status":"started",...}}
S→C: {"type":"event","event":"path_found","data":{"mode":"auto","total":42,"waypoints":[...]}}
S→C: {"type":"event","event":"path_progress","data":{"remaining":35.2,...}}   ← 每 ~1s
S→C: {"type":"event","event":"path_reached","data":{"x":100,"y":64,"z":-200}}

C→S: {"id":4,"action":"move","params":{"forward":1}}    ← 转发动作示例
S→C: {"id":4,"ok":true,"result":{"applied":{...}}}      ← 前置响应透传

C→S: {"id":5,"action":"screenshot","params":{}}
S→C: {"id":5,"ok":true,"result":{"path":"...\\screenshots\\world_...png","width":1920,"height":1080}}

C→S: {"id":6,"action":"chat","params":{"message":"hello"}}
S→C: {"id":6,"ok":true,"result":{"sent":true}}
```

---

## 11. 安全说明

1. **仅监听 127.0.0.1**：只有本机进程能连接；
2. **token 管理**：首启随机生成于 `config/tower.json` 并打印日志；泄露后删配置重启游戏即可重新生成；
3. **断线归零**：大脑断开 → Tower 断开前置连接 → 前置自动归零所有持续输入（复用前置机制），并取消导航；
4. **无作弊能力**：寻路只是走路，无传送/改模式/刷物品；全部动作为合法玩家输入。

---

## 12. 变更记录

> v1.0（草案，2026-08-24）：初始设计。转发前置 v2 全集（19 动作 + 9 事件），原生实现感知（raycast/get_blocks/get_entities）、导航（move_to 自动驾驶，原版 PathFinder）、视觉（screenshot）、扩展快照（inventory/xp/effects/biome）；新增寻路事件 7 种、错误码 501；决策 D5（手动暂停/取消）落实。
>
> v1.0 修订（2026-08-24，用户审阅后）：① 断线清理升级为「主动归零序列 + 断开前置 + 双保险」——读前置源码发现其断线归零不覆盖 attack hold，Tower 主动补发归零；② move_to 支持**自动开木门**（原版寻路本就穿过木门，控制器接近时右键开门）；③ 梯子攀爬列入 v1.1（用户确认）；④ 明确 AI 建筑能力不受影响（interact_block 放方块 + 重寻路）。
>
> **v1.0 定稿（2026-08-24）**：用户审阅通过，按此实现。
