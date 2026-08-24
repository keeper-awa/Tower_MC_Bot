package com.tower.control;

import com.google.gson.JsonObject;
import com.tower.state.PlayerState;
import net.minecraft.client.player.LocalPlayer;

/**
 * get_state（协议 §5.5）：Tower 原生构建完整快照（前置快照 + inventory/xp/effects/biome）。
 */
public final class GetStateAction extends NativeAction {
    @Override
    protected JsonObject execute(LocalPlayer player, ActionContext ctx, JsonObject params) {
        return PlayerState.snapshot(player);
    }
}
