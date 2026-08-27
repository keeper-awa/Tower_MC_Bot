"""系统提示词与消息组装（LLM 决策用）。"""
from __future__ import annotations

from ..mc.actions import ACTION_CATEGORY

# 动作 -> 参数说明（供提示词展示，粗粒度即可）
_ACTION_HINTS: dict[str, str] = {
    "move": '{"forward":0..1,"backward":0..1,"left":0..1,"right":0..1}（全量覆盖四轴）',
    "jump": '{"value":bool}',
    "jump_once": '{"ticks":1..20}',
    "look_at": '{"x":..,"y":..,"z":..} 或 {"entity":<id>} 或 {"unlock":true}/{}',
    "sneak/sprint/swim/fly/fall_fly": '{"value":bool}',
    "attack": '{"mode":"once|hold|release"}',
    "use_item": '{"slot":0..8}',
    "interact_block": '{"x":..,"y":..,"z":..,"face":"up|down|north|south|east|west","slot":0..8}',
    "interact_entity": '{"id":<实体id>,"slot":0..8}',
    "drop": '{"slot":0..40,"count":1|-1}',
    "hotbar": '{"slot":0..8}',
    "equip": '{"slot":0..35,"armor":"head|chest|legs|feet"}',
    "move_item": '{"from":0..40,"to":0..40}',
    "craft": '{"recipe":"minecraft:planks","shift":bool}',
    "chat": '{"message":"文本"}',
    "get_state": "{}",
    "set_push": '{"pos":bool}',
}


def _actions_block() -> str:
    lines = []
    for name in sorted(ACTION_CATEGORY):
        hint = _ACTION_HINTS.get(name, "")
        lines.append(f"- `{name}`{(': ' + hint) if hint else ''}")
    return "\n".join(lines)


def system_prompt() -> str:
    """系统提示词：说明角色、可用动作、输出格式。"""
    return f"""你是 Minecraft 游戏内的一位 AI 玩家。你通过一个控制接口操作玩家（移动/视角/攻击/背包/聊天等），目标是完成用户下达的任务。

你只能看到有限观测：玩家自身状态（位置/朝向/生命/饥饿/模式/维度/世界时间）以及事件（受伤/死亡/重生/聊天/挖掘进度）。你看不到方块与实体列表，因此要基于位置与朝向谨慎行动。

可用动作及参数：
{_actions_block()}

输出规则（严格遵守）：
1. 只输出一个 JSON 对象，不要多余文字、不要 markdown 代码块：
   {{"think": "简短理由（对玩家可见）", "action": "<动作名>", "params": {{...}}}}
2. 若当前无需任何动作（如正在等待/观察），省略 action 字段：
   {{"think": "等待观察"}}
3. params 必须与上表字段一致；不要发明不存在的字段或动作。
4. 持续动作（move/jump/look_at 等）会保持到再次下发；需要停止时显式发全零 move 或 unlock。
5. 注意限速：chat 需间隔 ≥800ms；持续类动作 20 次/秒。不要连发相同动作。
6. 面对危险（生命低/受伤害）优先自保：停止移动、逃离。
7. 每次只执行一个动作，等待其响应/事件后再决定下一步。
"""


def build_messages(
    system: str,
    observation: str,
    history: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """组装请求消息：system + history + 当前观测。"""
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for msg in history or []:
        messages.append(msg)
    messages.append({"role": "user", "content": observation})
    return messages
