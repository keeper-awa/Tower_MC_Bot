package com.tower.relay;

import com.google.gson.JsonObject;
import com.tower.net.AuthManager;
import io.netty.channel.Channel;

/**
 * 事件中继（《Tower协议.md》§7）：前置事件 → 大脑连接，data 原样透传。
 *
 * <p>前置 9 种事件（player_ready/pos/chat/damage/death/respawn/game_mode/mine/mine_done）
 * 全部经此转发；大脑未连接时事件丢弃，仅缓存 player_ready——
 * 大脑断线重连后由 {@link #replayPlayerReady} 重放（协议 §10 会话流程）。
 *
 * <p>线程安全：volatile 读 + Netty channel 写线程安全，任意线程可调用。
 */
public final class EventRelay {
    /** 最近一次来自前置的 player_ready（原样缓存，供大脑重连重放）。 */
    private static volatile JsonObject cachedPlayerReady;

    private EventRelay() {
    }

    /** 中继前置事件到当前大脑连接（大脑未连接时仅缓存 player_ready）。 */
    public static void relay(JsonObject msg) {
        String event = msg.get("event") == null ? "" : msg.get("event").getAsString();
        if ("player_ready".equals(event)) {
            cachedPlayerReady = msg;
        }
        Channel brain = AuthManager.getCurrent();
        if (brain != null) {
            brain.writeAndFlush(msg.toString());
        }
    }

    /** 大脑连接建立时重放缓存的 player_ready（若前置链路在且曾收到过）。 */
    public static void replayPlayerReady(Channel brain) {
        JsonObject m = cachedPlayerReady;
        if (m != null) {
            brain.writeAndFlush(m.toString());
        }
    }

    /** 前置链路断开后清除缓存（重连后前置会重新推送 player_ready）。 */
    public static void clear() {
        cachedPlayerReady = null;
    }
}
