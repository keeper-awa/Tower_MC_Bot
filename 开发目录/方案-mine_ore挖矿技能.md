# mine_ore 挖矿技能设计

> 日期：2026-08-28 · 状态：待实现（craft_chain 已先行完成，本技能为下一步） · 依据：功能列表「生存知识技能 v1.1 优先做」、开发日志遗留「mine_ore 挖矿技能（圆石/铁矿/钻石）」、keeper-awa Issue #1

## 目标

新增确定性技能 `mine_ore`：**挖圆石 / 铁矿石 / 钻石矿石** 等矿物方块，材料获取能力（采集 → 冶炼 → 装备升级的基础），也是 `craft_chain` 合成链的**缺料收集依赖**（接入后 craft_chain 可自动收圆石/铁锭）。

**调用形式**（LLM 排 plan 引用）：
```
mine_ore {ore: "minecraft:iron_ore"}        # 挖指定矿（可省略 = 挖石头/圆石）
mine_ore {x, y, z}                          # 扫描坐标直接指定矿位置（环境预扫描提供）
```

## 设计要点

### 1. 找矿逻辑（复用 mine_wood 模式 + search_find）

- **矿物识别**：按 id 判断目标
  - 圆石 `minecraft:cobblestone`（挖石头 `stone` 得到）
  - 矿石 `_ore` 结尾：`iron_ore` / `coal_ore` / `copper_ore` / `gold_ore` / `diamond_ore` / `redstone_ore` / `lapis_ore`
  - 深板岩变体：`deepslate_iron_ore` 等（深板岩层）
- **get_blocks 找最近目标**（半径 16，max 512）：mine_wood 同款 `_find` 逻辑
- **search_find 主动搜索**：原地没有 → 逐段探索（mine_wood 已实现，直接复用）
- **坐标参数优先**：`args.x/y/z` 指定时先确认该处仍是目标矿，否则回退自找（同 mine_wood）

### 2. 工具切换（关键：镐子等级）

**MC 规则**：不同矿石需要不同等级镐子，等级不够**挖不掉 / 不掉落**：

| 目标 | 最低镐等级 |
|---|---|
| 圆石/石头（cobblestone） | 木镐 wooden_pickaxe |
| 煤矿石 / 铁矿石 / 铜矿石 | 石镐 stone_pickaxe |
| 金 / 钻石 / 红石 / 青金石 / 绿宝石 | 铁镐 iron_pickaxe |
| 深板岩钻石矿 | 铁镐 |

- `_switch_pickaxe(ctx, state, required)`：按目标矿等级要求选镐
  - 背包/快捷栏找对应等级镐 → 切到快捷栏（同 mine_wood `_switch_axe` 模式）
  - **等级不够时**：不硬挖，返回「失败：需要石镐/铁镐」→ LLM 可先排 craft_items 造镐 → 再挖
  - 没有镐但有足够木头：可提示「先做木镐」（后续 craft_chain 统一处理，v1 仅报错）

### 3. 挖掘（复用 mine_wood `_mine_block`）

- `_goto`：走到矿方块旁（move_to + wait path_reached，同 mine_wood）
- `look_at` 对准矿方块中心（+0.5，**必须给中心**否则射线摆动挖掘进度涨不满——mine_wood 踩过的坑）
- `attack hold` → 等 `mine_done` → `attack release`（同 mine_wood）
- 安全：`ctx.checkpoint()` 每块前调用（低血/岩浆危险中断）
- **矿往往在脚下/侧面/头顶**：`_in_reach` 允许纵向范围更宽（mine_wood 水平 ≤3.5、纵向 ≤3）

### 4. 拾取 + 验证（复用 mine_wood）

- 挖完 `ctx.pickup_nearby()` 拾取引导
- `poll` 轮询验证：该矿掉落物入背包（矿石本身 or 圆石）数量增加（mine_wood 同款）
- 返回 `完成：挖到 X 个铁矿，背包 X → Y` / `失败：...`

## 复用清单

| 组件 | 来源 |
|---|---|
| `_find` / `_goto` / `_in_reach` / `_mine_block` | mine_wood.py 同款逻辑 |
| `search_find` / `pickup_nearby` / `checkpoint` | _base.py SkillContext 现成 |
| `find_slot` / `count_items` / `player_pos` / `poll` | _util.py 现成 |
| 工具切换 | mine_wood `_switch_axe` → 改成 `_switch_pickaxe`（等级判定） |

## 新增文件

- `brain/skills/mine_ore.py`（唯一新文件，`_` 前缀的复用不新增）
- 技能描述注入 system prompt：`mine_ore`：挖矿（圆石/铁/金/钻石等）；`ore=目标矿id（可省=挖圆石）`；等级不够自动报错提示

## 验收标准

1. **dry-run / 实机**：挖圆石（石头）成功，背包圆石增加
2. **铁矿**：有石镐 → 挖 iron_ore 成功；无石镐 → 明确报「需要石镐」
3. **search_find**：原地无矿 → 主动搜索到矿并挖到
4. **坐标参数**：指定坐标是矿 → 直接挖；指定处无矿 → 回退自找
5. **安全**：挖矿中低血/岩浆 → 正常中断不崩
6. 语法 `py_compile` 通过；不影响既有 6 技能

## 后续（依赖本技能）

- `craft_chain` 合成链（配方代码化：熔炉=8圆石+工作台；工具进度链 木→石→铁→钻石）
- 熔炉/铁装备任务端到端实机复测（开发日志遗留）
