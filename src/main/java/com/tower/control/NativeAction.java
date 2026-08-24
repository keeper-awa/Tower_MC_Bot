package com.tower.control;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.mojang.logging.LogUtils;
import com.tower.net.MessageCodec;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import org.slf4j.Logger;

/**
 * 原生动作基类（协议 §5.2-§5.5）：感知/导航/视觉等 Tower 自行实现的动作。
 *
 * <p>与转发动作不同，原生动作**访问游戏对象**，必须封送游戏主线程执行
 * （{@code Minecraft.getInstance().execute}），按到达顺序串行执行（协议 §4）；
 * 本类统一处理：未进世界 → 301，动作异常 → 401，异步响应（处理器执行完
 * 由主线程写回大脑连接，Netty 写线程安全）。
 *
 * <p>子类实现 {@link #execute}，在主线程执行，可抛 {@link ActionException}。
 */
public abstract class NativeAction implements ActionHandler {
    private static final Logger LOGGER = LogUtils.getLogger();

    @Override
    public final JsonElement handle(ActionContext ctx, JsonObject params) throws ActionException {
        Minecraft.getInstance().execute(() -> {
            try {
                LocalPlayer player = Minecraft.getInstance().player;
                if (player == null) {
                    ctx.channel.writeAndFlush(
                            MessageCodec.errorResponse(ctx.id, 301, "游戏未就绪（未进世界）"));
                    return;
                }
                JsonObject result = execute(player, ctx, params);
                ctx.channel.writeAndFlush(MessageCodec.successResponse(ctx.id, result));
            } catch (ActionException e) {
                ctx.channel.writeAndFlush(MessageCodec.errorResponse(ctx.id, e.code, e.getMessage()));
            } catch (Exception e) {
                LOGGER.error("Tower: 原生动作执行异常（内部错误）", e);
                ctx.channel.writeAndFlush(MessageCodec.errorResponse(ctx.id, MessageCodec.ERR_INTERNAL, "内部错误"));
            }
        });
        return null; // 异步响应
    }

    /** 在主线程执行动作；返回成功响应的 result。 */
    protected abstract JsonObject execute(LocalPlayer player, ActionContext ctx, JsonObject params)
            throws ActionException;

    // ── 参数解析辅助（协议 §8.3：102/103 参数校验）──

    /** 可选整数参数：类型错误或越界 → 103。 */
    protected static int optInt(JsonObject params, String key, int def, int min, int max)
            throws ActionException {
        JsonElement el = params.get(key);
        if (el == null || el.isJsonNull()) {
            return def;
        }
        if (!el.isJsonPrimitive() || !el.getAsJsonPrimitive().isNumber()) {
            throw new ActionException(MessageCodec.ERR_INVALID_PARAM, key + " 必须是数字");
        }
        int v = el.getAsInt();
        if (v < min || v > max) {
            throw new ActionException(MessageCodec.ERR_INVALID_PARAM, key + " 必须在 " + min + ".." + max);
        }
        return v;
    }

    /** 可选布尔参数：类型错误 → 103。 */
    protected static boolean optBool(JsonObject params, String key, boolean def)
            throws ActionException {
        JsonElement el = params.get(key);
        if (el == null || el.isJsonNull()) {
            return def;
        }
        if (!el.isJsonPrimitive() || !el.getAsJsonPrimitive().isBoolean()) {
            throw new ActionException(MessageCodec.ERR_INVALID_PARAM, key + " 必须是布尔值");
        }
        return el.getAsBoolean();
    }

    /** 可选浮点参数：类型错误或越界 → 103。 */
    protected static float optDouble(JsonObject params, String key, double def, double min, double max)
            throws ActionException {
        JsonElement el = params.get(key);
        if (el == null || el.isJsonNull()) {
            return (float) def;
        }
        if (!el.isJsonPrimitive() || !el.getAsJsonPrimitive().isNumber()) {
            throw new ActionException(MessageCodec.ERR_INVALID_PARAM, key + " 必须是数字");
        }
        double v = el.getAsDouble();
        if (v < min || v > max) {
            throw new ActionException(MessageCodec.ERR_INVALID_PARAM, key + " 必须在 " + min + ".." + max);
        }
        return (float) v;
    }
}
