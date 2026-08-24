package com.tower.net;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.tower.control.ActionContext;
import com.tower.control.ActionException;
import com.tower.control.ActionHandler;
import com.tower.control.ActionRegistry;
import com.mojang.logging.LogUtils;
import io.netty.channel.Channel;
import org.slf4j.Logger;

/**
 * 请求路由（《Tower协议.md》§4/§8）：校验 → 查动作注册表 → 执行。
 *
 * <p>线程模型：M1.3 转发动作在 Netty eventLoop 线程执行（纯网络操作，不触碰
 * 游戏对象）；按到达顺序串行发送至前置（前置按序执行，协议 §4）。M2+ 原生
 * 动作需访问游戏对象，届时在处理器内自行封送游戏主线程。
 *
 * <p>校验规则（协议 §4）：未知字段忽略；缺 id / 缺 action → 102；
 * id/action/params 类型错误 → 103；未注册动作 → 101；执行异常 → 401。
 * 转发动作在前置链路断开时 → 501（协议 §8.2）。
 */
public final class Dispatcher {
    private static final Logger LOGGER = LogUtils.getLogger();

    private Dispatcher() {
    }

    /** 分发一条已解析为对象的 request 消息。 */
    public static void dispatch(Channel channel, JsonObject obj) {
        // ── id（必填，数字）──
        JsonElement idEl = obj.get("id");
        if (idEl == null || idEl.isJsonNull()) {
            channel.writeAndFlush(MessageCodec.errorResponse(0, MessageCodec.ERR_MISSING_PARAM, "缺少必填字段 id"));
            return;
        }
        if (!idEl.isJsonPrimitive() || !idEl.getAsJsonPrimitive().isNumber()) {
            channel.writeAndFlush(MessageCodec.errorResponse(0, MessageCodec.ERR_INVALID_PARAM, "id 必须是数字"));
            return;
        }
        long id = idEl.getAsLong();

        // ── action（必填，字符串）──
        JsonElement actEl = obj.get("action");
        if (actEl == null || actEl.isJsonNull()) {
            channel.writeAndFlush(MessageCodec.errorResponse(id, MessageCodec.ERR_MISSING_PARAM, "缺少必填字段 action"));
            return;
        }
        if (!actEl.isJsonPrimitive() || !actEl.getAsJsonPrimitive().isString()) {
            channel.writeAndFlush(MessageCodec.errorResponse(id, MessageCodec.ERR_INVALID_PARAM, "action 必须是字符串"));
            return;
        }
        String action = actEl.getAsString();

        // ── params（可选，必须是对象）──
        JsonElement paramEl = obj.get("params");
        JsonObject params;
        if (paramEl == null || paramEl.isJsonNull()) {
            params = new JsonObject();
        } else if (paramEl.isJsonObject()) {
            params = paramEl.getAsJsonObject();
        } else {
            channel.writeAndFlush(MessageCodec.errorResponse(id, MessageCodec.ERR_INVALID_PARAM, "params 必须是对象"));
            return;
        }

        // ── 动作注册表 ──
        ActionHandler handler = ActionRegistry.get(action);
        if (handler == null) {
            channel.writeAndFlush(MessageCodec.errorResponse(id, MessageCodec.ERR_UNKNOWN_ACTION, "未知动作: " + action));
            return;
        }

        // ── 执行（转发动作纯网络操作，Netty 线程安全；原生动作在处理器内自行封送）──
        executeAction(channel, id, action, handler, params);
    }

    private static void executeAction(Channel channel, long id, String action, ActionHandler handler, JsonObject params) {
        try {
            JsonElement result = handler.handle(new ActionContext(channel, id), params);
            if (result != null) {
                channel.writeAndFlush(MessageCodec.successResponse(id, result));
            }
            // null = 异步响应：处理器已启动异步流程，响应由其稍后经回调发送
        } catch (ActionException e) {
            channel.writeAndFlush(MessageCodec.errorResponse(id, e.code, e.getMessage()));
        } catch (Exception e) {
            LOGGER.error("Tower: 动作 {} 执行异常（内部错误）", action, e);
            channel.writeAndFlush(MessageCodec.errorResponse(id, MessageCodec.ERR_INTERNAL, "内部错误"));
        }
    }
}
