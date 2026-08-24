package com.tower.control;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.tower.nav.NavController;
import com.tower.net.MessageCodec;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;

/**
 * move_to（协议 §5.3）：原版寻路导航。
 *
 * <p>mode：auto = 自动驾驶（默认）；waypoints = 只返回路径。
 * cancel=true 取消当前导航（其余参数忽略）。
 * 客户端单维度：目标非法（不可达）→ 304。
 */
public final class MoveToAction extends NativeAction {
    private static final float PRECISION_MIN = 0.5F;
    private static final float PRECISION_MAX = 4.0F;

    @Override
    protected JsonObject execute(LocalPlayer player, ActionContext ctx, JsonObject params)
            throws ActionException {
        // cancel 优先（协议 §5.3：cancel 时其余参数忽略）
        if (optBool(params, "cancel", false)) {
            NavController.cancel("requested");
            JsonObject r = new JsonObject();
            r.addProperty("status", "cancelled");
            return r;
        }

        // x/y/z 必填数字
        JsonElement xe = params.get("x");
        JsonElement ye = params.get("y");
        JsonElement ze = params.get("z");
        if (xe == null || ye == null || ze == null || xe.isJsonNull() || ye.isJsonNull() || ze.isJsonNull()) {
            throw new ActionException(MessageCodec.ERR_MISSING_PARAM, "缺少必填字段 x/y/z");
        }
        if (!isNumber(xe) || !isNumber(ye) || !isNumber(ze)) {
            throw new ActionException(MessageCodec.ERR_INVALID_PARAM, "x/y/z 必须是数字");
        }
        BlockPos target = new BlockPos(xe.getAsInt(), ye.getAsInt(), ze.getAsInt());

        // mode / allow_water / sprint / precision
        String mode = "auto";
        JsonElement modeEl = params.get("mode");
        if (modeEl != null && !modeEl.isJsonNull()) {
            if (!modeEl.isJsonPrimitive() || !modeEl.getAsJsonPrimitive().isString()) {
                throw new ActionException(MessageCodec.ERR_INVALID_PARAM, "mode 必须是字符串");
            }
            mode = modeEl.getAsString();
            if (!"auto".equals(mode) && !"waypoints".equals(mode)) {
                throw new ActionException(MessageCodec.ERR_INVALID_PARAM, "mode 必须是 auto 或 waypoints");
            }
        }
        boolean allowWater = optBool(params, "allow_water", false);
        boolean sprint = optBool(params, "sprint", false);
        float precision = optDouble(params, "precision", 1.5, PRECISION_MIN, PRECISION_MAX);

        return NavController.start(target, mode, allowWater, sprint, precision);
    }

    private static boolean isNumber(JsonElement el) {
        return el.isJsonPrimitive() && el.getAsJsonPrimitive().isNumber();
    }
}
